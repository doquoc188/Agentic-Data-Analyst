"""Persistent observability checks with fake models and temporary directories."""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from langchain.messages import AIMessage, ToolMessage

from app.agent import AgentTrace, ModelIntegrationError, ToolInvocationError, run_agent
from app.tools import calculator, execute_sql
from app.trace import PREVIEW_CHARS, PREVIEW_ROWS, main, read_trace, result_preview, safe_trace_value
from eval.runner import evaluate_case
from eval.rescore import rescore_report
from test_agent import FakeLlm, tool_request


class TraceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runs = Path(directory.name) / "runs"
        tracing = patch("app.trace.RUNS_DIR", self.runs)
        tracing.start()
        self.addCleanup(tracing.stop)

    def run_fake(self, responses, question="What is 2 + 3?", **kwargs):
        llm = FakeLlm(responses)
        llm.model = "fake-local-model"
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=llm):
            answer = run_agent(question, trace=trace, verbose=False, **kwargs)
        return answer, trace, read_trace(Path(trace.trace_path)), llm

    def test_success_persists_unique_json_with_times_chronology_and_ids(self):
        answer, trace, saved, llm = self.run_fake([
            tool_request("calculator", {"operation": "add", "a": 2, "b": 3}, "add"),
            AIMessage(content="5"),
        ])
        self.assertEqual(answer, "5")
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["run_id"], trace.run_id)
        self.assertEqual(saved["question"], "What is 2 + 3?")
        self.assertEqual(saved["source"], "other")
        self.assertEqual(saved["status"], "success")
        self.assertEqual(saved["termination_reason"], "success")
        self.assertEqual(saved["final_answer"], "5")
        self.assertEqual(saved["model"], {"provider": "google_genai", "name": "fake-local-model"})
        self.assertEqual([turn["turn"] for turn in saved["turns"]], [1, 2])
        self.assertEqual([turn["response_type"] for turn in saved["turns"]], ["tool_calls", "text"])
        call = saved["tool_calls"][0]
        self.assertEqual(call["tool_call_id"], "add")
        self.assertEqual(call["model_turn"], 1)
        self.assertEqual(call["status"], "success")
        self.assertEqual(call["result"]["text"], "5.0")
        timestamps = [saved["started_at"], saved["turns"][0]["started_at"],
                      saved["turns"][0]["finished_at"], call["started_at"], call["finished_at"],
                      saved["turns"][1]["started_at"], saved["turns"][1]["finished_at"], saved["finished_at"]]
        parsed = [datetime.fromisoformat(value) for value in timestamps]
        self.assertTrue(all(value.utcoffset() is not None for value in parsed))
        self.assertEqual(parsed, sorted(parsed))
        for event in [saved, *saved["turns"], call]:
            self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertEqual(saved["metrics"]["model_turn_count"], 2)
        self.assertEqual(saved["metrics"]["successful_tool_calls"], 1)
        self.assertEqual(saved, json.loads(json.dumps(saved)))
        self.assertFalse(list(self.runs.glob("*.tmp")))
        self.assertIsInstance(llm.bound.message_history[1][-1], ToolMessage)
        _, other, _, _ = self.run_fake([AIMessage(content="Another answer.")])
        self.assertNotEqual(trace.run_id, other.run_id)
        self.assertEqual(len(list(self.runs.glob("*.json"))), 2)

    def test_validation_then_corrected_sql_preserves_observations_and_sql_ids(self):
        query = "SELECT COUNT(*) FROM orders"
        result = "COLUMNS:\ncount\n\nROWS:\n300\n\nRows returned: 1"
        with patch.object(execute_sql, "func", return_value=result) as tool_body:
            answer, _, saved, llm = self.run_fake([
                tool_request("execute_sql", {"queries": [query]}, "invalid"),
                tool_request("execute_sql", {"query": query}, "corrected"),
                AIMessage(content="There are 300 orders."),
            ])
        tool_body.assert_called_once_with(query=query)
        self.assertEqual(answer, "There are 300 orders.")
        self.assertEqual(saved["status"], "success")
        self.assertEqual([call["status"] for call in saved["tool_calls"]], ["validation_error", "success"])
        self.assertEqual([call["tool_call_id"] for call in saved["sql_calls"]], ["invalid", "corrected"])
        self.assertIsNone(saved["sql_calls"][0]["query"])
        self.assertEqual(saved["sql_calls"][1]["query"], query)
        self.assertEqual(saved["sql_calls"][1]["row_count"], 1)
        self.assertEqual(saved["metrics"]["tool_validation_errors"], 1)
        self.assertEqual(saved["metrics"]["failed_tool_calls"], 1)
        self.assertEqual(saved["metrics"]["sql_execution_errors"], 0)
        self.assertEqual(llm.bound.message_history[1][-1].tool_call_id, "invalid")
        self.assertEqual(llm.bound.message_history[2][-1].tool_call_id, "corrected")

    def test_multiple_tools_in_one_turn_remain_chronological(self):
        calls = [tool_request("calculator", {"operation": "add", "a": 1, "b": 2}, "first"),
                 tool_request("calculator", {"operation": "multiply", "a": 2, "b": 3}, "second")]
        _, _, saved, _ = self.run_fake([
            AIMessage(content="", tool_calls=calls[0].tool_calls + calls[1].tool_calls),
            AIMessage(content="3 and 6"),
        ])
        self.assertEqual(saved["turns"][0]["tool_call_count"], 2)
        self.assertEqual([call["tool_call_id"] for call in saved["tool_calls"]], ["first", "second"])
        self.assertEqual([call["model_turn"] for call in saved["tool_calls"]], [1, 1])

    def test_failed_model_invocation_is_persisted_without_raw_exception(self):
        llm = FakeLlm([])
        error = RuntimeError("Authorization: Bearer fake-secret-provider-body")
        error.status = "RESOURCE_EXHAUSTED"
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=llm), \
             patch.object(llm.bound, "invoke", side_effect=error):
            with self.assertRaises(ModelIntegrationError):
                run_agent("A provider failure.", trace=trace, verbose=False)
        saved = read_trace(Path(trace.trace_path))
        self.assertEqual(saved["status"], "model_error")
        self.assertEqual(saved["termination_reason"], "model_integration_error")
        self.assertEqual(saved["turns"][0]["status"], "error")
        self.assertEqual(saved["turns"][0]["error"]["provider_category"], "RESOURCE_EXHAUSTED")
        self.assertEqual(saved["metrics"]["model_errors"], 1)
        self.assertGreaterEqual(saved["turns"][0]["duration_ms"], 0)
        self.assertNotIn("fake-secret-provider-body", json.dumps(saved))

    def test_setup_failure_is_persisted_without_a_fabricated_model_turn(self):
        trace = AgentTrace()
        with patch("app.agent.get_llm", side_effect=RuntimeError("raw setup internals")):
            with self.assertRaises(ModelIntegrationError):
                run_agent("Setup failure.", trace=trace, verbose=False)
        saved = read_trace(Path(trace.trace_path))
        self.assertEqual(saved["turns"], [])
        self.assertEqual(saved["model_error"]["phase"], "model_setup")
        self.assertEqual(saved["metrics"]["model_errors"], 1)
        self.assertNotIn("raw setup internals", json.dumps(saved))

    def test_iteration_limit_run_is_also_persisted(self):
        llm = FakeLlm([tool_request("execute_sql", {"queries": ["SELECT 1"]}, f"invalid-{i}")
                       for i in range(8)])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=llm):
            with self.assertRaisesRegex(RuntimeError, "after 8 model responses"):
                run_agent("Repeated invalid arguments.", trace=trace, verbose=False)
        saved = read_trace(Path(trace.trace_path))
        self.assertEqual(saved["status"], "iteration_limit")
        self.assertEqual(saved["termination_reason"], "iteration_limit")
        self.assertEqual(saved["metrics"]["tool_validation_errors"], 8)
        self.assertIsNone(saved["final_answer"])

    def test_runtime_tool_failure_keeps_timing_and_controlled_termination(self):
        llm = FakeLlm([tool_request("calculator", {"operation": "add", "a": 2, "b": 3}, "broken")])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=llm), \
             patch.object(calculator, "func", side_effect=RuntimeError("secret internal error")):
            with self.assertRaises(ToolInvocationError):
                run_agent("Runtime failure.", trace=trace, verbose=False)
        saved = read_trace(Path(trace.trace_path))
        self.assertEqual(saved["status"], "tool_error")
        self.assertEqual(saved["termination_reason"], "tool_runtime_error")
        self.assertEqual(saved["tool_calls"][0]["status"], "runtime_error")
        self.assertIsNotNone(saved["tool_calls"][0]["finished_at"])
        self.assertNotIn("secret internal error", json.dumps(saved))

    def test_unexpected_failure_is_persisted(self):
        trace = AgentTrace()
        with patch("app.agent._run_agent", side_effect=ValueError("raw unexpected exception")):
            with self.assertRaises(ValueError):
                run_agent("Unexpected failure.", trace=trace, verbose=False)
        saved = read_trace(Path(trace.trace_path))
        self.assertEqual(saved["status"], "unexpected_error")
        self.assertNotIn("raw unexpected exception", json.dumps(saved))

    def test_interrupted_run_is_finalized_and_interrupt_is_not_swallowed(self):
        trace = AgentTrace()
        with patch("app.agent._run_agent", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_agent("Interrupted run.", trace=trace, verbose=False)
        saved = read_trace(Path(trace.trace_path))
        self.assertEqual(saved["status"], "unexpected_error")
        self.assertIsNotNone(saved["finished_at"])

    def test_sql_error_observation_is_not_a_tool_argument_error(self):
        with patch.object(execute_sql, "func", return_value="SQL execution error: column does not exist."):
            _, _, saved, _ = self.run_fake([
                tool_request("execute_sql", {"query": "SELECT missing FROM orders"}, "bad-sql"),
                AIMessage(content="The query could not be completed."),
            ])
        self.assertEqual(saved["status"], "success")  # A final response was delivered, not a correctness judgment.
        self.assertEqual(saved["sql_calls"][0]["status"], "error")
        self.assertEqual(saved["sql_calls"][0]["error_category"], "sql_execution_error")
        self.assertEqual(saved["metrics"]["sql_execution_errors"], 1)
        self.assertEqual(saved["metrics"]["tool_validation_errors"], 0)

    def test_preview_caps_sql_rows_text_and_retains_application_truncation(self):
        rendered = "COLUMNS:\nvalue\n\nROWS:\n" + "\n".join(f"row-{i}" for i in range(100))
        rendered += "\n\nRows returned: 100\nResult truncated to 100 rows."
        preview = result_preview(rendered)
        self.assertEqual(preview["row_count"], 100)
        self.assertEqual(preview["rows_shown"], PREVIEW_ROWS)
        self.assertTrue(preview["result_truncated"])
        self.assertTrue(preview["preview_truncated"])
        self.assertNotIn("row-10\n", preview["text"])
        self.assertLessEqual(len(preview["text"]), PREVIEW_CHARS)
        self.assertEqual(len(result_preview("x" * 10000)["text"]), PREVIEW_CHARS)
        empty = result_preview("COLUMNS:\nid\n\nROWS:\n(no rows)\n\nRows returned: 0")
        self.assertEqual(empty["row_count"], 0)
        self.assertFalse(empty["preview_truncated"])
        self.assertIsNone(result_preview("Unrecognized output")["row_count"])

    def test_json_never_contains_fake_secrets_headers_urls_or_environment_dump(self):
        password, api_key = "fake-database-password", "fake-gemini-api-key"
        marker = "fake-bearer-credential"
        question = f"Question {password} {api_key} postgresql://user:other-password@localhost/db"
        response = AIMessage(content=f"Answer {password} {api_key}\nAuthorization: Bearer {marker}")
        with patch.dict(os.environ, {"DB_PASSWORD": password, "GOOGLE_API_KEY": api_key}):
            _, trace, _, _ = self.run_fake([response], question=question)
            rendered = Path(trace.trace_path).read_text(encoding="utf-8")
            for secret in (password, api_key, marker, "other-password", "postgresql://"):
                self.assertNotIn(secret, rendered)
            self.assertEqual(safe_trace_value({"environment": {"DB_HOST": "environment-host"}}),
                             {"environment": "[REDACTED]"})
            self.assertNotIn(marker, str(safe_trace_value({"headers": {"Authorization": marker}})))

    def test_validation_feedback_and_arguments_are_redacted_in_json(self):
        secret = "fake-secret-validation-value"
        with patch.dict(os.environ, {"DB_PASSWORD": secret}):
            _, trace, saved, _ = self.run_fake([
                tool_request("execute_sql", {"query": {"value": secret, "dsn": "postgresql://a:b@host/db"}}, "invalid"),
                AIMessage(content="The arguments were invalid."),
            ])
        rendered = Path(trace.trace_path).read_text(encoding="utf-8")
        self.assertNotIn(secret, rendered)
        self.assertNotIn("postgresql://", rendered)
        self.assertEqual(saved["tool_calls"][0]["status"], "validation_error")

    def test_atomic_publication_failure_is_safe_and_does_not_break_answer(self):
        errors = io.StringIO()
        with patch("app.trace.Path.replace", side_effect=OSError("password=fake-secret-filesystem-error")), \
             redirect_stderr(errors):
            llm = FakeLlm([AIMessage(content="Normal answer.")])
            trace = AgentTrace()
            with patch("app.agent.get_llm", return_value=llm):
                answer = run_agent("Normal question.", trace=trace, verbose=False)
        self.assertEqual(answer, "Normal answer.")
        self.assertIsNone(trace.trace_path)
        self.assertIn("Trace warning:", errors.getvalue())
        self.assertNotIn("fake-secret-filesystem-error", errors.getvalue())
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_usage_metadata_is_optional_and_only_numeric_allowlisted_counts_are_saved(self):
        _, _, saved, _ = self.run_fake([AIMessage(content="Done", usage_metadata={
            "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
        })])
        self.assertEqual(saved["turns"][0]["token_usage"],
                         {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
        _, _, without, _ = self.run_fake([AIMessage(content="Done")])
        self.assertNotIn("token_usage", without["turns"][0])

    def test_eval_links_trace_without_ground_truth_or_duplicate_tool_payloads(self):
        case = {"id": "fake_case", "question": "How many orders?", "category": "basic",
                "difficulty": "easy", "requires": ["count"],
                "reference_sql": "SELECT evaluator_only_secret", "expected_result": 300}
        llm = FakeLlm([
            tool_request("execute_sql", {"query": "SELECT COUNT(*) FROM orders"}, "count"),
            AIMessage(content="There are 300 orders."),
        ])
        with patch("app.agent.get_llm", return_value=llm), \
             patch.object(execute_sql, "func", return_value="COLUMNS:\ncount\n\nROWS:\n300\n\nRows returned: 1"):
            result = evaluate_case(case, query_executor=lambda query: {"columns": ["count"], "rows": [[300]]})
        self.assertTrue(result["passed"])
        self.assertNotIn("tool_calls", result)
        saved = read_trace(Path(result["trace_path"]))
        self.assertEqual(result["run_id"], saved["run_id"])
        self.assertEqual(saved["source"], "eval")
        self.assertEqual(saved["case_id"], "fake_case")
        rendered = json.dumps(saved)
        for forbidden in ("expected_result", "reference_sql", "evaluator_only_secret", "comparison", '"passed"'):
            self.assertNotIn(forbidden, rendered)
        # New reports rescore their recorded structured result, never limited previews.
        report = rescore_report({"summary": {"passed": 1, "total_cases": 1}, "cases": [result]}, [case])
        self.assertTrue(report["cases"][0]["passed"])
        self.assertEqual(report["cases"][0]["trace_path"], result["trace_path"])

    def test_reader_handles_latest_explicit_path_and_missing_files(self):
        _, trace, _, _ = self.run_fake([
            tool_request("calculator", {"operation": "add", "a": 2, "b": 3}, "add"),
            AIMessage(content="5"),
        ])
        for args in (["--latest"], [trace.trace_path]):
            output = io.StringIO()
            with patch("sys.argv", ["app.trace", *args]), redirect_stdout(output):
                main()
            text = output.getvalue()
            self.assertIn("RUN " + trace.run_id, text)
            self.assertLess(text.index("Turn 1"), text.index("Tool calculator"))
            self.assertLess(text.index("Tool calculator"), text.index("Turn 2"))
            self.assertIn("Answer: 5", text)
        with patch("sys.argv", ["app.trace", str(self.runs / "missing.json")]), \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
