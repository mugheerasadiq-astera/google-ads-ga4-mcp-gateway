import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token


GA4_ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
GA4_DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"


def _token_string() -> str:
    token_obj = get_access_token()
    if not token_obj or not token_obj.token:
        raise ToolError(
            "Missing OAuth access token. This MCP server expects the caller "
            "to provide Authorization: Bearer <token> when calling /mcp."
        )
    return token_obj.token


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _token_cache_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_property_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    # Accept "properties/123" or "123"
    raw = value.strip()
    if raw.startswith("properties/"):
        raw = raw.removeprefix("properties/")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or None


@dataclass
class CachedProperties:
    property_ids: List[str]
    expires_at: float


_properties_cache: Dict[str, CachedProperties] = {}
_PROPERTIES_TTL_SECONDS = 300.0


async def list_accounts(*, client: Optional[httpx.AsyncClient] = None) -> List[Dict[str, Any]]:
    token = _token_string()
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True
    try:
        url = f"{GA4_ADMIN_BASE}/accounts"
        resp = await client.get(url, headers=_headers(token))
        if resp.status_code >= 400:
            raise ToolError(f"GA4 Admin API error {resp.status_code}: {resp.text}")
        data = resp.json()
        return data.get("accounts", []) or []
    finally:
        if close_client:
            await client.aclose()


async def list_properties(*, client: Optional[httpx.AsyncClient] = None) -> List[Dict[str, Any]]:
    token = _token_string()
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True
    try:
        url = f"{GA4_ADMIN_BASE}/properties"
        resp = await client.get(url, headers=_headers(token))
        if resp.status_code >= 400:
            raise ToolError(f"GA4 Admin API error {resp.status_code}: {resp.text}")
        data = resp.json()
        return data.get("properties", []) or []
    finally:
        if close_client:
            await client.aclose()


async def list_accessible_property_ids() -> List[str]:
    token = _token_string()
    cache_key = _token_cache_key(token)
    now = time.time()
    cached = _properties_cache.get(cache_key)
    if cached and cached.expires_at > now:
        return cached.property_ids

    async with httpx.AsyncClient(timeout=30.0) as client:
        props = await list_properties(client=client)
        ids: List[str] = []
        for p in props:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            if isinstance(name, str) and name.startswith("properties/"):
                pid = _normalize_property_id(name)
                if pid:
                    ids.append(pid)

        _properties_cache[cache_key] = CachedProperties(
            property_ids=ids,
            expires_at=now + _PROPERTIES_TTL_SECONDS,
        )
        return ids


async def assert_property_access(property_id: str) -> None:
    pid = _normalize_property_id(property_id)
    if not pid:
        raise ToolError("property_id must be digits (e.g. 123456789) or 'properties/123456789'.")
    accessible = await list_accessible_property_ids()
    if pid not in accessible:
        raise ToolError(f"property_id '{pid}' is not accessible to this OAuth user.")


async def run_report(
    *,
    property_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    pid = _normalize_property_id(property_id)
    if not pid:
        raise ToolError("property_id must be digits (e.g. 123456789) or 'properties/123456789'.")
    await assert_property_access(pid)

    token = _token_string()
    url = f"{GA4_DATA_BASE}/properties/{pid}:runReport"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=_headers(token), json=body)
        if resp.status_code >= 400:
            raise ToolError(f"GA4 Data API error {resp.status_code}: {resp.text}")
        return resp.json()


async def run_realtime_report(
    *,
    property_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    pid = _normalize_property_id(property_id)
    if not pid:
        raise ToolError("property_id must be digits (e.g. 123456789) or 'properties/123456789'.")
    await assert_property_access(pid)

    token = _token_string()
    url = f"{GA4_DATA_BASE}/properties/{pid}:runRealtimeReport"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=_headers(token), json=body)
        if resp.status_code >= 400:
            raise ToolError(f"GA4 Data API error {resp.status_code}: {resp.text}")
        return resp.json()

