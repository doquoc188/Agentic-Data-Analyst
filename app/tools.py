"""Standalone tools for the project."""

import re

import psycopg
from langchain.tools import tool

from app.database import get_connection

MAX_ROWS = 100
STATEMENT_TIMEOUT_MS = 5000


@tool("calculator")
def calculator(operation: str, a: float, b: float) -> float:
    """Calculate a + b, a - b, a * b, or a / b. Use operation: add, subtract, multiply, or divide."""
    if operation == "add":
        return a + b
    if operation == "subtract":
        return a - b
    if operation == "multiply":
        return a * b
    if operation == "divide":
        if b == 0:
            raise ZeroDivisionError("Cannot divide by zero.")
        return a / b
    raise ValueError(f"Unknown operation: {operation}")


@tool("get_schema")
def get_schema() -> str:
    """List public database tables with their columns and data types. Use before writing SQL for unfamiliar tables."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.table_name, c.column_name, c.data_type
                FROM information_schema.columns AS c
                JOIN information_schema.tables AS t
                  ON t.table_schema = c.table_schema
                 AND t.table_name = c.table_name
                WHERE c.table_schema = 'public'
                  AND t.table_type = 'BASE TABLE'
                ORDER BY c.table_name, c.ordinal_position;
                """
            )
            rows = cursor.fetchall()

    lines = []
    current_table = None
    for table_name, column_name, data_type in rows:
        if table_name != current_table:
            if lines:
                lines.append("")
            lines.append(f"TABLE: {table_name}")
            current_table = table_name
        lines.append(f"- {column_name}: {data_type}")

    return "\n".join(lines) if lines else "No tables found in public schema."


@tool("describe_table")
def describe_table(table_name: str) -> str:
    """Describe a public table's columns, defaults, primary key, and foreign keys."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                  AND table_name = %s;
                """,
                (table_name,),
            )
            if cursor.fetchone() is None:
                return f"Table '{table_name}' was not found in the public schema."

            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position;
                """,
                (table_name,),
            )
            columns = cursor.fetchall()

            cursor.execute(
                """
                SELECT attribute.attname
                FROM pg_catalog.pg_constraint AS con
                JOIN pg_catalog.pg_class AS rel ON rel.oid = con.conrelid
                JOIN pg_catalog.pg_namespace AS ns ON ns.oid = rel.relnamespace
                CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY
                    AS pk_column(attnum, position)
                JOIN pg_catalog.pg_attribute AS attribute
                  ON attribute.attrelid = rel.oid
                 AND attribute.attnum = pk_column.attnum
                WHERE ns.nspname = 'public'
                  AND rel.relname = %s
                  AND con.contype = 'p'
                ORDER BY pk_column.position;
                """,
                (table_name,),
            )
            primary_keys = [row[0] for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT pg_catalog.pg_get_constraintdef(con.oid)
                FROM pg_catalog.pg_constraint AS con
                JOIN pg_catalog.pg_class AS rel ON rel.oid = con.conrelid
                JOIN pg_catalog.pg_namespace AS ns ON ns.oid = rel.relnamespace
                WHERE ns.nspname = 'public'
                  AND rel.relname = %s
                  AND con.contype = 'f'
                ORDER BY con.conname;
                """,
                (table_name,),
            )
            foreign_keys = [row[0] for row in cursor.fetchall()]

    lines = [f"TABLE: {table_name}", "", "COLUMNS:"]
    for column_name, data_type, is_nullable, default in columns:
        null_status = "NULL" if is_nullable == "YES" else "NOT NULL"
        column = f"- {column_name}: {data_type}, {null_status}"
        if default is not None:
            column += f", DEFAULT {default}"
        lines.append(column)
    if not columns:
        lines.append("- None")

    lines.extend(["", "PRIMARY KEY:"])
    if primary_keys:
        lines.extend(f"- {name}" for name in primary_keys)
    else:
        lines.append("- None")

    lines.extend(["", "FOREIGN KEYS:"])
    if foreign_keys:
        lines.extend(f"- {key}" for key in foreign_keys)
    else:
        lines.append("- None")
    return "\n".join(lines)


def _query_validation_error(query: str) -> str | None:
    sql = query.strip()
    if not sql:
        return "Query rejected: SQL query is empty."

    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        return "Query rejected: SQL query is empty."
    if ";" in sql:
        return "Query rejected: only one SQL statement is allowed."

    first_word = sql.split(maxsplit=1)[0].upper()
    if first_word not in {"SELECT", "WITH"}:
        return "Query rejected: only SELECT or WITH queries are allowed."

    blocked = (
        r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|"
        r"REVOKE|COPY|CALL|DO|MERGE|INTO)\b"
    )
    if re.search(blocked, sql, re.IGNORECASE):
        return "Query rejected: write and DDL operations are not allowed."
    return None


@tool("execute_sql")
def execute_sql(query: str) -> str:
    """Run one read-only SELECT or WITH query and return at most 100 result rows."""
    validation_error = _query_validation_error(query)
    if validation_error:
        return validation_error

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(STATEMENT_TIMEOUT_MS),),
                )
                cursor.execute(query)
                if cursor.description is None:
                    return "SQL execution error: query returned no columns."
                columns = [column.name for column in cursor.description]
                if not columns:
                    return "SQL execution error: query returned no columns."
                rows = cursor.fetchmany(MAX_ROWS + 1)
    except psycopg.Error as exc:
        errors = {
            "42601": "invalid SQL syntax",
            "42P01": "table does not exist",
            "42703": "column does not exist",
            "57014": "query timed out or was cancelled",
        }
        detail = errors.get(exc.sqlstate, "PostgreSQL rejected the query")
        return f"SQL execution error: {detail}."

    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    row_lines = []
    for row in rows:
        values = ["NULL" if value is None else str(value).replace("\n", " ") for value in row]
        row_lines.append(" | ".join(values))
    if not row_lines:
        row_lines = ["(no rows)"]

    result = [
        "COLUMNS:",
        " | ".join(columns),
        "",
        "ROWS:",
        *row_lines,
        "",
        f"Rows returned: {len(rows)}",
    ]
    if truncated:
        result.append(f"Result truncated to {MAX_ROWS} rows.")
    return "\n".join(result)
