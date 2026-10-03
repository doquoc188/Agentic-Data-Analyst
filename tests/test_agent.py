"""Focused tests for the manual agent tool registry and dispatch."""

import unittest
import os
import hashlib
import json
import re
from tempfile import TemporaryDirectory
from pathlib import Path
from dataclasses import asdict
from unittest.mock import MagicMock
from unittest.mock import patch

from langchain.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from app.agent import AgentTrace, ModelIntegrationError, SYSTEM_INSTRUCTIONS, TOOLS, ToolInvocationError, run_agent
from app.tools import calculator, describe_table, execute_sql


class FakeBoundLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.message_history = []

    def invoke(self, messages):
        self.message_history.append(list(messages))
        return self.responses.pop(0)


class FakeLlm:
    def __init__(self, responses):
        self.bound = FakeBoundLlm(responses)
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self.bound


def tool_request(name, args, call_id):
    return AIMessage(
        content="",
        tool_calls=[{
            "name": name,
            "args": args,
            "id": call_id,
            "type": "tool_call",
        }],
    )


class AgentTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.trace_directory = Path(directory.name)
        self.trace_patch = patch("app.trace.RUNS_DIR", self.trace_directory)
        self.trace_patch.start()
        self.addCleanup(self.trace_patch.stop)

    def test_convergence_guidance_has_no_benchmark_answers(self):
        self.assertIn("stop exploring and answer", SYSTEM_INSTRUCTIONS)
        self.assertIn("verified zero-row result", SYSTEM_INSTRUCTIONS)
        self.assertIn("Preserve response budget", SYSTEM_INSTRUCTIONS)
        self.assertIn("contradictory evidence", SYSTEM_INSTRUCTIONS)
        self.assertIn("trusted column descriptions", SYSTEM_INSTRUCTIONS)
        self.assertNotIn("0.10", SYSTEM_INSTRUCTIONS)  # Units come from DB metadata.
        self.assertNotIn("reference_sql", SYSTEM_INSTRUCTIONS)
        self.assertNotIn("expected_result", SYSTEM_INSTRUCTIONS)
        from eval.runner import load_cases
        for case in load_cases():
            self.assertNotIn(case["id"], SYSTEM_INSTRUCTIONS)

    def test_verified_empty_observation_can_lead_directly_to_an_answer(self):
        fake_llm = FakeLlm([
            tool_request("execute_sql", {"query": "SELECT id FROM example WHERE active"}, "empty"),
            AIMessage(content="No matching records exist."),
        ])
        fake_tool = MagicMock()
        fake_tool.invoke.return_value = "COLUMNS:\nid\n\nROWS:\n(no rows)\n\nRows returned: 0"
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.dict("app.agent.TOOL_MAP", {"execute_sql": fake_tool}):
            self.assertEqual(run_agent("Find active records.", trace=trace, verbose=False),
                             "No matching records exist.")
        self.assertEqual(trace.tool_calls[0].status, "success")
        self.assertEqual(trace.model_turns, 2)
        fake_tool.invoke.assert_called_once()
        self.assertIn("at most 8 model responses, including the final answer",
                      fake_llm.bound.message_history[0][0].content)

    def test_categorical_guidance_is_generic_targeted_and_preserves_convergence(self):
        for guidance in (
            "categorical/text filter values are database semantics",
            "do not assume capitalization, spelling, or codes",
            "trusted describe_table metadata that defines allowed values",
            "SELECT DISTINCT query with ORDER BY and LIMIT 10",
            "inferred, unverified categorical literal is insufficient evidence",
            "verify stored values before concluding no matches",
            "correct the literal and rerun the analytical query if needed",
            "Reuse literals established by trusted metadata or previous tool results",
            "do not inspect before every equality filter",
            "a zero result is valid; conclude without repeated verification",
            "Preserve exact equality semantics",
            "Do not treat a bounded sample as an exhaustive list",
        ):
            self.assertIn(guidance, SYSTEM_INSTRUCTIONS)
        self.assertIsNone(re.search(
            r"\b(priority|high|support_tickets|saas|accounts|plans|subscriptions|invoices|monthly_price_per_seat)\b",
            SYSTEM_INSTRUCTIONS, re.IGNORECASE,
        ))
        for relative in ("eval/cases.json", "eval/generalization/cases.json"):
            path = Path(__file__).resolve().parents[1] / relative
            for case in json.loads(path.read_text(encoding="utf-8")):
                self.assertNotIn(case["id"], SYSTEM_INSTRUCTIONS)

    def test_fake_categorical_zero_is_grounded_corrected_and_traced(self):
        # Scripted model choices verify dispatch/observations, not live LLM compliance.
        inferred = "SELECT COUNT(*) AS count FROM records WHERE state = 'Ready'"
        distinct = "SELECT DISTINCT state FROM records ORDER BY state LIMIT 10"
        corrected = "SELECT COUNT(*) AS count FROM records WHERE state = 'RDY'"
        results = {
            inferred: "COLUMNS:\ncount\n\nROWS:\n0\n\nRows returned: 1",
            distinct: "COLUMNS:\nstate\n\nROWS:\nDRAFT\nRDY\n\nRows returned: 2",
            corrected: "COLUMNS:\ncount\n\nROWS:\n7\n\nRows returned: 1",
        }
        fake_llm = FakeLlm([
            tool_request("execute_sql", {"query": inferred}, "initial-zero"),
            tool_request("execute_sql", {"query": distinct}, "ground-values"),
            tool_request("execute_sql", {"query": corrected}, "corrected-count"),
            AIMessage(content="There are 7 matching records."),
        ])
        paths = [Path(__file__).resolve().parents[1] / relative
                 for relative in ("eval/cases.json", "eval/generalization/cases.json")]
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(execute_sql, "func", side_effect=lambda query: results[query]) as sql:
            answer = run_agent("How many records are ready?", trace=trace, verbose=False)
        self.assertEqual(answer, "There are 7 matching records.")
        self.assertEqual([call.kwargs["query"] for call in sql.call_args_list],
                         [inferred, distinct, corrected])
        self.assertEqual(trace.model_turns, 4)
        self.assertEqual([call.status for call in trace.tool_calls], ["success"] * 3)
        self.assertIn("inferred, unverified categorical literal is insufficient evidence",
                      fake_llm.bound.message_history[0][0].content)
        for history, call_id, query in zip(fake_llm.bound.message_history[1:],
                                          ["initial-zero", "ground-values", "corrected-count"],
                                          [inferred, distinct, corrected]):
            self.assertIsInstance(history[-2], AIMessage)
            self.assertIsInstance(history[-1], ToolMessage)
            self.assertEqual(history[-1].tool_call_id, call_id)
            self.assertEqual(history[-1].content, results[query])
        saved = json.loads(Path(trace.trace_path).read_text(encoding="utf-8"))
        self.assertEqual([call["query"] for call in saved["sql_calls"]],
                         [inferred, distinct, corrected])
        self.assertEqual([call["tool_call_id"] for call in saved["sql_calls"]],
                         ["initial-zero", "ground-values", "corrected-count"])
        self.assertEqual(saved["final_answer"], answer)
        self.assertEqual(saved["status"], "success")
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

    def test_metadata_grounded_zero_count_needs_no_distinct_or_retry(self):
        query = "SELECT COUNT(*) AS count FROM records WHERE state = 'RDY'"
        metadata = "TABLE: records\nCOLUMNS:\n- state: text\n  description: Stored states: DRAFT, RDY."
        zero = "COLUMNS:\ncount\n\nROWS:\n0\n\nRows returned: 1"
        fake_llm = FakeLlm([
            tool_request("describe_table", {"table_name": "records"}, "metadata"),
            tool_request("execute_sql", {"query": query}, "grounded-zero"),
            AIMessage(content="No matching records exist."),
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(describe_table, "func", return_value=metadata) as describe, \
             patch.object(execute_sql, "func", return_value=zero) as sql:
            answer = run_agent("Find ready records.", trace=trace, verbose=False)
        self.assertEqual(answer, "No matching records exist.")
        describe.assert_called_once_with(table_name="records")
        sql.assert_called_once_with(query=query)
        self.assertEqual([call.name for call in trace.tool_calls], ["describe_table", "execute_sql"])
        self.assertEqual(trace.model_turns, 3)
        self.assertEqual(fake_llm.bound.message_history[1][-1].content, metadata)
        self.assertEqual(fake_llm.bound.message_history[2][-1].content, zero)

    def test_previously_observed_literal_can_return_zero_without_rediscovery(self):
        observed_query = "SELECT state FROM records ORDER BY state LIMIT 1"
        analytical_query = "SELECT id FROM records WHERE state = 'RDY' AND archived = TRUE"
        observed = "COLUMNS:\nstate\n\nROWS:\nRDY\n\nRows returned: 1"
        empty = "COLUMNS:\nid\n\nROWS:\n(no rows)\n\nRows returned: 0"
        fake_llm = FakeLlm([
            tool_request("execute_sql", {"query": observed_query}, "observed-value"),
            tool_request("execute_sql", {"query": analytical_query}, "verified-empty"),
            AIMessage(content="No matching archived records exist."),
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(execute_sql, "func", side_effect=[observed, empty]) as sql:
            answer = run_agent("Find archived ready records.", trace=trace, verbose=False)
        self.assertEqual(answer, "No matching archived records exist.")
        self.assertEqual(sql.call_count, 2)
        self.assertEqual(trace.model_turns, 3)
        self.assertEqual([call.arguments["query"] for call in trace.tool_calls],
                         [observed_query, analytical_query])

    def test_model_failure_records_phase_turn_and_safe_provider_category(self):
        fake_llm = FakeLlm([tool_request("calculator", {"operation": "add", "a": 1, "b": 2}, "ok")])
        provider = RuntimeError("Authorization: Bearer fake-api-key; password=fake-password")
        provider.status = "RESOURCE_EXHAUSTED"
        wrapper = RuntimeError("secret-bearing request object")
        wrapper.__cause__ = provider
        trace = AgentTrace()
        original_invoke = fake_llm.bound.invoke

        def invoke(messages):
            if fake_llm.bound.responses:
                return original_invoke(messages)
            raise wrapper

        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(fake_llm.bound, "invoke", side_effect=invoke):
            with self.assertRaises(ModelIntegrationError) as caught:
                run_agent("Add two values.", trace=trace, verbose=False)
        self.assertEqual(trace.model_turns, 1)  # Failed invocation is not a response.
        self.assertEqual(trace.model_error, {
            "phase": "model_invocation", "exception_type": "RuntimeError",
            "provider_category": "RESOURCE_EXHAUSTED", "model_turn": 2,
        })
        rendered = str(asdict(trace)) + str(caught.exception)
        for value in ("Authorization", "Bearer", "fake-api-key", "fake-password", "request object"):
            self.assertNotIn(value, rendered)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_setup_binding_and_unknown_model_errors_are_safe(self):
        trace = AgentTrace()
        with patch("app.agent.get_llm", side_effect=ValueError("api_key=do-not-log")):
            with self.assertRaises(ModelIntegrationError):
                run_agent("Example.", trace=trace, verbose=False)
        self.assertEqual(trace.model_error["phase"], "model_setup")
        self.assertEqual(trace.model_error["model_turn"], 0)
        self.assertEqual(trace.model_error["provider_category"], "unknown")
        fake_llm = MagicMock()
        error = RuntimeError("Authorization: do-not-log")
        error.code = 503
        fake_llm.bind_tools.side_effect = error
        with patch("app.agent.get_llm", return_value=fake_llm):
            with self.assertRaises(ModelIntegrationError):
                run_agent("Example.", trace=trace, verbose=False)
        self.assertEqual(trace.model_error["phase"], "tool_binding")
        self.assertEqual(trace.model_error["provider_category"], "UNAVAILABLE")
        self.assertNotIn("do-not-log", str(asdict(trace)))

    def test_invocation_is_recorded_before_failure_and_secrets_are_redacted(self):
        secret = "fake-secret-for-test"
        fake_llm = FakeLlm([
            tool_request("execute_sql", {"query": "SELECT '" + secret + "'", "password": secret}, "broken-call")
        ])
        trace = AgentTrace()

        def invoke(arguments):
            self.assertEqual(trace.tool_calls[0].status, "pending")
            self.assertEqual(arguments["password"], secret)  # Invocation arguments are unchanged.
            raise RuntimeError("postgresql://user:" + secret + "@localhost/database")

        fake_tool = MagicMock()
        fake_tool.invoke.side_effect = invoke
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.dict("app.agent.TOOL_MAP", {"execute_sql": fake_tool}), \
             patch.dict(os.environ, {"DB_PASSWORD": secret}):
            with self.assertRaises(ToolInvocationError) as caught:
                run_agent("A failing tool call.", trace=trace, verbose=False)

        event = trace.tool_calls[0]
        self.assertEqual(event.status, "error")
        self.assertEqual(event.error_type, "RuntimeError")
        self.assertEqual(event.tool_call_id, "broken-call")
        self.assertEqual(event.arguments["password"], "[REDACTED]")
        self.assertNotIn(secret, str(asdict(event)))
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("postgresql://", event.error_message)

    def test_validation_error_preserves_the_failed_tool_request(self):
        fake_llm = FakeLlm([
            tool_request("calculator", {"operation": "add", "b": 2}, "invalid-args"),
            AIMessage(content="I could not calculate without the missing value."),
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm):
            run_agent("Missing a required argument.", trace=trace, verbose=False)
        self.assertEqual(trace.model_turns, 2)
        self.assertEqual(trace.tool_calls[0].name, "calculator")
        self.assertEqual(trace.tool_calls[0].error_type, "ValidationError")
        self.assertEqual(trace.tool_calls[0].status, "error")
        self.assertNotIn("a", trace.tool_calls[0].arguments)
        observation = fake_llm.bound.message_history[1][-1]
        self.assertIsInstance(observation, ToolMessage)
        self.assertEqual(observation.tool_call_id, "invalid-args")
        self.assertIn("a: missing", observation.content)

    def test_invalid_sql_arguments_are_observed_then_corrected_by_model(self):
        invalid = {"queries": ["SELECT COUNT(*) FROM orders"]}
        corrected = {"query": "SELECT COUNT(*) FROM orders"}
        sql_result = "COLUMNS:\ncount\n\nROWS:\n300\n\nRows returned: 1"
        fake_llm = FakeLlm([
            tool_request("execute_sql", invalid, "invalid-sql"),
            tool_request("execute_sql", corrected, "corrected-sql"),
            AIMessage(content="There are 300 orders."),
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(execute_sql, "func", return_value=sql_result) as invoke:
            answer = run_agent("How many orders?", trace=trace, verbose=False)
        invoke.assert_called_once_with(**corrected)  # Invalid call never reaches the tool body.
        self.assertEqual(answer, "There are 300 orders.")
        self.assertEqual(trace.model_turns, 3)
        self.assertEqual(trace.tool_calls[0].arguments, invalid)  # Never silently renamed.
        self.assertEqual([call.status for call in trace.tool_calls], ["error", "success"])
        self.assertEqual(trace.tool_calls[0].error_type, "ValidationError")
        self.assertEqual(trace.tool_calls[0].error_category, "tool_argument_validation_error")
        invalid_message = fake_llm.bound.message_history[1][-1]
        self.assertIsInstance(invalid_message, ToolMessage)
        self.assertEqual(invalid_message.tool_call_id, "invalid-sql")
        for text in ("query: missing", "query: string (required)", "Received fields: queries"):
            self.assertIn(text, invalid_message.content)
        corrected_message = fake_llm.bound.message_history[2][-1]
        self.assertEqual(corrected_message.content, sql_result)
        self.assertEqual(corrected_message.tool_call_id, "corrected-sql")
        self.assertIsInstance(fake_llm.bound.message_history[1][-2], AIMessage)

    def test_describe_table_uses_the_same_generic_argument_recovery(self):
        fake_llm = FakeLlm([
            tool_request("describe_table", {"table": "orders"}, "wrong-key"),
            tool_request("describe_table", {"table_name": "orders"}, "right-key"),
            AIMessage(content="The table has an order_id primary key."),
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(describe_table, "func", return_value="TABLE: orders\nPRIMARY KEY:\n- order_id") as invoke:
            run_agent("Describe orders.", trace=trace, verbose=False)
        invoke.assert_called_once_with(table_name="orders")
        observation = fake_llm.bound.message_history[1][-1]
        self.assertIn("table_name: missing", observation.content)
        self.assertIn("Received fields: table", observation.content)
        self.assertEqual(observation.tool_call_id, "wrong-key")
        self.assertEqual([call.status for call in trace.tool_calls], ["error", "success"])

    def test_repeated_invalid_arguments_stop_at_existing_eight_response_limit(self):
        fake_llm = FakeLlm([
            tool_request("execute_sql", {"queries": ["SELECT 1"]}, f"bad-{turn}")
            for turn in range(8)
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(execute_sql, "func") as invoke:
            with self.assertRaisesRegex(RuntimeError, "after 8 model responses without a final answer"):
                run_agent("Keep sending invalid arguments.", trace=trace, verbose=False)
        invoke.assert_not_called()
        self.assertEqual(trace.model_turns, 8)
        self.assertIsNone(trace.final_answer)
        self.assertEqual(len(trace.tool_calls), 8)
        self.assertTrue(all(call.status == "error" for call in trace.tool_calls))
        for turn, messages in enumerate(fake_llm.bound.message_history[1:], 1):
            self.assertEqual(messages[-1].tool_call_id, f"bad-{turn - 1}")

    def test_invalid_call_does_not_skip_other_calls_in_the_same_response(self):
        invalid_response = tool_request("execute_sql", {"queries": ["SELECT 1"]}, "invalid")
        valid_response = tool_request("calculator", {"operation": "add", "a": 1, "b": 2}, "valid")
        fake_llm = FakeLlm([
            AIMessage(content="", tool_calls=invalid_response.tool_calls + valid_response.tool_calls),
            AIMessage(content="The arithmetic result is 3."),
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm):
            run_agent("Two requests.", trace=trace, verbose=False)
        observations = [message for message in fake_llm.bound.message_history[1]
                        if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in observations], ["invalid", "valid"])
        self.assertEqual(observations[1].content, "3.0")
        self.assertEqual([call.status for call in trace.tool_calls], ["error", "success"])

    def test_validation_feedback_excludes_secrets_and_raw_input_values(self):
        secret = "fake-password-for-validation-test"
        api_key = "fake-api-key-for-validation-test"
        arguments = {"query": {"value": secret, "api_key": api_key,
                               "dsn": "postgresql://user:password@host/database"}}
        fake_llm = FakeLlm([
            tool_request("execute_sql", arguments, "secret-input"),
            AIMessage(content="The tool arguments were invalid."),
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.dict(os.environ, {"DB_PASSWORD": secret, "GOOGLE_API_KEY": api_key}):
            run_agent("Invalid input.", trace=trace, verbose=False)
        observation = fake_llm.bound.message_history[1][-1]
        self.assertIn("query: string_type", observation.content)
        rendered = str(asdict(trace)) + observation.content
        for value in (secret, api_key, "postgresql://", "user:password@host"):
            self.assertNotIn(value, rendered)
        self.assertNotIn("value", observation.content)

    def test_validation_error_inside_tool_body_is_still_a_controlled_failure(self):
        try:
            calculator.get_input_schema().model_validate({})
        except ValidationError as exc:
            runtime_error = exc
        fake_llm = FakeLlm([
            tool_request("calculator", {"operation": "add", "a": 1, "b": 2}, "runtime-validation")
        ])
        trace = AgentTrace()
        with patch("app.agent.get_llm", return_value=fake_llm), \
             patch.object(calculator, "func", side_effect=runtime_error):
            with self.assertRaises(ToolInvocationError):
                run_agent("Valid arguments, broken implementation.", trace=trace, verbose=False)
        self.assertEqual(trace.tool_calls[0].error_type, "ValidationError")
        self.assertEqual(trace.tool_calls[0].error_category, "tool_runtime_error")
        self.assertEqual(trace.tool_calls[0].result, "")
        self.assertEqual(trace.model_turns, 1)

    def test_optional_trace_records_all_calls_without_changing_answer(self):
        fake_llm = FakeLlm([
            AIMessage(content="", tool_calls=[
                {"name": "calculator", "args": {"operation": "add", "a": 1, "b": 2},
                 "id": "first", "type": "tool_call"},
                {"name": "calculator", "args": {"operation": "multiply", "a": 3, "b": 4},
                 "id": "second", "type": "tool_call"},
            ]),
            AIMessage(content="The results are 3 and 12."),
        ])
        trace = AgentTrace()

        with patch("app.agent.get_llm", return_value=fake_llm):
            answer = run_agent("Calculate two values.", trace=trace, verbose=False)

        self.assertEqual(answer, "The results are 3 and 12.")
        self.assertEqual(trace.final_answer, answer)
        self.assertEqual(trace.model_turns, 2)
        self.assertEqual([call.tool_call_id for call in trace.tool_calls], ["first", "second"])
        self.assertEqual([call.result for call in trace.tool_calls], ["3.0", "12.0"])
        self.assertEqual([call.status for call in trace.tool_calls], ["success", "success"])

    def test_tool_registry_contains_all_project_tools(self):
        self.assertEqual(
            [tool.name for tool in TOOLS],
            ["calculator", "get_schema", "describe_table", "execute_sql"],
        )

    def test_dispatches_tool_and_returns_result_with_matching_call_id(self):
        fake_llm = FakeLlm([
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "calculator",
                    "args": {"operation": "multiply", "a": 25, "b": 17},
                    "id": "calculator-call-1",
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="25 multiplied by 17 is 425."),
        ])

        with patch("app.agent.get_llm", return_value=fake_llm):
            answer = run_agent("What is 25 * 17?")

        self.assertEqual(answer, "25 multiplied by 17 is 425.")
        tool_message = fake_llm.bound.message_history[1][-1]
        self.assertIsInstance(tool_message, ToolMessage)
        self.assertEqual(tool_message.content, "425.0")
        self.assertEqual(tool_message.tool_call_id, "calculator-call-1")

    def test_unknown_tool_is_returned_to_model_as_an_error(self):
        fake_llm = FakeLlm([
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "unknown_tool",
                    "args": {},
                    "id": "unknown-call-1",
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="I could not use that tool."),
        ])

        with patch("app.agent.get_llm", return_value=fake_llm):
            answer = run_agent("Use an unknown tool.")

        self.assertEqual(answer, "I could not use that tool.")
        tool_message = fake_llm.bound.message_history[1][-1]
        self.assertIn("Unknown tool requested: unknown_tool", tool_message.content)
        self.assertEqual(tool_message.tool_call_id, "unknown-call-1")

    def test_sql_error_can_be_inspected_and_corrected_across_turns(self):
        wrong_sql = "SELECT missing_column FROM public.orders"
        corrected_sql = "SELECT COUNT(*) AS total_rows FROM public.orders"
        fake_llm = FakeLlm([
            tool_request("execute_sql", {"query": wrong_sql}, "sql-error-1"),
            tool_request("describe_table", {"table_name": "orders"}, "describe-1"),
            tool_request("execute_sql", {"query": corrected_sql}, "sql-fixed-1"),
            AIMessage(content="There are 300 orders."),
        ])

        with patch("app.agent.get_llm", return_value=fake_llm):
            answer = run_agent("How many orders are there?")

        self.assertEqual(answer, "There are 300 orders.")
        history = fake_llm.bound.message_history
        self.assertEqual([len(messages) for messages in history], [2, 4, 6, 8])
        observations = [history[1][-1], history[2][-1], history[3][-1]]
        self.assertTrue(all(isinstance(message, ToolMessage) for message in observations))
        self.assertEqual(
            [message.tool_call_id for message in observations],
            ["sql-error-1", "describe-1", "sql-fixed-1"],
        )
        self.assertEqual(
            observations[0].content,
            "SQL execution error: column does not exist.",
        )
        self.assertIn("TABLE: orders", observations[1].content)
        self.assertIn("PRIMARY KEY:\n- order_id", observations[1].content)
        self.assertIn("total_rows", observations[2].content)
        self.assertIn("\n300\n", observations[2].content)

    def test_multiple_tool_calls_keep_their_ids_during_retry(self):
        fake_llm = FakeLlm([
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "execute_sql", "args": {"query": "SELECT bad_column FROM public.orders"},
                     "id": "bad-sql", "type": "tool_call"},
                    {"name": "describe_table", "args": {"table_name": "orders"},
                     "id": "table-details", "type": "tool_call"},
                ],
            ),
            tool_request(
                "execute_sql",
                {"query": "SELECT COUNT(*) AS total_rows FROM public.orders"},
                "good-sql",
            ),
            AIMessage(content="There are 300 orders."),
        ])

        with patch("app.agent.get_llm", return_value=fake_llm):
            answer = run_agent("How many orders are there?")

        self.assertEqual(answer, "There are 300 orders.")
        observations = [
            message for message in fake_llm.bound.message_history[-1]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(
            [message.tool_call_id for message in observations],
            ["bad-sql", "table-details", "good-sql"],
        )

    def test_iteration_limit_stops_repeated_tool_calls(self):
        fake_llm = FakeLlm([
            tool_request("calculator", {"operation": "add", "a": 1, "b": 1}, "call-1"),
            tool_request("calculator", {"operation": "add", "a": 1, "b": 1}, "call-2"),
        ])

        with patch("app.agent.get_llm", return_value=fake_llm):
            with self.assertRaisesRegex(RuntimeError, "after 2 model responses"):
                run_agent("Keep calculating.", max_iterations=2)

        self.assertEqual(len(fake_llm.bound.message_history), 2)
        self.assertIn("at most 2 model responses", fake_llm.bound.message_history[0][0].content)


if __name__ == "__main__":
    unittest.main()
