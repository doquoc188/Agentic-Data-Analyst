"""Run the separate unseen-database suite using the existing evaluator."""

import argparse
import json
from pathlib import Path

from app.agent import run_agent
from eval.generalization.verify import CASE_PATH, require_database
from eval.runner import execute_read_only_sql, load_cases, run_evaluation

RESULT_PATH = Path(__file__).parent / "generalization" / "results" / "latest.json"


def run_generalization(cases: list[dict], agent_run=run_agent,
                       query_executor=execute_read_only_sql) -> dict:
    """Check readiness and database identity before passing questions to Gemini."""
    if not cases or any(case.get("ground_truth_verified") is not True or
                        case.get("expected_result") is None for case in cases):
        raise ValueError("Generalization ground truth is pending. Run the verification utility first.")
    require_database(query_executor)
    return run_evaluation(cases, agent_run, query_executor, trace_source="eval_generalization")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Run one case by ID")
    parser.add_argument("--limit", type=int, help="Run only the first N selected cases")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    cases = load_cases(CASE_PATH)
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error(f"Unknown case ID: {args.case}")
    if args.limit:
        cases = cases[:args.limit]
    try:
        report = run_generalization(cases)
    except ValueError as exc:
        parser.exit(1, f"Evaluation stopped: {exc}\n")
    except Exception as exc:
        parser.exit(1, f"Evaluation stopped ({type(exc).__name__}); no credentials logged.\n")
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(f"Generalization complete: {summary['passed']} / {summary['total_cases']} passed "
          f"({summary['pass_rate']:.2f}%).")
    print(f"Results: {RESULT_PATH}")


if __name__ == "__main__":
    main()
