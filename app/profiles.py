"""Allowed database destinations, without domain reasoning or credentials."""

from dataclasses import dataclass, field

from app.config import ConfigurationError, Settings, get_settings, validate_database_url

DATABASE_PROFILES = {
    "sales": "agentic_analyst",
    "saas": "agentic_analyst_saas",
}

# Public descriptions never contain connection configuration.
PUBLIC_DATABASES = [
    {"id": "sales", "name": "Sales Analytics", "description": "Synthetic retail sales dataset"},
    {"id": "saas", "name": "SaaS Analytics", "description": "Synthetic subscription analytics dataset"},
]


@dataclass(frozen=True)
class DatabaseTarget:
    database_name: str | None = None
    url: str | None = field(default=None, repr=False)


def resolve_profile(profile: str, settings: Settings | None = None) -> DatabaseTarget:
    """Resolve an allowed ID; configured URLs take precedence over local names."""
    if profile not in DATABASE_PROFILES:
        raise ConfigurationError("Unknown database profile.")
    settings = settings if settings is not None else get_settings(load_environment=False)
    url = {"sales": settings.database_sales_url, "saas": settings.database_saas_url}[profile]
    if url:
        validate_database_url(url)
    else:
        settings.local_connection(DATABASE_PROFILES[profile])
    return DatabaseTarget(DATABASE_PROFILES[profile], url or None)
