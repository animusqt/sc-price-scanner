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
    ("uex", "UEX", "UEX_API_BASE_URL", "UEX_API_DOCUMENTATION_URL"),
    ("sc-trade-tools", "SC Trade Tools", "SC_TRADE_TOOLS_API_BASE_URL", "SC_TRADE_TOOLS_API_DOCUMENTATION_URL"),
    ("hauler", "Hauler", "HAULER_API_BASE_URL", "HAULER_API_DOCUMENTATION_URL"),
    ("sc-truckers", "SC-Truckers", "SC_TRUCKERS_API_BASE_URL", "SC_TRUCKERS_API_DOCUMENTATION_URL"),
    ("erkul", "Erkul", "ERKUL_API_BASE_URL", "ERKUL_API_DOCUMENTATION_URL"),
    ("wiki", "Star Citizen Wiki", "SC_WIKI_API_BASE_URL", "SC_WIKI_API_DOCUMENTATION_URL"),
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
