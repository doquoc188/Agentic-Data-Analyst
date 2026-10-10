"""Allowed database destinations, without domain reasoning or credentials."""

from dataclasses import dataclass, field

from app.config import ConfigurationError, Settings, get_settings, validate_database_url

LOCAL_AGENT_PROFILES = {
    "sales": "agentic_analyst",
    "saas": "agentic_analyst_saas",
    "olist": "agentic_analyst_olist",
}

# The deployed API and local MCP interface remain limited to these profiles.
PUBLIC_PROFILE_IDS = ("sales", "saas")
DATABASE_PROFILES = {
    profile: LOCAL_AGENT_PROFILES[profile] for profile in PUBLIC_PROFILE_IDS
}

# Safe display metadata never contains connection configuration.
LOCAL_AGENT_DATABASES = [
    {"id": "sales", "name": "Sales Analytics", "description": "Synthetic retail sales dataset"},
    {"id": "saas", "name": "SaaS Analytics", "description": "Synthetic subscription analytics dataset"},
    {
        "id": "olist",
        "name": "Olist E-Commerce",
        "description": "An anonymized real-world Brazilian e-commerce dataset.",
    },
]
PUBLIC_DATABASES = [
    profile for profile in LOCAL_AGENT_DATABASES if profile["id"] in PUBLIC_PROFILE_IDS
]


@dataclass(frozen=True)
class DatabaseTarget:
    database_name: str | None = None
    url: str | None = field(default=None, repr=False)


def resolve_profile(profile: str, settings: Settings | None = None) -> DatabaseTarget:
    """Resolve a local Agent ID; configured URLs take precedence over local names."""
    if profile not in LOCAL_AGENT_PROFILES:
        raise ConfigurationError("Unknown database profile.")
    settings = settings if settings is not None else get_settings(load_environment=False)
    url = {
        "sales": settings.database_sales_url,
        "saas": settings.database_saas_url,
        "olist": settings.database_olist_url,
    }[profile]
    if url:
        validate_database_url(url)
    else:
        settings.local_connection(LOCAL_AGENT_PROFILES[profile])
    return DatabaseTarget(LOCAL_AGENT_PROFILES[profile], url or None)
