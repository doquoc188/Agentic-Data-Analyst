"""Run the frozen local Olist suite using the existing deterministic evaluator."""

import argparse
import json
from pathlib import Path

from app.agent import run_agent
from app.database import database_context
from eval.olist.verify import CASE_PATH, require_database, require_local_olist_target
from eval.runner import execute_read_only_sql, load_cases, run_evaluation

RESULT_PATH = Path(__file__).parent / "olist" / "results" / "latest.json"


def run_olist(cases: list[dict], agent_run=None, query_executor=None) -> dict:
    """Run verified cases against the fixed local Olist profile."""
    if not cases or any(
        case.get("ground_truth_verified") is not True
        or case.get("expected_result") is None
        for case in cases
    ):
        raise ValueError("Olist ground truth is pending. Run the verifier first.")

    target = require_local_olist_target()

    def scoped_agent(question, **kwargs):
        return run_agent(
            question,
            database_name=target.database_name,
            database_url=target.url,
            **kwargs,
        )

    def scoped_query(query):
        with database_context(target.database_name, database_url=target.url):
            return execute_read_only_sql(query)

    selected_agent = agent_run or scoped_agent
    selected_query = query_executor or scoped_query
    require_database(selected_query)
    return run_evaluation(
        cases,
        selected_agent,
        selected_query,
        trace_source="eval_olist",
    )


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
        cases = cases[: args.limit]

    try:
        report = run_olist(cases)
    except ValueError as exc:
        parser.exit(1, f"Evaluation stopped: {exc}\n")
    except Exception as exc:
        parser.exit(
            1,
            f"Evaluation stopped ({type(exc).__name__}); no credentials logged.\n",
        )

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    print(
        f"Olist evaluation complete: {summary['passed']} / "
        f"{summary['total_cases']} passed ({summary['pass_rate']:.2f}%)."
    )
    print(f"Results: {RESULT_PATH}")


if __name__ == "__main__":
    main()
