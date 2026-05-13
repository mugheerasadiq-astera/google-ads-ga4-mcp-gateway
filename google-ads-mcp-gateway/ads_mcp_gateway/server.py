import logging
import os

from ads_mcp_gateway.coordinator import OAUTH_PUBLIC_BASE_URL, mcp

# Register tools
from ads_mcp_gateway import tools  # noqa: F401
from ads_mcp_gateway import ga4_tools  # noqa: F401
from ads_mcp_gateway import mutate_tools  # noqa: F401
from ads_mcp_gateway import safety

logger = logging.getLogger(__name__)


def run_server() -> None:
    port = int(os.environ.get("PORT", "8085"))
    if safety.mutations_enabled():
        logger.warning(
            "⚠  ADS_MCP_ENABLE_MUTATIONS=true — Google Ads WRITE tools are ACTIVE. "
            "Ensure ADS_MCP_MUTATION_CUSTOMER_ALLOWLIST and "
            "ADS_MCP_MAX_DAILY_BUDGET_MICROS are set appropriately."
        )
    else:
        logger.info(
            "ADS_MCP_ENABLE_MUTATIONS is not set — Google Ads mutation tools are "
            "disabled (read-only mode). Set ADS_MCP_ENABLE_MUTATIONS=true to enable writes."
        )
    if not (os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip():
        logger.warning(
            "GOOGLE_ADS_DEVELOPER_TOKEN is not set — every Google Ads tool "
            "(including ads_list_accessible_customers and ads_search) needs a "
            "developer token unless the client passes developer_token on each call. "
            "Set GOOGLE_ADS_DEVELOPER_TOKEN in the environment (or .env) before starting."
        )
    if mcp.auth is None:
        logger.warning(
            "OAuth proxy is disabled: set GOOGLE_ADS_MCP_OAUTH_CLIENT_ID and "
            "GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET in the environment (or a .env file in "
            "the gateway folder) before starting, then restart. Without them, "
            "GET /.well-known/oauth-authorization-server returns 404 and Astera "
            "discovery fails."
        )
    else:
        assert OAUTH_PUBLIC_BASE_URL
        logger.info(
            "OAuth proxy enabled. Astera Discovery URL (no /mcp suffix): "
            "%s/.well-known/oauth-authorization-server",
            OAUTH_PUBLIC_BASE_URL.rstrip("/"),
        )
    # Always run as streamable-http; this is intended for hosted/shared SaaS.
    mcp.run(transport="streamable-http", port=port, host="0.0.0.0")


if __name__ == "__main__":
    run_server()

