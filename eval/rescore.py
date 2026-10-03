"""Locally rescore saved evidence without calling Gemini or PostgreSQL."""

import argparse
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from app.agent import ToolCallRecord
from eval.runner import RESULT_PATH, compare_result, json_value, load_cases, summary_for
from eval.selection import observation_table, select_answer_sql, sql_metrics, tool_validation_metrics

RESCORE_PATH = RESULT_PATH.with_name("local_rescore.json")


def rescore_report(saved: dict, cases: list[dict]) -> dict:
    """Apply current contracts to the previous run; preserve its original evidence."""
    benchmark = {case["id"]: case for case in cases}
    results = []
    for old in saved["cases"]:
        case = benchmark[old["case_id"]]
        if "tool_calls" not in old:
            # New reports already associate structured actual_result with the
            # selected answer_sql_call_id. Bounded trace previews are not complete
            # tables and must never replace that evidence for rescoring.
            result = deepcopy(old)
            result.update(original_passed=old["passed"], original_generated_sql=old["generated_sql"],
                          original_question=old["question"], question=case["question"],
                          question_changed=old["question"] != case["question"],
                          comparison=case.get("comparison", {}))
            if old["final_answer"] is not None and old["actual_result"] is not None:
                result["passed"], result["failure_reason"] = compare_result(
                    old["actual_result"], case["expected_result"], case.get("numeric_tolerance", 0),
                    comparison=case.get("comparison"),
                )
                result["failure_type"] = None if result["passed"] else "result_mismatch"
                result["actual_result_source"] = "saved structured actual_result"
            else:
                result["passed"] = False
                result["failure_reason"] = old.get("failure_reason") or "Saved evidence is insufficient for structured rescoring."
                result["failure_type"] = old.get("failure_type") or "insufficient_saved_evidence"
            if not result["passed"]:
                result["recovered_after_sql_error"] = False
                result["recovered_after_tool_validation_error"] = False
            results.append(result)
            continue
        result = deepcopy(old)
        result.update({
            "original_passed": old["passed"],
            "original_generated_sql": old["generated_sql"],
            "original_question": old["question"],
            "question": case["question"],
            "question_changed": case["question"] != old["question"],
            "comparison": case.get("comparison", {}),
            "actual_result": None,
            "actual_result_source": None,
            "passed": False,
        })
        calls = [ToolCallRecord(**call) for call in old["tool_calls"]]
        selected, reason = select_answer_sql(calls, old["final_answer"], old["question"])
        result["generated_sql"] = selected.arguments.get("query") if selected else None
        result["answer_sql_call_id"] = selected.tool_call_id if selected else None
        result["sql_selection_reason"] = reason
        result["failure_reason"] = None
        result["failure_type"] = None
        if old["final_answer"] is None:
            result["failure_reason"] = old["failure_reason"] or "No recorded final answer."
            result["failure_type"] = old.get("failure_type")
            if result["failure_type"] is None:
                if result["failure_reason"].startswith("Agent stopped after "):
                    result["failure_type"] = "iteration_limit"
                elif "ChatGoogleGenerativeAIError" in result["failure_reason"]:
                    result["failure_type"] = "model_integration_error"
                elif "ToolInvocationError" in result["failure_reason"]:
                    result["failure_type"] = "tool_invocation_error"
                else:
                    result["failure_type"] = "agent_error"
        elif selected is None:
            result["failure_reason"] = reason
            result["failure_type"] = "evaluator_selection_failure"
        else:
            try:
                # Structured data belongs to the originally scored query, not
                # necessarily the last successful call in a multi-query trace.
                original_id = old.get("answer_sql_call_id")
                same_query = (
                    selected.tool_call_id == original_id if original_id is not None
                    else selected.arguments.get("query") == old.get("generated_sql")
                )
                if same_query and old["actual_result"] is not None:
                    actual = old["actual_result"]
                    result["actual_result_source"] = "saved structured actual_result"
                else:
                    actual = observation_table(selected.result, require_complete=True)
                    result["actual_result_source"] = "complete legacy tool observation"
                result["actual_result"] = json_value(actual)
                result["passed"], result["failure_reason"] = compare_result(
                    actual, case["expected_result"], case.get("numeric_tolerance", 0),
                    comparison=case.get("comparison"),
                )
                if not result["passed"]:
                    result["failure_type"] = "result_mismatch"
            except (ValueError, KeyError):
                result["failure_reason"] = "Saved evidence is insufficient for structured rescoring."
                result["failure_type"] = "insufficient_saved_evidence"
        result.update(sql_metrics(calls, result["passed"]))
        result.update(tool_validation_metrics(calls, result["passed"]))
        result["sql_errors"] = result["sql_execution_errors"]
        result["tool_calls"] = [asdict(call) for call in calls]
        results.append(result)
    return {
        "run_type": "offline rescore of the previous live run",
        "note": "Uses current contracts on recorded answers only. No new model responses or database results are produced; question_changed identifies changed questions.",
        "original_summary": saved["summary"],
        "summary": summary_for(results),
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rescore saved evidence without Gemini or database calls.")
    parser.add_argument("--input", type=Path, default=RESULT_PATH)
    args = parser.parse_args()
    report = rescore_report(json.loads(args.input.read_text(encoding="utf-8")), load_cases())
    RESCORE_PATH.parent.mkdir(exist_ok=True)
    RESCORE_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(report["run_type"])
    print(f"Original: {report['original_summary']['passed']} / {report['original_summary']['total_cases']}")
    print(f"Recalibrated: {report['summary']['passed']} / {report['summary']['total_cases']} "
          f"({report['summary']['pass_rate']:.2f}%)")
    for result in report["cases"]:
        if not result["original_passed"] and result["passed"]:
            print("FAIL -> PASS:", result["case_id"])
        elif not result["passed"]:
            print("STILL FAILED:", result["case_id"], "-", result["failure_reason"])
    print("Offline only; changed questions, if any, have not been run again. Results:", RESCORE_PATH)


if __name__ == "__main__":
    main()
