"""Verify frozen Olist reference results directly, without calling Gemini."""

import argparse
from pathlib import Path

from app.config import get_settings
from app.database import database_context, get_connection
from app.profiles import resolve_profile
from app.tools import MAX_ROWS, STATEMENT_TIMEOUT_MS, _query_validation_error
from eval.generalization.verify import derive_expected_results, save_verified_cases
from eval.runner import compare_result, load_cases

DATABASE_NAME = "agentic_analyst_olist"
CASE_PATH = Path(__file__).with_name("cases.json")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def require_local_olist_target():
    """Resolve the fixed local fixture and reject remote benchmark targets."""
    settings = get_settings()
    target = resolve_profile("olist", settings)
    if target.url or settings.db_host.strip().casefold() not in LOCAL_HOSTS:
        raise ValueError("Olist benchmark verification requires the local database.")
    if settings.db_user != "analyst_agent":
        raise ValueError("Olist benchmark verification requires analyst_agent.")
    return target


def execute_verification_sql(query: str, parameters=None) -> dict:
    """Run one reference query in a bounded, read-only local transaction."""
    error = _query_validation_error(query)
    if error:
        raise ValueError(error)
    target = require_local_olist_target()
    with database_context(target.database_name):
        with get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(STATEMENT_TIMEOUT_MS),),
                    )
                    cursor.execute("SELECT current_user, current_database()")
                    role, database = cursor.fetchone()
                    if role != "analyst_agent" or database != DATABASE_NAME:
                        raise ValueError(
                            "Verification requires analyst_agent on agentic_analyst_olist."
                        )
                    cursor.execute(query, parameters)
                    if cursor.description is None:
                        raise ValueError("Verification query returned no columns.")
                    columns = [column.name for column in cursor.description]
                    rows = cursor.fetchmany(MAX_ROWS + 1)
                    if len(rows) > MAX_ROWS:
                        raise ValueError(
                            f"Verification query returned more than {MAX_ROWS} rows."
                        )
            finally:
                connection.rollback()
    return {"columns": columns, "rows": [list(row) for row in rows]}


def require_database(query_executor=execute_verification_sql) -> None:
    """Stop before verification if the executor targets another database."""
    result = query_executor("SELECT current_database() AS database_name")
    if result["rows"] != [[DATABASE_NAME]]:
        raise ValueError("Olist verification requires agentic_analyst_olist.")


def verify_stored_results(query_executor=execute_verification_sql) -> list[dict]:
    """Recompute every reference answer and compare it with the frozen fixture."""
    require_database(query_executor)
    cases = load_cases(CASE_PATH)
    fresh_cases = derive_expected_results(cases, query_executor)
    for stored, fresh in zip(cases, fresh_cases):
        actual = fresh["expected_result"]
        if fresh["comparison"]["result_type"] == "scalar":
            actual = {"columns": fresh["expected_columns"], "rows": [[actual]]}
        matched, reason = compare_result(
            actual,
            stored["expected_result"],
            comparison=stored["comparison"],
        )
        if not matched:
            raise ValueError(f"Stored ground truth differs for {stored['id']}: {reason}")
    return fresh_cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-expected",
        action="store_true",
        help="Replace the fixture only after all local reference queries succeed",
    )
    args = parser.parse_args()
    try:
        if args.write_expected:
            require_database()
            verified = derive_expected_results(load_cases(CASE_PATH), execute_verification_sql)
            save_verified_cases(verified, CASE_PATH)
        else:
            verified = verify_stored_results()
        print(
            f"Verified {len(verified)} Olist reference queries as analyst_agent "
            "in read-only transactions; no Gemini calls."
        )
    except ValueError as exc:
        parser.exit(1, f"Verification stopped: {exc}\n")
    except Exception as exc:
        parser.exit(
            1,
            f"Verification stopped ({type(exc).__name__}); no credentials logged.\n",
        )


if __name__ == "__main__":
    main()
