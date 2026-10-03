"""Run benchmark questions and compare the agent's SQL result with ground truth."""

import argparse
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.agent import AgentTrace, ModelIntegrationError, ToolCallRecord, ToolInvocationError, run_agent, safe_trace_value
from app.database import get_connection
from app.tools import MAX_ROWS, STATEMENT_TIMEOUT_MS, _query_validation_error
from eval.selection import select_answer_sql, sql_metrics, tool_validation_metrics

CASE_PATH = Path(__file__).with_name("cases.json")
RESULT_PATH = Path(__file__).parent / "results" / "latest.json"


def load_cases(path: Path = CASE_PATH) -> list[dict]:
    """Load the fixed benchmark without passing its answers to the agent."""
    return json.loads(path.read_text(encoding="utf-8"))


def execute_read_only_sql(query: str) -> dict:
    """Re-run the agent's SQL as structured data, under the analyst role."""
    error = _query_validation_error(query)
    if error:
        raise ValueError(error)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_user")
            if cursor.fetchone()[0] != "analyst_agent":
                raise RuntimeError("Evaluation requires the analyst_agent database role.")
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(STATEMENT_TIMEOUT_MS),),
            )
            cursor.execute(query)
            if cursor.description is None:
                raise ValueError("Generated SQL returned no columns.")
            columns = [column.name for column in cursor.description]
            rows = cursor.fetchmany(MAX_ROWS + 1)
            if len(rows) > MAX_ROWS:
                raise ValueError(f"Generated SQL returned more than {MAX_ROWS} rows.")
    return {"columns": columns, "rows": [list(row) for row in rows]}


def month_value(value):
    """Normalize only when a case explicitly requests month granularity."""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m")
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}", value):
            try:
                return datetime.strptime(value, "%Y-%m").strftime("%Y-%m")
            except ValueError:
                return value
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m")
        except ValueError:
            pass
    return value


def values_match(actual, expected, tolerance=0, time_granularity=None) -> bool:
    """Compare typed cells; decimal and JSON numbers use decimal arithmetic."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (Decimal, int, float)) and isinstance(expected, (Decimal, int, float)):
        return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal(str(tolerance))
    if time_granularity == "month":
        actual, expected = month_value(actual), month_value(expected)
    elif isinstance(actual, (date, datetime)):
        actual = actual.isoformat()
    return type(actual) is type(expected) and actual == expected


def compare_result(actual: dict, expected, tolerance=0, *, comparison=None) -> tuple[bool, str | None]:
    """Compare requested fields using an explicit contract; defaults are strict."""
    contract = comparison or {}
    result_type = contract.get("result_type", "table" if isinstance(expected, dict) else "scalar")
    column_matching = contract.get("column_matching", "strict")
    row_order = contract.get("row_order", "strict")
    row_match = contract.get("row_match", "exact")
    tolerance = contract.get("numeric_tolerance", tolerance)
    granularity = contract.get("time_granularity")
    if result_type not in {"scalar", "table"} or column_matching not in {"strict", "position"}:
        raise ValueError("Invalid comparison contract.")
    if row_order not in {"strict", "unordered"} or granularity not in {None, "month"}:
        raise ValueError("Invalid comparison contract.")
    if row_match not in {"exact", "prefix"}:
        raise ValueError("Invalid row_match in comparison contract.")
    if row_match == "prefix" and (result_type != "table" or row_order != "strict"):
        raise ValueError("Prefix matching requires a strictly ordered table.")
    if not Decimal(str(tolerance)).is_finite() or Decimal(str(tolerance)) < 0:
        raise ValueError("Numeric tolerance must be finite and nonnegative.")
    support = contract.get("scalar_support")
    if "scalar_support" in contract:
        if (
            result_type != "scalar" or not isinstance(support, dict)
            or set(support) != {"label_column", "label_value", "value_column"}
            or any(not isinstance(support[key], str) or not support[key]
                   for key in ("label_column", "value_column"))
            or support["label_column"] == support["value_column"]
            or isinstance(support["label_value"], (dict, list))
        ):
            raise ValueError("Invalid scalar_support in comparison contract.")
    columns, rows = actual["columns"], actual["rows"]
    if result_type == "table":
        if not isinstance(expected, dict):
            raise ValueError("Table comparison requires a table expectation.")
        expected_columns = expected["columns"]
        required = contract.get("required_columns", expected_columns)
        if not required or len(set(required)) != len(required) or not set(required) <= set(expected_columns):
            raise ValueError("Invalid required_columns in comparison contract.")
        expected_indices = [expected_columns.index(column) for column in required]
        if "required_columns" in contract:
            if len(set(columns)) != len(columns):
                return False, "Ambiguous duplicate column names."
            if set(required) <= set(columns):
                # Resolve each side independently, including extra/reordered fields.
                actual_indices = [columns.index(column) for column in required]
            elif column_matching == "strict":
                return False, "Missing requested business column."
            else:
                # Aliases remain allowed only in a declared positional layout.
                layout = None
                if len(columns) == len(required):
                    layout = required
                elif len(columns) == len(expected_columns) and all(
                    columns[i] == name for i, name in enumerate(expected_columns)
                    if name not in required
                ):
                    layout = expected_columns
                if layout is None or any(
                    name in expected_columns and name != layout[i]
                    for i, name in enumerate(columns)
                ):
                    return False, "Missing or ambiguous requested business column."
                actual_indices = [layout.index(column) for column in required]
        elif column_matching == "strict":
            if columns != expected_columns:
                return False, "Column names differ from expected result."
            actual_indices = expected_indices
        elif len(columns) == len(expected_columns):
            actual_indices = expected_indices
        else:
            return False, "Requested business column count differs from expected result."
        if any(len(row) != len(columns) for row in rows):
            return False, "Actual row width differs from its columns."
        rows = [[row[i] for i in actual_indices] for row in rows]
        expected_rows = [[row[i] for i in expected_indices] for row in expected["rows"]]
        if row_match == "prefix":
            if not expected_rows:
                raise ValueError("An empty expectation cannot use prefix matching.")
            if len(rows) < len(expected_rows):
                return False, "Too few rows for the expected ranking prefix."
            rows = rows[:len(expected_rows)]
        if len(rows) != len(expected_rows):
            return False, "Row count differs from expected result."

        def row_matches(row, other):
            return all(values_match(a, b, tolerance, granularity) for a, b in zip(row, other))

        if row_order == "unordered":
            # Match each occurrence once, including duplicate rows and numeric tolerance.
            assigned = {}

            def assign(row_index, seen):
                for index, other in enumerate(expected_rows):
                    if index not in seen and row_matches(rows[row_index], other):
                        seen.add(index)
                        if index not in assigned or assign(assigned[index], seen):
                            assigned[index] = row_index
                            return True
                return False

            if not all(assign(index, set()) for index in range(len(rows))):
                return False, "Unordered rows differ from expected result."
            return True, None
        for row_index, (row, expected_row) in enumerate(zip(rows, expected_rows), 1):
            if not row_matches(row, expected_row):
                return False, f"Row {row_index} differs from expected result."
        return True, None
    if isinstance(expected, dict):
        raise ValueError("Scalar comparison requires a scalar expectation.")
    if len(columns) == 1 and len(rows) == 1 and len(rows[0]) == 1:
        value = rows[0][0]
    elif support is not None:
        if any(len(row) != len(columns) for row in rows):
            return False, "Actual row width differs from its columns."
        if any(columns.count(support[key]) != 1 for key in ("label_column", "value_column")):
            return False, "Missing or ambiguous labeled-scalar column."
        label_index = columns.index(support["label_column"])
        value_index = columns.index(support["value_column"])
        matching = [row for row in rows if values_match(row[label_index], support["label_value"])]
        if len(matching) != 1:
            return False, "Expected exactly one matching labeled-scalar row."
        value = matching[0][value_index]
    else:
        return False, "Expected one row and one column."
    if not values_match(value, expected, tolerance, granularity):
        return False, "Scalar value differs from expected result."
    return True, None


def json_value(value):
    """Convert PostgreSQL values for a readable JSON artifact."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def evaluate_case(case: dict, agent_run=run_agent, query_executor=execute_read_only_sql,
                  *, trace_source: str = "eval") -> dict:
    """Run one question; only its question text crosses into the agent."""
    started = time.perf_counter()
    trace = AgentTrace(source=trace_source, case_id=case["id"], question=case["question"])
    outcome = {
        "case_id": case["id"],
        "question": case["question"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "requires": case["requires"],
        "passed": False,
        "final_answer": None,
        "generated_sql": None,
        "answer_sql_call_id": None,
        "sql_selection_reason": None,
        "comparison": case.get("comparison", {}),
        "sql_attempts": 0,
        "sql_errors": 0,
        "run_id": None,
        "trace_path": None,
        "model_turns": 0,
        "used_get_schema": False,
        "used_describe_table": False,
        "expected_result": case["expected_result"],
        "actual_result": None,
        "failure_reason": None,
        "failure_type": None,
        "model_error": None,
        "elapsed_seconds": 0.0,
    }
    try:
        # Ground truth stays in this evaluator; the agent receives question only.
        outcome["final_answer"] = safe_trace_value(agent_run(case["question"], trace=trace, verbose=False))
    except Exception as exc:
        if isinstance(exc, ModelIntegrationError):
            outcome["failure_type"] = "model_integration_error"
            outcome["model_error"] = safe_trace_value(exc.diagnostics)
            outcome["failure_reason"] = str(exc)
        elif isinstance(exc, ToolInvocationError):
            outcome["failure_type"] = "tool_invocation_error"
            outcome["failure_reason"] = "Agent failed (ToolInvocationError)."
        elif isinstance(exc, RuntimeError) and str(exc).startswith("Agent stopped after "):
            outcome["failure_type"] = "iteration_limit"
            outcome["failure_reason"] = str(exc)
        else:
            outcome["failure_type"] = "agent_error"
            outcome["failure_reason"] = f"Agent failed ({type(exc).__name__})."
    finally:
        # Fake/custom agents may not finalize themselves. Persist only their
        # observations here, before any evaluator result or comparison exists.
        if trace.finished_at is None:
            failure = outcome["failure_type"]
            status, reason = {
                "model_integration_error": ("model_error", "model_integration_error"),
                "tool_invocation_error": ("tool_error", "tool_runtime_error"),
                "iteration_limit": ("iteration_limit", "iteration_limit"),
                "agent_error": ("unexpected_error", "unexpected_error"),
            }.get(failure, ("success", "success"))
            trace.final_answer = outcome["final_answer"]
            trace.model_error = outcome["model_error"]
            trace.finish(status, reason)
            trace.persist()
        outcome["run_id"], outcome["trace_path"] = trace.run_id, trace.trace_path
        outcome["tool_call_count"] = len(trace.tool_calls)
        outcome["get_schema_calls"] = sum(call.name == "get_schema" for call in trace.tool_calls)
        outcome["describe_table_calls"] = sum(call.name == "describe_table" for call in trace.tool_calls)
        outcome["model_turns"] = trace.model_turns
        outcome["used_get_schema"] = any(call.name == "get_schema" for call in trace.tool_calls)
        outcome["used_describe_table"] = any(call.name == "describe_table" for call in trace.tool_calls)
        selected, reason = select_answer_sql(
            trace.tool_calls, outcome["final_answer"], case["question"]
        )
        outcome["sql_selection_reason"] = reason
        if selected:
            outcome["generated_sql"] = selected.arguments.get("query")
            outcome["answer_sql_call_id"] = selected.tool_call_id

    if outcome["failure_reason"] is None:
        if not outcome["generated_sql"]:
            outcome["failure_reason"] = "No successful execute_sql query was produced."
            if any(call.result.startswith("SQL execution error:") for call in trace.tool_calls):
                outcome["failure_type"] = "sql_execution_error"
            elif any(call.result.startswith("Query rejected:") for call in trace.tool_calls):
                outcome["failure_type"] = "sql_rejection"
            else:
                outcome["failure_type"] = "evaluator_selection_failure"
                outcome["failure_reason"] = outcome["sql_selection_reason"]
        else:
            try:
                actual = query_executor(outcome["generated_sql"])
                outcome["actual_result"] = json_value(actual)
                outcome["passed"], outcome["failure_reason"] = compare_result(
                    actual, case["expected_result"], case.get("numeric_tolerance", 0),
                    comparison=case.get("comparison"),
                )
                if not outcome["passed"]:
                    outcome["failure_type"] = "result_mismatch"
            except Exception as exc:
                # Do not write raw connection errors: they can contain configuration.
                outcome["failure_reason"] = f"Evaluator SQL failed ({type(exc).__name__})."
                outcome["failure_type"] = "evaluator_sql_error"

    outcome.update(sql_metrics(trace.tool_calls, outcome["passed"]))
    outcome.update(tool_validation_metrics(trace.tool_calls, outcome["passed"]))
    outcome["sql_errors"] = outcome["sql_execution_errors"]  # Compatibility for older consumers.
    outcome["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return outcome


def summary_for(results: list[dict]) -> dict:
    """Aggregate deterministic counts and pass rates for completed cases."""
    def score(items):
        total = len(items)
        passed = sum(item["passed"] for item in items)
        return {"total": total, "passed": passed,
                "pass_rate": round(100 * passed / total, 2) if total else 0.0}

    def grouped(field):
        groups = {}
        for item in results:
            keys = item[field] if field == "requires" else [item[field]]
            for key in keys:
                groups.setdefault(key, []).append(item)
        return {key: score(groups[key]) for key in sorted(groups)}

    total = len(results)
    overall = score(results)
    # Keep older saved reports readable without duplicating new run traces.
    validation = [tool_validation_metrics(
        [ToolCallRecord(**call) for call in result["tool_calls"]], result["passed"]
    ) if "tool_calls" in result else result for result in results]
    def call_count(result, name=None):
        if "tool_calls" in result:
            return sum(name is None or call["name"] == name for call in result["tool_calls"])
        return result["tool_call_count" if name is None else
                      "get_schema_calls" if name == "get_schema" else "describe_table_calls"]
    return {
        "total_cases": total,
        "passed": overall["passed"],
        "failed": total - overall["passed"],
        "pass_rate": overall["pass_rate"],
        "by_difficulty": grouped("difficulty"),
        "by_category": grouped("category"),
        "by_capability": grouped("requires"),
        "average_tool_calls": round(sum(call_count(r) for r in results) / total, 2) if total else 0.0,
        "average_sql_attempts": round(sum(r["sql_attempts"] for r in results) / total, 2) if total else 0.0,
        "total_sql_attempts": sum(r["sql_attempts"] for r in results),
        "cases_with_multiple_sql_calls": sum(r["sql_attempts"] > 1 for r in results),
        "sql_execution_errors": sum(r["sql_execution_errors"] for r in results),
        "cases_with_sql_errors": sum(r["sql_execution_errors"] > 0 for r in results),
        "sql_invocation_errors": sum(r["sql_invocation_errors"] for r in results),
        "sql_rejections": sum(r["sql_rejections"] for r in results),
        "recovered_after_sql_error": sum(r["recovered_after_sql_error"] for r in results),
        "iteration_limit_failures": sum(r.get("failure_type") == "iteration_limit" for r in results),
        "model_integration_errors": sum(r.get("failure_type") == "model_integration_error" for r in results),
        "tool_invocation_errors": sum(item["tool_invocation_errors"] for item in validation),
        "tool_argument_validation_errors": sum(item["tool_argument_validation_errors"] for item in validation),
        "cases_recovered_after_tool_validation_error": sum(
            item["recovered_after_tool_validation_error"] for item in validation
        ),
        "evaluator_selection_failures": sum(
            r.get("failure_type") == "evaluator_selection_failure" for r in results
        ),
        "get_schema_usage_count": sum(call_count(r, "get_schema") for r in results),
        "describe_table_usage_count": sum(call_count(r, "describe_table") for r in results),
    }


def run_evaluation(cases: list[dict], agent_run=run_agent, query_executor=execute_read_only_sql,
                   *, trace_source: str = "eval") -> dict:
    results = [evaluate_case(case, agent_run, query_executor, trace_source=trace_source)
               for case in cases]
    return {"summary": summary_for(results), "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the manual data analyst agent.")
    parser.add_argument("--case", help="Run one case by ID")
    parser.add_argument("--limit", type=int, help="Run only the first N selected cases")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    cases = load_cases()
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error(f"Unknown case ID: {args.case}")
    if args.limit:
        cases = cases[:args.limit]

    report = run_evaluation(cases)
    RESULT_PATH.parent.mkdir(exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(f"Evaluation complete: {summary['passed']} / {summary['total_cases']} passed "
          f"({summary['pass_rate']:.2f}%).")
    print(f"Cases with multiple SQL calls: {summary['cases_with_multiple_sql_calls']}; "
          f"SQL execution errors: {summary['sql_execution_errors']}; "
          f"recovered after error: {summary['recovered_after_sql_error']}.")
    print(f"Tool argument validation errors: {summary['tool_argument_validation_errors']}; "
          f"cases recovered: {summary['cases_recovered_after_tool_validation_error']}.")
    print(f"Results: {RESULT_PATH}")


if __name__ == "__main__":
    main()
