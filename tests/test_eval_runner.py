"""Offline tests for deterministic evaluation; no Gemini calls."""

import json
import inspect
import unittest
from decimal import Decimal
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agent import ModelIntegrationError, ToolCallRecord, ToolInvocationError
from eval.runner import (
    compare_result, evaluate_case, execute_read_only_sql, load_cases,
    run_evaluation, summary_for,
)
from eval.selection import select_answer_sql, sql_metrics, tool_validation_metrics
from eval.rescore import rescore_report


CASE = {
    "id": "example", "question": "How many orders?", "category": "basic",
    "difficulty": "easy", "requires": ["count", "filter"],
    "reference_sql": "SELECT secret_reference FROM example",
    "expected_result": 300,
}


def fake_agent(question, *, trace, verbose):
    assert question == CASE["question"]
    assert verbose is False
    trace.model_turns = 3
    trace.final_answer = "There are 300 orders."
    trace.tool_calls.extend([
        ToolCallRecord("get_schema", {}, "TABLE: orders", "schema-1"),
        ToolCallRecord("execute_sql", {"query": "SELECT bad FROM orders"},
                       "SQL execution error: column does not exist.", "sql-1"),
        ToolCallRecord("describe_table", {"table_name": "orders"}, "TABLE: orders", "table-1"),
        ToolCallRecord("execute_sql", {"query": "SELECT COUNT(*) FROM orders"},
                       "COLUMNS:\ncount\nROWS:\n300", "sql-2"),
    ])
    return trace.final_answer


class ComparisonTests(unittest.TestCase):
    SCALAR_SUPPORT = {"scalar_support": {
        "label_column": "status", "label_value": "Completed", "value_column": "count",
    }}

    def test_labeled_scalar_requires_explicit_opt_in_and_preserves_plain_scalar(self):
        actual = {"columns": ["status", "count"], "rows": [["Returned", 45], ["Completed", 198]]}
        self.assertTrue(compare_result(actual, 198, comparison=self.SCALAR_SUPPORT)[0])
        self.assertFalse(compare_result(actual, 198)[0])
        plain = {"columns": ["completed_orders"], "rows": [[198]]}
        self.assertTrue(compare_result(plain, 198, comparison=self.SCALAR_SUPPORT)[0])
        self.assertFalse(compare_result(plain, 199, comparison=self.SCALAR_SUPPORT)[0])

    def test_labeled_scalar_rejects_wrong_missing_or_duplicate_labels(self):
        for rows in (
            [["Completed", 197], ["Returned", 198]],  # Expected number in unrelated row.
            [["Returned", 198]],
            [["Completed", 198], ["Completed", 198]],
            [],
        ):
            with self.subTest(rows=rows):
                self.assertFalse(compare_result({"columns": ["status", "count"], "rows": rows},
                                                198, comparison=self.SCALAR_SUPPORT)[0])

    def test_labeled_scalar_requires_unambiguous_named_columns_and_valid_rows(self):
        for columns, rows in (
            (["state", "count"], [["Completed", 198]]),
            (["status", "total"], [["Completed", 198]]),
            (["status", "status", "count"], [["Completed", "Completed", 198]]),
            (["status", "count", "count"], [["Completed", 198, 198]]),
            (["status", "count"], [["Completed"]]),
            (["status", "count", "other"], [["Completed", 197, 198]]),
        ):
            with self.subTest(columns=columns, rows=rows):
                self.assertFalse(compare_result({"columns": columns, "rows": rows},
                                                198, comparison=self.SCALAR_SUPPORT)[0])
        reordered = {"columns": ["count", "extra", "status"], "rows": [[198, 45, "Completed"]]}
        self.assertTrue(compare_result(reordered, 198, comparison=self.SCALAR_SUPPORT)[0])

    def test_labeled_scalar_uses_existing_decimal_tolerance(self):
        actual = {"columns": ["status", "count"], "rows": [["Completed", Decimal("19.995")]]}
        self.assertTrue(compare_result(actual, 20, comparison={
            **self.SCALAR_SUPPORT, "numeric_tolerance": 0.01,
        })[0])
        self.assertFalse(compare_result(actual, 20, comparison={
            **self.SCALAR_SUPPORT, "numeric_tolerance": 0.001,
        })[0])

    def test_invalid_labeled_scalar_contracts_are_rejected(self):
        support = self.SCALAR_SUPPORT["scalar_support"]
        for contract in (
            {"scalar_support": None},
            {"scalar_support": {"label_column": "status"}},
            {"scalar_support": {**support, "value_column": "status"}},
            {"scalar_support": {**support, "label_column": ""}},
            {"scalar_support": {**support, "label_value": {"nested": "Completed"}}},
            {**self.SCALAR_SUPPORT, "result_type": "table"},
        ):
            with self.subTest(contract=contract), self.assertRaises(ValueError):
                compare_result({"columns": ["count"], "rows": [[198]]}, 198, comparison=contract)

    def test_only_semantically_declared_benchmark_case_opts_in(self):
        opted_in = [case for case in load_cases() if "scalar_support" in case.get("comparison", {})]
        self.assertEqual([case["id"] for case in opted_in], ["completed_orders"])
        self.assertEqual(opted_in[0]["comparison"]["scalar_support"], self.SCALAR_SUPPORT["scalar_support"])

    def test_required_fields_resolve_independently_with_extra_columns(self):
        expected = {"columns": ["name", "city", "count"], "rows": [["A", "Paris", 4]]}
        contract = {"column_matching": "position", "required_columns": ["name", "count"]}
        for actual in (
            {"columns": ["id", "name", "count"], "rows": [[7, "A", 4]]},
            {"columns": ["count", "id", "name"], "rows": [[4, 7, "A"]]},
            {"columns": ["id", "count", "name", "extra"], "rows": [[7, 4, "A", "ignored"]]},
            {"columns": ["count", "name"], "rows": [[4, "A"]]},
        ):
            with self.subTest(columns=actual["columns"]):
                self.assertTrue(compare_result(actual, expected, comparison=contract)[0])
                self.assertFalse(compare_result(actual, expected)[0])
        strict_projection = {**contract, "column_matching": "strict"}
        self.assertTrue(compare_result(actual, expected, comparison=strict_projection)[0])

    def test_projection_rejects_missing_and_ambiguous_required_fields(self):
        expected = {"columns": ["name", "city", "count"], "rows": [["A", "Paris", 4]]}
        contract = {"column_matching": "position", "required_columns": ["name", "count"]}
        for columns, row in (
            (["id", "city", "count"], [7, "Paris", 4]),
            (["name", "city"], ["A", "Paris"]),
            (["name", "name", "count"], ["A", "A", 4]),
        ):
            self.assertFalse(compare_result({"columns": columns, "rows": [row]}, expected,
                                            comparison=contract)[0])

    def test_full_positional_alias_layout_still_works(self):
        expected = {"columns": ["name", "city", "count"], "rows": [["A", "Paris", 4]]}
        actual = {"columns": ["name", "city", "total"], "rows": [["A", "Paris", 4]]}
        self.assertTrue(compare_result(actual, expected, comparison={
            "column_matching": "position", "required_columns": ["name", "count"],
        })[0])

    def test_ranked_prefix_is_explicit_ordered_and_checks_every_expected_row(self):
        expected = {"columns": ["city", "revenue"], "rows": [["A", 20], ["B", 10]]}
        actual = {**expected, "rows": [*expected["rows"], ["C", 5]]}
        contract = {"row_match": "prefix", "row_order": "strict"}
        self.assertFalse(compare_result(actual, expected)[0])
        self.assertTrue(compare_result(actual, expected, comparison=contract)[0])
        for rows in ([['B', 10], ['A', 20], ['C', 5]], [['A', 20]], [['A', 20], ['C', 5]]):
            self.assertFalse(compare_result({**actual, "rows": rows}, expected, comparison=contract)[0])
        top_one = {**expected, "rows": expected["rows"][:1]}
        self.assertTrue(compare_result(actual, top_one, comparison=contract)[0])
        self.assertFalse(compare_result({**actual, "rows": [['B', 10], ['A', 20]]}, top_one,
                                        comparison=contract)[0])
        with self.assertRaises(ValueError):
            compare_result(actual, expected, comparison={**contract, "row_order": "unordered"})
        with self.assertRaises(ValueError):
            compare_result(actual, {**expected, "rows": []}, comparison=contract)
        with self.assertRaises(ValueError):
            compare_result({"columns": ["count"], "rows": [[20]]}, 20, comparison=contract)

    def test_aliases_are_relaxed_only_by_explicit_contract(self):
        actual = {"columns": ["category", "total_units"], "rows": [["A", 2]]}
        expected = {"columns": ["category", "units"], "rows": [["A", 2]]}
        self.assertFalse(compare_result(actual, expected)[0])
        self.assertTrue(compare_result(actual, expected, comparison={"column_matching": "position"})[0])

    def test_month_granularity_is_opt_in(self):
        actual = {"columns": ["month", "count"],
                  "rows": [[datetime(2025, 1, 1, tzinfo=timezone.utc), 8]]}
        expected = {"columns": ["month", "count"], "rows": [["2025-01", 8]]}
        self.assertFalse(compare_result(actual, expected)[0])
        self.assertTrue(compare_result(actual, expected, comparison={"time_granularity": "month"})[0])
        actual["rows"][0][0] = "2025-01-01T00:00:00+07:00"
        self.assertTrue(compare_result(actual, expected, comparison={"time_granularity": "month"})[0])

    def test_unordered_groups_and_strict_rankings(self):
        expected = {"columns": ["city", "count"], "rows": [["A", 2], ["B", 1]]}
        actual = {**expected, "rows": list(reversed(expected["rows"]))}
        self.assertFalse(compare_result(actual, expected)[0])
        self.assertTrue(compare_result(actual, expected, comparison={"row_order": "unordered"})[0])
        duplicated = {**expected, "rows": [["A", 2], ["A", 2]]}
        self.assertFalse(compare_result(duplicated, expected, comparison={"row_order": "unordered"})[0])

    def test_unordered_tolerance_can_match_each_occurrence(self):
        actual = {"columns": ["value"], "rows": [[Decimal("0")], [Decimal("0.02")]]}
        expected = {"columns": ["value"], "rows": [[0.01], [0]]}
        self.assertTrue(compare_result(actual, expected, comparison={
            "row_order": "unordered", "numeric_tolerance": 0.01,
        })[0])

    def test_only_declared_supplementary_fields_can_be_omitted(self):
        expected = {"columns": ["name", "city", "count"], "rows": [["A", "Paris", 2]]}
        actual = {"columns": ["name", "total"], "rows": [["A", 2]]}
        contract = {"column_matching": "position", "required_columns": ["name", "count"]}
        self.assertTrue(compare_result(actual, expected, comparison=contract)[0])
        self.assertFalse(compare_result(actual, expected)[0])
        missing = {"columns": ["name"], "rows": [["A"]]}
        self.assertFalse(compare_result(missing, expected, comparison=contract)[0])
        self.assertFalse(compare_result(actual, expected, comparison={
            "required_columns": ["name", "count"],
        })[0])  # Strict requested names do not accept the alias 'total'.

    def test_incorrect_revenue_still_fails_when_aliases_are_allowed(self):
        expected = {"columns": ["city", "revenue"], "rows": [["Hanoi", 8299.85]]}
        actual = {"columns": ["city", "total_revenue"], "rows": [["Hanoi", Decimal("9024.38145")]]}
        self.assertFalse(compare_result(actual, expected, comparison={
            "column_matching": "position", "numeric_tolerance": 0.01,
        })[0])

    def test_scalar_equality_and_tolerance(self):
        actual = {"columns": ["total"], "rows": [[Decimal("19.995")]]}
        self.assertTrue(compare_result(actual, 19.995)[0])
        self.assertTrue(compare_result(actual, 20.0, 0.01)[0])
        self.assertFalse(compare_result(actual, 20.0, 0.001)[0])
        self.assertFalse(compare_result({"columns": ["total"], "rows": []}, 20)[0])

    def test_structured_equality_order_and_tolerance(self):
        actual = {"columns": ["city", "revenue"],
                  "rows": [["Paris", Decimal("20.005")], ["Rome", Decimal("10")]]}
        expected = {"columns": ["city", "revenue"],
                    "rows": [["Paris", 20.0], ["Rome", 10]]}
        self.assertTrue(compare_result(actual, expected, 0.01)[0])
        self.assertFalse(compare_result(actual, expected)[0])
        self.assertFalse(compare_result(actual, {**expected, "columns": ["name", "revenue"]}, 0.01)[0])
        self.assertFalse(compare_result(actual, {**expected, "rows": list(reversed(expected["rows"]))}, 0.01)[0])


class RunnerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tracing = patch("app.trace.RUNS_DIR", Path(directory.name))
        tracing.start()
        self.addCleanup(tracing.stop)

    def test_tool_validation_recovery_metrics_require_same_tool_success_and_case_pass(self):
        def corrected_agent(question, *, trace, verbose):
            trace.tool_calls.extend([
                ToolCallRecord("execute_sql", {"queries": ["SELECT COUNT(*) FROM orders"]},
                               "Tool argument validation error: query: missing.", "invalid",
                               status="error", error_type="ValidationError",
                               error_category="tool_argument_validation_error"),
                ToolCallRecord("execute_sql", {"query": "SELECT COUNT(*) FROM orders"},
                               "COLUMNS:\ncount\n\nROWS:\n300\n\nRows returned: 1", "corrected"),
            ])
            return "There are 300 orders."

        passed = evaluate_case(CASE, corrected_agent, lambda query: {"columns": ["count"], "rows": [[300]]})
        failed = evaluate_case(CASE, corrected_agent, lambda query: {"columns": ["count"], "rows": [[299]]})
        self.assertEqual(passed["tool_argument_validation_errors"], 1)
        self.assertEqual(passed["sql_invocation_errors"], 1)
        self.assertEqual(passed["tool_invocation_errors"], 1)
        self.assertEqual(passed["sql_execution_errors"], 0)
        self.assertTrue(passed["recovered_after_tool_validation_error"])
        self.assertFalse(failed["recovered_after_tool_validation_error"])
        summary = summary_for([passed, failed])
        self.assertEqual(summary["tool_argument_validation_errors"], 2)
        self.assertEqual(summary["cases_recovered_after_tool_validation_error"], 1)
        self.assertEqual(summary["recovered_after_sql_error"], 0)

    def test_validation_metrics_exclude_other_tool_success_runtime_and_sql_errors(self):
        invalid = ToolCallRecord("describe_table", {"table": "orders"},
                                 "Tool argument validation error.", "invalid", status="error",
                                 error_type="ValidationError", error_category="tool_argument_validation_error")
        corrected = ToolCallRecord("describe_table", {"table_name": "orders"}, "TABLE: orders", "corrected")
        other = ToolCallRecord("calculator", {"operation": "add", "a": 1, "b": 2}, "3.0", "other")
        runtime = ToolCallRecord("describe_table", {}, "", "runtime", status="error",
                                 error_type="ValidationError", error_category="tool_runtime_error")
        sql_error = ToolCallRecord("execute_sql", {}, "SQL execution error: invalid SQL syntax.", "sql-error")
        sql_ok = ToolCallRecord("execute_sql", {"query": "SELECT 1"}, "COLUMNS:\nvalue\n\nROWS:\n1", "ok")
        for calls in ([invalid, other], [corrected, invalid], [runtime, corrected], [sql_error, sql_ok]):
            self.assertFalse(tool_validation_metrics(calls, True)["recovered_after_tool_validation_error"])
        metrics = tool_validation_metrics([runtime, sql_error, sql_ok], True)
        self.assertEqual(metrics["tool_argument_validation_errors"], 0)
        self.assertTrue(tool_validation_metrics([invalid, corrected], True)["recovered_after_tool_validation_error"])
        self.assertFalse(tool_validation_metrics([invalid, corrected], False)["recovered_after_tool_validation_error"])
        self.assertFalse(tool_validation_metrics([invalid, runtime], True)["recovered_after_tool_validation_error"])

    def test_offline_rescore_does_not_invent_validation_recovery_or_missing_answer(self):
        invalid = ToolCallRecord("execute_sql", {"queries": ["SELECT COUNT(*) FROM orders"]}, "", "invalid",
                                 status="error", error_type="ValidationError")
        saved = {"summary": {"passed": 0, "total_cases": 1}, "cases": [{
            "case_id": CASE["id"], "question": CASE["question"], "category": CASE["category"],
            "difficulty": CASE["difficulty"], "requires": CASE["requires"], "passed": False,
            "final_answer": None, "failure_reason": "Agent failed (ToolInvocationError).",
            "generated_sql": None, "actual_result": None,
            "tool_calls": [invalid.__dict__],
        }]}
        # Legacy files did not have error_category; they remain readable.
        del saved["cases"][0]["tool_calls"][0]["error_category"]
        with patch("app.agent.get_llm", side_effect=AssertionError("No Gemini")), \
             patch("eval.runner.get_connection", side_effect=AssertionError("No PostgreSQL")):
            report = rescore_report(saved, [CASE])
        self.assertEqual(report["run_type"], "offline rescore of the previous live run")
        self.assertEqual(report["summary"]["tool_argument_validation_errors"], 1)
        self.assertEqual(report["summary"]["cases_recovered_after_tool_validation_error"], 0)
        self.assertFalse(report["cases"][0]["passed"])
        self.assertIsNone(report["cases"][0]["final_answer"])
        self.assertIsNone(report["cases"][0]["actual_result"])

    def test_model_tool_sql_and_iteration_failures_are_distinct(self):
        def model_error(question, *, trace, verbose):
            raise ModelIntegrationError(RuntimeError("Authorization: secret"), "model_invocation", 2)

        def tool_error(question, *, trace, verbose):
            trace.tool_calls.append(ToolCallRecord(
                "calculator", {}, "", "broken", status="error", error_type="ValidationError"
            ))
            raise ToolInvocationError("Failed before an observation.")

        def limit(question, *, trace, verbose):
            trace.model_turns = 8
            raise RuntimeError("Agent stopped after 8 model responses without a final answer.")

        def sql_error(question, *, trace, verbose):
            trace.tool_calls.append(ToolCallRecord(
                "execute_sql", {"query": "SELECT missing FROM orders"},
                "SQL execution error: column does not exist.", "bad"
            ))
            return "The query could not be completed."

        results = [evaluate_case(CASE, agent, lambda query: self.fail("No re-execution"))
                   for agent in (model_error, tool_error, limit, sql_error)]
        self.assertEqual([r["failure_type"] for r in results], [
            "model_integration_error", "tool_invocation_error", "iteration_limit", "sql_execution_error",
        ])
        self.assertEqual(results[0]["model_error"]["model_turn"], 2)
        self.assertNotIn("Authorization", json.dumps(results))
        summary = summary_for(results)
        self.assertEqual(summary["model_integration_errors"], 1)
        self.assertEqual(summary["tool_invocation_errors"], 1)  # Includes non-SQL tools.
        self.assertEqual(summary["iteration_limit_failures"], 1)
        self.assertEqual(summary["sql_execution_errors"], 1)

    def test_selection_failure_metric_is_limited_to_identifiable_missing_support(self):
        def unsupported(question, *, trace, verbose):
            trace.tool_calls.append(ToolCallRecord(
                "execute_sql", {"query": "SELECT status FROM orders"},
                "COLUMNS:\nstatus\n\nROWS:\nReturned\n\nRows returned: 1", "lookup"
            ))
            return "There are 300 orders."

        result = evaluate_case(CASE, unsupported, lambda query: self.fail("Unsupported query"))
        self.assertEqual(result["failure_type"], "evaluator_selection_failure")
        self.assertEqual(summary_for([result])["evaluator_selection_failures"], 1)
        self.assertEqual(result["sql_execution_errors"], 0)

    def test_load_cases(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps([CASE]), encoding="utf-8")
            self.assertEqual(load_cases(path), [CASE])

    def test_agent_sees_question_only_and_retry_metrics_are_recorded(self):
        executor = MagicMock(return_value={"columns": ["count"], "rows": [[300]]})
        result = evaluate_case(CASE, fake_agent, executor)
        self.assertTrue(result["passed"])
        self.assertEqual(result["final_answer"], "There are 300 orders.")
        self.assertEqual(result["generated_sql"], "SELECT COUNT(*) FROM orders")
        self.assertEqual(result["sql_attempts"], 2)
        self.assertEqual(result["sql_errors"], 1)
        self.assertEqual(result["model_turns"], 3)
        self.assertTrue(result["used_get_schema"])
        self.assertTrue(result["used_describe_table"])
        self.assertNotIn("tool_calls", result)
        saved_trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
        self.assertEqual([call["tool_call_id"] for call in saved_trace["tool_calls"]],
                         ["schema-1", "sql-1", "table-1", "sql-2"])
        executor.assert_called_once_with("SELECT COUNT(*) FROM orders")
        self.assertNotIn(CASE["reference_sql"], json.dumps(saved_trace))

    def test_wrong_result_missing_sql_and_case_error_do_not_abort(self):
        wrong = evaluate_case(CASE, fake_agent,
                              lambda query: {"columns": ["count"], "rows": [[299]]})
        self.assertFalse(wrong["passed"])
        self.assertIn("Scalar value", wrong["failure_reason"])

        def no_sql(question, *, trace, verbose):
            trace.model_turns = 1
            return "I cannot answer."

        missing = evaluate_case(CASE, no_sql, lambda query: self.fail("SQL was not requested"))
        self.assertFalse(missing["passed"])
        self.assertIn("No successful execute_sql", missing["failure_reason"])

        def failed_sql(question, *, trace, verbose):
            trace.model_turns = 2
            trace.tool_calls.append(ToolCallRecord(
                "execute_sql", {"query": "SELECT missing FROM orders"},
                "SQL execution error: column does not exist.", "failed-sql"
            ))
            return "I could not answer."

        all_failed = evaluate_case(CASE, failed_sql, lambda query: self.fail("SQL was not requested"))
        self.assertEqual(all_failed["sql_attempts"], 1)
        self.assertEqual(all_failed["sql_errors"], 1)
        self.assertIsNone(all_failed["generated_sql"])
        self.assertFalse(all_failed["passed"])

        def broken(question, *, trace, verbose):
            trace.model_turns = 8
            raise RuntimeError("Agent stopped after 8 model responses without a final answer.")

        report = run_evaluation([CASE, CASE], broken)
        self.assertEqual(report["summary"]["failed"], 2)
        self.assertEqual(report["cases"][0]["model_turns"], 8)

    def test_evaluator_query_failure_is_case_failure(self):
        def raises(query):
            raise ValueError("dangerous connection text")

        result = evaluate_case(CASE, fake_agent, raises)
        self.assertEqual(result["failure_reason"], "Evaluator SQL failed (ValueError).")
        self.assertNotIn("dangerous connection text", json.dumps(result))

    def test_summary_grouping_and_recovery(self):
        passed = evaluate_case(CASE, fake_agent,
                               lambda query: {"columns": ["count"], "rows": [[300]]})
        failed_case = {**CASE, "id": "hard_case", "category": "join",
                       "difficulty": "hard", "requires": ["join", "filter"]}
        failed = evaluate_case(failed_case, fake_agent,
                               lambda query: {"columns": ["count"], "rows": [[0]]})
        summary = summary_for([passed, failed])
        self.assertEqual((summary["total_cases"], summary["passed"], summary["failed"]), (2, 1, 1))
        self.assertEqual(summary["pass_rate"], 50.0)
        self.assertEqual(summary["by_difficulty"]["easy"]["pass_rate"], 100.0)
        self.assertEqual(summary["by_difficulty"]["hard"]["pass_rate"], 0.0)
        self.assertEqual(summary["by_category"]["join"]["passed"], 0)
        self.assertEqual(summary["by_capability"]["filter"]["total"], 2)
        self.assertEqual(summary["average_tool_calls"], 4.0)
        self.assertEqual(summary["average_sql_attempts"], 2.0)
        self.assertEqual(summary["cases_with_multiple_sql_calls"], 2)
        self.assertEqual(summary["total_sql_attempts"], 4)
        self.assertEqual(summary["sql_execution_errors"], 2)
        self.assertEqual(summary["cases_with_sql_errors"], 2)
        self.assertEqual(summary["recovered_after_sql_error"], 1)
        self.assertEqual(summary["get_schema_usage_count"], 2)
        self.assertEqual(summary["describe_table_usage_count"], 2)

    def test_database_helper_uses_read_only_transaction_and_timeout(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = ("analyst_agent",)
        cursor.description = [MagicMock(name="count")]
        cursor.description[0].name = "count"
        cursor.fetchmany.return_value = [(300,)]
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        with patch("eval.runner.get_connection", return_value=connection):
            actual = execute_read_only_sql("SELECT COUNT(*) FROM orders")
        self.assertEqual(actual, {"columns": ["count"], "rows": [[300]]})
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(statements[0], "SET TRANSACTION READ ONLY")
        self.assertTrue(any("statement_timeout" in statement for statement in statements))
        self.assertEqual(statements[-1], "SELECT COUNT(*) FROM orders")

        with patch("eval.runner.get_connection") as connect:
            with self.assertRaises(ValueError):
                execute_read_only_sql("DELETE FROM orders")
            connect.assert_not_called()


class SelectionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tracing = patch("app.trace.RUNS_DIR", Path(directory.name))
        tracing.start()
        self.addCleanup(tracing.stop)

    @staticmethod
    def call(call_id, query, columns, rows):
        body = "\n".join(" | ".join(map(str, row)) for row in rows) or "(no rows)"
        return ToolCallRecord("execute_sql", {"query": query},
                              "COLUMNS:\n" + " | ".join(columns) + "\n\nROWS:\n" + body +
                              "\n\nRows returned: " + str(len(rows)), call_id)

    def test_city_and_amount_beat_status_lookup_in_either_call_order(self):
        status = self.call("status", "SELECT status FROM orders GROUP BY status",
                           ["status"], [["Completed"], ["Returned"], ["Cancelled"]])
        revenue = self.call("revenue", "SELECT city, SUM(revenue) FROM orders GROUP BY city",
                            ["city", "revenue"], [["West", "8299.845"], ["East", "7929.67"], ["North", 5000]])
        answer = "West generated the most revenue from completed orders: 8,299.85."
        for calls in ([status, revenue], [revenue, status]):
            for _ in range(3):
                selected, _ = select_answer_sql(calls, answer, "Which city generated the most revenue?")
                self.assertEqual(selected.tool_call_id, "revenue")

    def test_duplicate_cells_and_extra_unmentioned_rows_do_not_add_support(self):
        answer = self.call("answer", "SELECT city, SUM(revenue) FROM orders GROUP BY city",
                           ["city", "revenue"], [["West", 20]])
        repeated = self.call("lookup", "SELECT status FROM orders", ["status"], [["Completed"]] * 50)
        selected, _ = select_answer_sql([answer, repeated], "West: 20 from completed orders.")
        self.assertEqual(selected.tool_call_id, "answer")
        larger = self.call("larger", answer.arguments['query'], ['city', 'revenue'],
                           [['West', 20], ['Other', 15], ['Third', 10]])
        selected, _ = select_answer_sql([larger, answer], "West: 20 from completed orders.")
        self.assertEqual(selected.tool_call_id, "answer")  # Equal evidence; recency, not size.

    def test_plausible_analytical_queries_use_question_overlap_before_recency(self):
        completed = self.call("completed", "SELECT city, revenue FROM orders WHERE status = 'Completed'",
                              ["city", "revenue"], [["West", 20]])
        all_orders = self.call("all", "SELECT city, revenue FROM orders", ["city", "revenue"], [["West", 20]])
        selected, _ = select_answer_sql([completed, all_orders], "West: 20.",
                                        "Which city has the most revenue from completed orders?")
        self.assertEqual(selected.tool_call_id, "completed")

    def test_selector_has_no_ground_truth_inputs_or_loader_access(self):
        self.assertEqual(list(inspect.signature(select_answer_sql).parameters),
                         ['tool_calls', 'final_answer', 'question'])
        call = self.call("answer", "SELECT COUNT(*) FROM orders", ["count"], [[300]])
        with patch("eval.runner.load_cases", side_effect=AssertionError("No case lookup")), \
             patch("eval.runner.get_connection", side_effect=AssertionError("No database")), \
             patch("app.agent.get_llm", side_effect=AssertionError("No LLM")):
            selected, _ = select_answer_sql([call], "300 orders.", "How many orders?")
        self.assertEqual(selected.tool_call_id, "answer")

    def test_empty_answer_support_is_not_a_sql_error(self):
        empty = self.call("empty", "SELECT product_name FROM products WHERE NOT EXISTS (SELECT 1)",
                          ["product_name"], [])
        lookup = self.call("lookup", "SELECT COUNT(*) FROM products", ["count"], [[12]])
        for answer in ("No matching products exist.", "Every product has a completed order; all 12 have one."):
            selected, _ = select_answer_sql([empty, lookup], answer,
                                            "Which products have never appeared on a completed order?")
            self.assertEqual(selected.tool_call_id, "empty")
        self.assertEqual(sql_metrics([empty], True)['sql_execution_errors'], 0)

    def test_rescore_does_not_attach_old_structured_result_to_new_selected_query(self):
        status = self.call("status", "SELECT status FROM orders", ["status"], [['Completed']])
        revenue = self.call("revenue", "SELECT city, revenue FROM orders", ['city', 'revenue'],
                            [['West', 20], ['East', 10]])
        from dataclasses import asdict
        case = {**CASE, 'expected_result': {'columns': ['city', 'revenue'], 'rows': [['West', 20]]},
                'comparison': {'row_match': 'prefix', 'row_order': 'strict'}}
        saved = {'summary': {'passed': 0, 'total_cases': 1}, 'cases': [{
            **{k: case[k] for k in ('question', 'category', 'difficulty', 'requires')},
            'case_id': case['id'], 'passed': False, 'final_answer': 'West: 20 from completed orders.',
            'generated_sql': status.arguments['query'], 'answer_sql_call_id': 'status',
            'actual_result': {'columns': ['status'], 'rows': [['Completed']]},
            'failure_reason': 'Column count mismatch.', 'tool_calls': [asdict(status), asdict(revenue)],
        }]}
        report = rescore_report(saved, [case])
        self.assertTrue(report['cases'][0]['passed'])
        self.assertEqual(report['cases'][0]['answer_sql_call_id'], 'revenue')
        self.assertEqual(report['cases'][0]['actual_result_source'], 'complete legacy tool observation')

    def test_rescore_does_not_manufacture_final_answers(self):
        from dataclasses import asdict
        correct = self.call('correct', 'SELECT COUNT(*) FROM orders', ['count'], [[300]])
        saved = {'summary': {'passed': 0, 'total_cases': 1}, 'cases': [{
            **{k: CASE[k] for k in ('question', 'category', 'difficulty', 'requires')},
            'case_id': CASE['id'], 'passed': False, 'final_answer': None,
            'generated_sql': correct.arguments['query'], 'actual_result': None,
            'failure_reason': 'Agent stopped after 8 model responses without a final answer.',
            'tool_calls': [asdict(correct)],
        }]}
        report = rescore_report(saved, [CASE])
        result = report['cases'][0]
        self.assertFalse(result['passed'])
        self.assertIsNone(result['final_answer'])
        self.assertIsNone(result['actual_result'])
        self.assertEqual(report['summary']['iteration_limit_failures'], 1)

    def test_support_query_does_not_replace_answer_query(self):
        def agent(question, *, trace, verbose):
            trace.tool_calls.extend([
                ToolCallRecord("execute_sql", {"query": "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE status='Completed'"},
                               "COLUMNS:\ncount\n\nROWS:\n20\n\nRows returned: 1", "answer"),
                ToolCallRecord("execute_sql", {"query": "SELECT status, COUNT(*) FROM orders GROUP BY status"},
                               "COLUMNS:\nstatus | count\n\nROWS:\nCompleted | 198\nReturned | 45\n\nRows returned: 2", "support"),
            ])
            return "20 different customers completed at least one order."

        executor = MagicMock(return_value={"columns": ["count"], "rows": [[20]]})
        result = evaluate_case({**CASE, "expected_result": 20}, agent, executor)
        self.assertTrue(result["passed"])
        self.assertEqual(result["answer_sql_call_id"], "answer")
        self.assertEqual(executor.call_args.args[0], "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE status='Completed'")
        self.assertEqual(result["sql_execution_errors"], 0)
        self.assertFalse(result["recovered_after_sql_error"])

    def test_selection_uses_answer_evidence_not_ground_truth(self):
        calls = [
            ToolCallRecord("execute_sql", {"query": "SELECT COUNT(*) FROM orders"},
                           "COLUMNS:\ncount\n\nROWS:\n300\n\nRows returned: 1", "earlier"),
            ToolCallRecord("execute_sql", {"query": "SELECT COUNT(*) FROM orders WHERE status='Completed'"},
                           "COLUMNS:\ncount\n\nROWS:\n198\n\nRows returned: 1", "later"),
        ]
        selected, _ = select_answer_sql(calls, "There are 198 orders.")
        self.assertEqual(selected.tool_call_id, "later")
        selected, _ = select_answer_sql(calls, "There are 300 orders.")
        self.assertEqual(selected.tool_call_id, "earlier")
        calls[1].result = calls[0].result
        for _ in range(2):
            selected, _ = select_answer_sql(calls, "There are 300 orders.")
            self.assertEqual(selected.tool_call_id, "later")

    def test_recovery_requires_error_then_success_then_pass(self):
        success = ToolCallRecord("execute_sql", {"query": "SELECT 1"}, "COLUMNS:\nvalue\n\nROWS:\n1", "ok")
        error = ToolCallRecord("execute_sql", {}, "SQL execution error: invalid SQL syntax.", "bad")
        rejection = ToolCallRecord("execute_sql", {}, "Query rejected: write not allowed.", "reject")
        invocation = ToolCallRecord("execute_sql", {}, "", "invalid", status="error", error_type="ValidationError")
        self.assertFalse(sql_metrics([success, error], True)["recovered_after_sql_error"])
        self.assertTrue(sql_metrics([error, success], True)["recovered_after_sql_error"])
        self.assertFalse(sql_metrics([error, success], False)["recovered_after_sql_error"])
        metrics = sql_metrics([success, success, rejection, invocation], True)
        self.assertEqual(metrics["sql_attempts"], 4)
        self.assertEqual(metrics["sql_execution_errors"], 0)
        self.assertEqual(metrics["sql_rejections"], 1)
        self.assertEqual(metrics["sql_invocation_errors"], 1)
        self.assertFalse(metrics["recovered_after_sql_error"])

    def test_local_rescore_preserves_input_and_uses_saved_supporting_result(self):
        saved = {"summary": {"passed": 0, "total_cases": 1}, "cases": [{
            "case_id": CASE["id"], "question": CASE["question"], "category": CASE["category"],
            "difficulty": CASE["difficulty"], "requires": CASE["requires"], "passed": False,
            "final_answer": "300 orders.", "failure_reason": "Column names differ.",
            "generated_sql": "SELECT DISTINCT status FROM orders",
            "actual_result": {"columns": ["status"], "rows": [["Completed"]]},
            "tool_calls": [
                {"name": "execute_sql", "arguments": {"query": "SELECT COUNT(*) FROM orders"},
                 "result": "COLUMNS:\ncount\n\nROWS:\n300\n\nRows returned: 1", "tool_call_id": "answer"},
                {"name": "execute_sql", "arguments": {"query": "SELECT DISTINCT status FROM orders"},
                 "result": "COLUMNS:\nstatus\n\nROWS:\nCompleted\n\nRows returned: 1", "tool_call_id": "support"},
            ],
        }]}
        before = json.dumps(saved, sort_keys=True)
        with patch("app.agent.get_llm", side_effect=AssertionError("No Gemini")), \
             patch("eval.runner.get_connection", side_effect=AssertionError("No PostgreSQL")):
            report = rescore_report(saved, [CASE])
        self.assertEqual(json.dumps(saved, sort_keys=True), before)
        self.assertTrue(report["cases"][0]["passed"])
        self.assertEqual(report["cases"][0]["actual_result_source"], "complete legacy tool observation")


if __name__ == "__main__":
    unittest.main()
