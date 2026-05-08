# Google Ads + GA4 MCP Gateway (minimal)

This is a minimal **multi-tenant** MCP server that proxies *read-only* Google Ads and GA4 calls **via REST**.

## Why this exists

The official `googleads/google-ads-mcp` server takes `login-customer-id` from an environment variable, which makes a single shared deployment effectively single-tenant for MCC context. This gateway makes `login_customer_id` **per tool call**.

## Transport

- Uses **FastMCP streamable-http**.
- MCP endpoint: `http://localhost:8085/mcp` (default).

## Auth model

Astera calls MCP servers with `Authorization: Bearer <access_token>` attached. This server reads that access token from FastMCP and forwards it to Google APIs.

### OAuth discovery in Astera (fixes 404 on `/.well-known/oauth-authorization-server`)

If Studio uses **Discovery URL** `http://localhost:8085/.well-known/oauth-authorization-server`, that document is only served when the gateway runs with **Google OAuth client env vars** (same as official `google-ads-mcp`):

```powershell
$env:GOOGLE_ADS_MCP_OAUTH_CLIENT_ID="your.apps.googleusercontent.com"
$env:GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET="your-secret"
# Must match the URL users hit (scheme, host, port). For ngrok, use your https ngrok base URL (no trailing path).
$env:GOOGLE_ADS_MCP_BASE_URL="http://localhost:8085"
$env:PORT="8085"
py -m ads_mcp_gateway.server
```

Then in Astera, set **Discovery URL** to:

`http://localhost:8085/.well-known/oauth-authorization-server`

(Replace host/port with your public URL if using ngrok.)

If you **do not** set the client id/secret, the gateway has no OAuth proxy; use another OAuth setup in Studio (e.g. static Google authorize/token URLs) or run the official Ads MCP pattern above.

**Scopes** requested by the gateway include both **Google Ads** (`adwords`) and **GA4** (`analytics.readonly`) so one login covers both tool families.

#### If discovery still returns 404

1. **Restart** the gateway after setting env vars (they are read when the process starts). Prefer a `.env` file in this folder (`GOOGLE_ADS_MCP_OAUTH_CLIENT_ID=...`) so you do not forget the shell step; `python-dotenv` is loaded at startup.
2. **`GOOGLE_ADS_MCP_BASE_URL` must not end with `/mcp`.** Use `http://localhost:8085`, not `http://localhost:8085/mcp`. Otherwise FastMCP serves metadata at `/.well-known/oauth-authorization-server/mcp` and the root discovery URL 404s.
3. On startup, when OAuth is on, the log line **Astera Discovery URL** shows the exact URL to paste into Studio.

## Tools

### Google Ads

Tool names use an `ads_` prefix so they stay distinct from `ga4_*` on the same `/mcp` endpoint (behavior matches official google-ads-mcp `list_accessible_customers` / `search` / `get_resource_metadata`).

- `ads_list_accessible_customers(login_customer_id?, developer_token?)`
- `ads_get_resource_metadata(resource_name, login_customer_id?, developer_token?)`
- `ads_search(customer_id, fields, resource, conditions?, orderings?, limit?, login_customer_id?, developer_token?)`

### GA4 (Google Analytics)

- `ga4_list_accounts()`
- `ga4_list_properties()`
- `ga4_run_report(property_id, request)`
- `ga4_run_realtime_report(property_id, request)`

Notes:
- `developer_token` can be passed **per-call**. If omitted, the server falls back to `GOOGLE_ADS_DEVELOPER_TOKEN`.
- `login_customer_id` is optional, but required when querying an advertiser account via MCC.

If you previously called `list_accessible_customers` / `search` on this gateway, update skills and automation to `ads_list_accessible_customers` / `ads_search`. Add **`ads_get_resource_metadata`** where you used official `get_resource_metadata`.

## Run locally

```powershell
cd "C:\Code\third-repo\Centerprise\Self Hosted MCPs\google-ads-mcp-gateway"
py -m pip install -e .

# Optional fallback developer token (recommended to pass per-call in SaaS)
$env:GOOGLE_ADS_DEVELOPER_TOKEN="YOUR_TOKEN"
$env:PORT="8085"

py -m ads_mcp_gateway.server
```

