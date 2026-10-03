"""PostgreSQL connection and a small connection check."""

from contextlib import contextmanager
from contextvars import ContextVar

import psycopg

from app.config import get_settings, load_dotenv, validate_database_url
from app.profiles import DatabaseTarget


# ContextVar values belong to the current thread/task, not to other requests.
_database_target = ContextVar("database_target", default=None)


class DatabaseUnavailableError(RuntimeError):
    """A connection could not be opened; retain no connection error details."""


@contextmanager
def database_context(database_name: str | None, *, database_url: str | None = None):
    """Select a database for one run, restoring the previous context on exit."""
    token = _database_target.set(DatabaseTarget(database_name, database_url))
    try:
        yield
    finally:
        _database_target.reset(token)


def get_connection(database_name: str | None = None, *, database_url: str | None = None) -> psycopg.Connection:
    """Open the explicit/run destination, or the existing local .env database."""
    load_dotenv()
    target = (DatabaseTarget(database_name, database_url)
              if database_name is not None or database_url is not None
              else _database_target.get() or DatabaseTarget())
    try:
        if target.url:
            validate_database_url(target.url)
            # Psycopg preserves credentials and URL options such as sslmode.
            return psycopg.connect(target.url, connect_timeout=5)
        settings = get_settings(load_environment=False)
        return psycopg.connect(**settings.local_connection(target.database_name), connect_timeout=5)
    except psycopg.OperationalError:
        raise DatabaseUnavailableError("The database is unavailable.") from None


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
