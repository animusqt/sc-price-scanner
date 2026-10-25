"""External market provider contracts; no provider is enabled without a contract."""

from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class Provider:
    key: str
    display_name: str
    base_url_env: str
    documentation_url_env: str
    enabled: bool


PROVIDER_CONFIG = (
    (
        "sc-truckers",
        "SC-Truckers Lovable",
        "SC_TRUCKERS_API_BASE_URL",
        "SC_TRUCKERS_API_DOCUMENTATION_URL",
    ),
)


def provider_registry() -> list[dict[str, Any]]:
    """Report configuration state without exposing credentials or scraping sites."""
    return [
        {
            "key": key,
            "name": name,
            "configured": bool(os.environ.get(base_url_env)),
            "documentation_configured": bool(os.environ.get(documentation_env)),
            "enabled": False,
            "reason": (
                "A documented, authorized provider contract and field mapper are required before ingestion."
            ),
        }
        for key, name, base_url_env, documentation_env in PROVIDER_CONFIG
    ]
