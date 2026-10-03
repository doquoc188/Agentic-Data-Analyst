"""Small environment settings shared by the API, model, database, and traces."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigurationError(RuntimeError):
    """Invalid or missing settings; never retain their values."""


@dataclass(frozen=True)
class Settings:
    google_api_key: str = field(default="", repr=False)
    database_sales_url: str = field(default="", repr=False)
    database_saas_url: str = field(default="", repr=False)
    db_host: str = field(default="", repr=False)
    db_port: str = field(default="", repr=False)
    db_name: str = field(default="", repr=False)
    db_user: str = field(default="", repr=False)
    db_password: str = field(default="", repr=False)
    allowed_origins: tuple[str, ...] = ()
    trace_enabled: bool = True
    trace_dir: Path | None = None

    def local_connection(self, database_name: str | None = None) -> dict:
        values = {
            "host": self.db_host, "port": self.db_port,
            "dbname": database_name or self.db_name,
            "user": self.db_user, "password": self.db_password,
        }
        if not all(values.values()):
            raise ConfigurationError("Required local database settings are missing.")
        return values


def get_settings(*, load_environment: bool = True) -> Settings:
    """Preserve process overrides; no caching or per-request environment edits."""
    if load_environment:
        load_dotenv()
    text = os.environ.get
    enabled = text("TRACE_ENABLED", "true").strip().lower()
    if enabled not in {"true", "false"}:
        raise ConfigurationError("TRACE_ENABLED must be true or false.")
    origins = tuple(origin.strip() for origin in text("ALLOWED_ORIGINS", "").split(",")
                    if origin.strip())
    # Require explicit origins, including in production; credentials are disabled.
    if any("*" in origin for origin in origins):
        raise ConfigurationError("ALLOWED_ORIGINS requires explicit origins.")
    trace_dir = text("TRACE_DIR", "").strip()
    return Settings(
        google_api_key=text("GOOGLE_API_KEY", ""),
        database_sales_url=text("DATABASE_SALES_URL", ""),
        database_saas_url=text("DATABASE_SAAS_URL", ""),
        db_host=text("DB_HOST", ""), db_port=text("DB_PORT", ""),
        db_name=text("DB_NAME", ""), db_user=text("DB_USER", ""),
        db_password=text("DB_PASSWORD", ""), allowed_origins=origins,
        trace_enabled=enabled == "true",
        trace_dir=PROJECT_ROOT / trace_dir if trace_dir else None,
    )


def validate_database_url(url: str) -> None:
    """Let Psycopg validate URL syntax without connecting or rebuilding it."""
    try:
        if not url.startswith(("postgresql://", "postgres://")):
            raise ValueError
        conninfo_to_dict(url)
    except Exception:
        raise ConfigurationError("Configured database URL is invalid.") from None


def secret_values() -> list[str]:
    """Known secrets for redaction, even when other configuration is invalid."""
    values = [os.environ.get(name, "") for name in (
        "DB_PASSWORD", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
        "DATABASE_SALES_URL", "DATABASE_SAAS_URL",
    )]
    for name in ("DATABASE_SALES_URL", "DATABASE_SAAS_URL"):
        url = os.environ.get(name, "")
        if url:
            try:
                # Native decoding also redacts a hosted password printed alone.
                values.append(conninfo_to_dict(url).get("password", ""))
            except Exception:
                pass
    return sorted({value for value in values if value}, key=len, reverse=True)
