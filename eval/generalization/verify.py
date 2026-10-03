"""Verify the SaaS database and derive evaluator ground truth without Gemini."""

import argparse
import json
from pathlib import Path

from app.database import get_connection
from app.tools import MAX_ROWS, STATEMENT_TIMEOUT_MS, _query_validation_error, describe_table, get_schema
from eval.runner import compare_result, json_value, load_cases

DATABASE_NAME = "agentic_analyst_saas"
CASE_PATH = Path(__file__).with_name("cases.json")
TABLE_COUNTS = {"accounts": 60, "plans": 4, "subscriptions": 120,
                "invoices": 360, "support_tickets": 240}


def execute_verification_sql(query: str, parameters=None) -> dict:
    """Run evaluator SQL directly in a bounded, read-only Psycopg transaction."""
    error = _query_validation_error(query)
    if error:
        raise ValueError(error)
    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Transaction control belongs here, never in an agent tool call.
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SELECT set_config('statement_timeout', %s, true)",
                               (str(STATEMENT_TIMEOUT_MS),))
                cursor.execute("SELECT current_user, current_database()")
                role, database = cursor.fetchone()
                if role != "analyst_agent":
                    raise ValueError("Verification requires the analyst_agent database role.")
                if database != DATABASE_NAME:
                    raise ValueError("Set process DB_NAME=agentic_analyst_saas; leave .env unchanged.")
                cursor.execute(query, parameters)
                if cursor.description is None:
                    raise ValueError("Verification query returned no columns.")
                columns = [column.name for column in cursor.description]
                rows = cursor.fetchmany(MAX_ROWS + 1)
                if len(rows) > MAX_ROWS:
                    raise ValueError(f"Verification query returned more than {MAX_ROWS} rows.")
        finally:
            # End even successful verification transactions without committing.
            connection.rollback()
    return {"columns": columns, "rows": [list(row) for row in rows]}


def require_database(query_executor=execute_verification_sql) -> None:
    """Fail before an evaluation can accidentally use the sales database."""
    result = query_executor("SELECT current_database() AS database_name")
    if result["rows"] != [[DATABASE_NAME]]:
        raise ValueError("Set process DB_NAME=agentic_analyst_saas; leave .env unchanged.")


def verify_database() -> dict:
    """Check the fixture, effective privileges, and the existing discovery tools."""
    require_database()  # The verifier executor also requires analyst_agent.
    counts = {}
    for table, expected in TABLE_COUNTS.items():
        # Identifiers here are fixed fixture names, never user input.
        count = execute_verification_sql(f"SELECT COUNT(*) FROM public.{table}")["rows"][0][0]
        if count != expected:
            raise ValueError(f"Unexpected fixture row count for {table}.")
        counts[table] = count

    constraints = execute_verification_sql("""
        SELECT c.relname AS table_name, con.contype AS constraint_type,
               pg_get_constraintdef(con.oid) AS definition
        FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND con.contype IN ('p', 'f')
        ORDER BY c.relname, con.conname
    """)["rows"]
    primary_tables = {table for table, kind, _ in constraints if kind == "p"}
    foreign_keys = {(table, definition) for table, kind, definition in constraints if kind == "f"}
    expected_foreign_keys = {
        ("subscriptions", "FOREIGN KEY (account_id) REFERENCES accounts(account_id)"),
        ("subscriptions", "FOREIGN KEY (plan_id) REFERENCES plans(plan_id)"),
        ("invoices", "FOREIGN KEY (subscription_id) REFERENCES subscriptions(subscription_id)"),
        ("support_tickets", "FOREIGN KEY (account_id) REFERENCES accounts(account_id)"),
    }
    if primary_tables != set(TABLE_COUNTS) or foreign_keys != expected_foreign_keys:
        raise ValueError("The fixture PK/FK constraints do not match its design.")

    privileges = execute_verification_sql("""
        SELECT table_name,
               has_table_privilege(current_user, 'public.' || table_name, %s),
               has_table_privilege(current_user, 'public.' || table_name, %s),
               has_table_privilege(current_user, 'public.' || table_name, %s),
               has_table_privilege(current_user, 'public.' || table_name, %s),
               has_table_privilege(current_user, 'public.' || table_name, %s)
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """, ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"))["rows"]
    if {row[0] for row in privileges} != set(TABLE_COUNTS) or any(
        row[1:] != [True, False, False, False, False] for row in privileges
    ):
        raise ValueError("analyst_agent must have SELECT and no table write privileges.")
    permissions = execute_verification_sql("""
        SELECT has_database_privilege(current_user, current_database(), %s),
               has_database_privilege(current_user, current_database(), %s),
               has_database_privilege(current_user, current_database(), %s),
               has_schema_privilege(current_user, 'public', %s),
               has_schema_privilege(current_user, 'public', %s)
    """, ("CONNECT", "CREATE", "TEMPORARY", "USAGE", "CREATE"))["rows"]
    if permissions != [[True, False, False, True, False]]:
        raise ValueError("Unexpected effective database/schema privileges.")

    schema = get_schema.invoke({})
    for table in TABLE_COUNTS:
        if f"TABLE: {table}\n" not in schema:
            raise ValueError(f"get_schema did not discover {table}.")
    descriptions = {table: describe_table.invoke({"table_name": table}) for table in TABLE_COUNTS}
    for table, text in {
        "plans": "seats multiplied by monthly_price_per_seat",
        "subscriptions": "active contributes seats and MRR",
        "invoices": "sum of amount for paid invoices",
        "support_tickets": "closed_at minus opened_at",
    }.items():
        if text not in descriptions[table]:
            raise ValueError(f"Semantic comments are missing from describe_table({table}).")
    for table, target in (("subscriptions", "REFERENCES accounts(account_id)"),
                          ("subscriptions", "REFERENCES plans(plan_id)"),
                          ("invoices", "REFERENCES subscriptions(subscription_id)"),
                          ("support_tickets", "REFERENCES accounts(account_id)")):
        if target not in descriptions[table]:
            raise ValueError(f"describe_table did not expose the FK from {table}.")
    return {"database": DATABASE_NAME, "counts": counts, "constraints": constraints,
            "schema": schema, "descriptions": descriptions, "read_only_privileges": True}


def derive_expected_results(cases: list[dict], query_executor=execute_verification_sql) -> list[dict]:
    """Derive all answers from successful read-only reference queries, in memory."""
    verified = []
    for case in cases:
        error = _query_validation_error(case["reference_sql"])
        if error:
            raise ValueError(f"Invalid reference_sql for {case['id']}: {error}")
        actual = query_executor(case["reference_sql"])
        if actual["columns"] != case["expected_columns"]:
            raise ValueError(f"Reference output columns differ for {case['id']}.")
        if case["comparison"]["result_type"] == "scalar":
            if len(actual["columns"]) != 1 or len(actual["rows"]) != 1 or len(actual["rows"][0]) != 1:
                raise ValueError(f"Reference must produce a scalar for {case['id']}.")
            expected = json_value(actual["rows"][0][0])
        else:
            expected = json_value(actual)
        matched, _ = compare_result(actual, expected, comparison=case["comparison"])
        if not matched:
            raise ValueError(f"Reference comparison failed for {case['id']}.")
        if "zero_result" in case["requires"] and actual["rows"]:
            raise ValueError(f"The zero-result fixture condition failed for {case['id']}.")
        verified.append({**case, "expected_result": expected, "ground_truth_verified": True})
    return verified


def save_verified_cases(cases: list[dict], path: Path = CASE_PATH) -> None:
    """Publish only a complete verified suite; leave the old file on failure."""
    if not cases or not all(case.get("ground_truth_verified") is True for case in cases):
        raise ValueError("Refusing to save unverified ground truth.")
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-expected", action="store_true",
                        help="Store ground truth only after all database checks succeed")
    args = parser.parse_args()
    try:
        report = verify_database()
        cases = load_cases(CASE_PATH)
        verified = derive_expected_results(cases)
        if args.write_expected:
            save_verified_cases(verified)
        else:
            for stored, fresh in zip(cases, verified):
                if stored.get("ground_truth_verified") is not True:
                    raise ValueError("Ground truth is pending; use --write-expected after migration.")
                actual = fresh["expected_result"]
                if fresh["comparison"]["result_type"] == "scalar":
                    actual = {"columns": fresh["expected_columns"], "rows": [[actual]]}
                if not compare_result(actual, stored["expected_result"], comparison=stored["comparison"])[0]:
                    raise ValueError(f"Stored ground truth differs for {stored['id']}.")
        print(json.dumps(json_value(report), indent=2))
        print(f"Verified {len(verified)} reference queries as analyst_agent; no Gemini calls.")
    except ValueError as exc:
        parser.exit(1, f"Verification stopped: {exc}\n")
    except Exception as exc:
        parser.exit(1, f"Verification stopped ({type(exc).__name__}); no credentials logged.\n")


if __name__ == "__main__":
    main()
