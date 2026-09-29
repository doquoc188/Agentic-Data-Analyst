"""PostgreSQL connection and a small connection check."""

import os

import psycopg
from dotenv import load_dotenv


def get_connection() -> psycopg.Connection:
    """Open a PostgreSQL connection using settings from the environment."""
    load_dotenv()
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing database settings: {', '.join(missing)}")

    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


if __name__ == "__main__":
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database();")
            database_name = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM sales;")
            sales_count = cursor.fetchone()[0]

    if not connection.closed:
        raise AssertionError("Database connection was not closed")
    if database_name != "agentic_analyst":
        raise AssertionError(f"Unexpected database: {database_name}")
    if sales_count != 300:
        raise AssertionError(f"Expected 300 sales rows, found {sales_count}")

    print("Database:", database_name)
    print("Sales rows:", sales_count)
