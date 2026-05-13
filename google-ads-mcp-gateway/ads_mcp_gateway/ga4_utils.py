import hashlib
import re
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


def _normalize_account_id(value: Optional[str]) -> Optional[str]:
    """Return numeric GA account id, or None. Accepts '2625639' or 'accounts/2625639'."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("accounts/"):
        s = s.removeprefix("accounts/")
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits or None


def _account_filter_for_properties(account_digits: str) -> str:
    """Admin API properties.list requires a non-empty filter; use parent account."""
    return f"parent:accounts/{account_digits}"


_FILTER_PARENT_RE = re.compile(
    r"^\s*(?P<kind>parent|ancestor)\s*:\s*accounts/(?P<aid>\d+)\s*$",
    re.IGNORECASE,
)


def _parse_account_from_filter(filter_value: Optional[str]) -> Optional[str]:
    """Extract account id from 'parent:accounts/123' or 'ancestor:accounts/123'."""
    if not filter_value or not str(filter_value).strip():
        return None
    m = _FILTER_PARENT_RE.match(str(filter_value).strip())
    if not m:
        return None
    return m.group("aid")


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
    """List all GA4 accounts accessible to the OAuth user (handles pagination)."""
    token = _token_string()
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True
    try:
        url = f"{GA4_ADMIN_BASE}/accounts"
        out: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            params: Dict[str, str] = {}
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(url, headers=_headers(token), params=params or None)
            if resp.status_code >= 400:
                raise ToolError(f"GA4 Admin API error {resp.status_code}: {resp.text}")
            data = resp.json()
            for acc in data.get("accounts") or []:
                if isinstance(acc, dict):
                    out.append(acc)
            page_token = data.get("nextPageToken") or None
            if not page_token:
                break
        return out
    finally:
        if close_client:
            await client.aclose()


async def _list_properties_for_parent(
    *,
    client: httpx.AsyncClient,
    token: str,
    account_digits: str,
) -> List[Dict[str, Any]]:
    """
    GA4 Admin properties.list — filter is required (parent:accounts/{id}).
    https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/properties/list
    """
    filt = _account_filter_for_properties(account_digits)
    out: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        params: Dict[str, str] = {"filter": filt}
        if page_token:
            params["pageToken"] = page_token
        url = f"{GA4_ADMIN_BASE}/properties"
        resp = await client.get(url, headers=_headers(token), params=params)
        if resp.status_code >= 400:
            raise ToolError(f"GA4 Admin API error {resp.status_code}: {resp.text}")
        data = resp.json()
        for p in data.get("properties") or []:
            if isinstance(p, dict):
                out.append(p)
        page_token = data.get("nextPageToken") or None
        if not page_token:
            break
    return out


async def list_properties(
    *,
    account_id: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    List GA4 properties accessible to the user.

    Admin API requires `filter` on properties.list — we use `parent:accounts/{id}`.

    If `account_id` is set (digits or `accounts/123`), list properties for that account only.
    If omitted, list accounts then aggregate properties under each account (deduped by `name`).
    """
    token = _token_string()
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=60.0)
        close_client = True
    try:
        acc_digits = _normalize_account_id(account_id)
        if acc_digits:
            return await _list_properties_for_parent(
                client=client, token=token, account_digits=acc_digits
            )

        accounts = await list_accounts(client=client)
        seen: set[str] = set()
        merged: List[Dict[str, Any]] = []
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            name = acc.get("name")
            if not isinstance(name, str) or not name.startswith("accounts/"):
                continue
            aid = _normalize_account_id(name)
            if not aid:
                continue
            try:
                props = await _list_properties_for_parent(
                    client=client, token=token, account_digits=aid
                )
            except ToolError:
                continue
            for p in props:
                if not isinstance(p, dict):
                    continue
                key = p.get("name")
                if isinstance(key, str) and key not in seen:
                    seen.add(key)
                    merged.append(p)
        return merged
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

    async with httpx.AsyncClient(timeout=60.0) as client:
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
    if not accessible:
        # Could not enumerate properties — likely wrong scope or no linked accounts.
        # Proceed and let the GA4 Data API return its own auth error rather than
        # blocking the call with a false-negative from an empty cache.
        return
    if pid not in accessible:
        raise ToolError(
            f"property_id '{pid}' is not in the list of GA4 properties accessible to this "
            "OAuth user. Call `ga4_list_properties` to see available properties."
        )


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

