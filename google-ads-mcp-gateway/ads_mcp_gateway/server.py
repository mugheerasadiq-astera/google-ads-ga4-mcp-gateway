import logging
import os

from ads_mcp_gateway.coordinator import OAUTH_PUBLIC_BASE_URL, mcp

# Register tools
from ads_mcp_gateway import tools  # noqa: F401
from ads_mcp_gateway import ga4_tools  # noqa: F401

logger = logging.getLogger(__name__)


def run_server() -> None:
    port = int(os.environ.get("PORT", "8085"))
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

