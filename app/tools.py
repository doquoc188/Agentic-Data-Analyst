"""Standalone tools for the project."""

from langchain.tools import tool

from app.database import get_connection


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
                SELECT kcu.column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_catalog = kcu.constraint_catalog
                 AND tc.constraint_schema = kcu.constraint_schema
                 AND tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                 AND tc.table_name = kcu.table_name
                WHERE tc.table_schema = 'public'
                  AND tc.table_name = %s
                  AND tc.constraint_type = 'PRIMARY KEY'
                ORDER BY kcu.ordinal_position;
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
