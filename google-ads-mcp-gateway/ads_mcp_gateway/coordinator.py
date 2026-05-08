import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider


def _normalize_public_base_url(url: str) -> str:
    """Public origin for OAuth (no /mcp path).

    If GOOGLE_ADS_MCP_BASE_URL is mistakenly set to ``http://host:port/mcp``, FastMCP
    moves discovery to ``/.well-known/oauth-authorization-server/mcp`` (RFC 8414),
    and ``GET /.well-known/oauth-authorization-server`` returns 404.
    """
    u = (url or "").strip().rstrip("/")
    if u.endswith("/mcp"):
        u = u[: -len("/mcp")].rstrip("/")
    return u or f"http://localhost:{os.environ.get('PORT', '8085')}"


# Same pattern as official googleads/google-ads-mcp: when these are set, FastMCP serves
# OAuth metadata at /.well-known/oauth-authorization-server so Astera DCR/discovery works.
_CLIENT_ID = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_ID")
_CLIENT_SECRET = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET")
_PORT = os.environ.get("PORT", "8085")
_RAW_BASE = os.environ.get("GOOGLE_ADS_MCP_BASE_URL", f"http://localhost:{_PORT}")
_BASE_URL = _normalize_public_base_url(_RAW_BASE)

if _CLIENT_ID and _CLIENT_SECRET:
    auth = GoogleProvider(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        base_url=_BASE_URL,
        required_scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/adwords",
            "https://www.googleapis.com/auth/analytics.readonly",
        ],
    )
    mcp = FastMCP("google-ads-mcp-gateway", auth=auth)
else:
    mcp = FastMCP("google-ads-mcp-gateway")

# Set when OAuth proxy is active; use for Astera Discovery URL (append /.well-known/...).
OAUTH_PUBLIC_BASE_URL: str | None = _BASE_URL if (_CLIENT_ID and _CLIENT_SECRET) else None

