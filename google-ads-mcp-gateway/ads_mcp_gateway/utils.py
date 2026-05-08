import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

ADS_API_VERSION = "v24"
ADS_API_BASE = f"https://googleads.googleapis.com/{ADS_API_VERSION}"


def _token_string() -> str:
    token_obj = get_access_token()
    if not token_obj or not token_obj.token:
        raise ToolError(
            "Missing OAuth access token. This MCP server expects the caller "
            "to provide Authorization: Bearer <token> when calling /mcp."
        )
    return token_obj.token


def _token_cache_key(token: str) -> str:
    # Tokens may be large/opaque; store only a hash.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _developer_token(developer_token: Optional[str]) -> str:
    dt = (developer_token or "").strip() or (os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip()
    if not dt:
        raise ToolError(
            "developer_token is required. Pass developer_token to the tool call, "
            "or set GOOGLE_ADS_DEVELOPER_TOKEN in the server environment."
        )
    return dt


def _normalize_customer_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def _headers(token: str, developer_token: str, login_customer_id: Optional[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    return headers


def _parse_search_stream_batches(text: str) -> List[Dict[str, Any]]:
    """
    Parse googleAds:searchStream HTTP body into batch dicts (each may contain 'results').

    Normal case: newline-delimited JSON (one object per line). Some stacks return a
    single JSON array of batch objects instead; handle both without assuming .get on a list.
    """
    text = text or ""
    stripped = text.strip()
    if not stripped:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    batch_dicts: List[Dict[str, Any]] = []
    ndjson_ok = True
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            ndjson_ok = False
            break
        if isinstance(obj, dict):
            batch_dicts.append(obj)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    batch_dicts.append(item)
        else:
            ndjson_ok = False
            break

    if ndjson_ok:
        return batch_dicts

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _flatten_search_stream_rows(text: str) -> List[Dict[str, Any]]:
    """Flatten all row dicts from a searchStream body."""
    out: List[Dict[str, Any]] = []
    for batch in _parse_search_stream_batches(text):
        results = batch.get("results") or []
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict):
                    out.append(r)
    return out


@dataclass
class CachedAccessibleCustomers:
    customer_ids: List[str]
    expires_at: float


_accessible_customers_cache: Dict[str, CachedAccessibleCustomers] = {}
_ACCESSIBLE_CUSTOMERS_TTL_SECONDS = 300.0


async def list_accessible_customers_rest(
    *,
    developer_token: str,
    login_customer_id: Optional[str],
    client: Optional[httpx.AsyncClient] = None,
) -> List[str]:
    token = _token_string()
    cache_key = _token_cache_key(token)
    now = time.time()
    cached = _accessible_customers_cache.get(cache_key)
    if cached and cached.expires_at > now:
        return cached.customer_ids

    url = f"{ADS_API_BASE}/customers:listAccessibleCustomers"
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True
    try:
        resp = await client.get(url, headers=_headers(token, developer_token, login_customer_id))
        if resp.status_code >= 400:
            raise ToolError(f"Google Ads API error {resp.status_code}: {resp.text}")
        data = resp.json()
        resource_names = data.get("resourceNames", []) or []
        ids = [rn.removeprefix("customers/") for rn in resource_names if isinstance(rn, str)]
        _accessible_customers_cache[cache_key] = CachedAccessibleCustomers(
            customer_ids=ids,
            expires_at=now + _ACCESSIBLE_CUSTOMERS_TTL_SECONDS,
        )
        return ids
    finally:
        if close_client:
            await client.aclose()


async def assert_customer_access(
    *,
    customer_id: str,
    developer_token: str,
    login_customer_id: Optional[str],
) -> None:
    """
    Minimal tenant safety:
    - If customer_id is directly accessible to the OAuth user => allow.
    - Else if login_customer_id is provided and is accessible => verify that
      customer_id is a child in that manager tree via a customer_client query.
    """
    token = _token_string()
    async with httpx.AsyncClient(timeout=30.0) as client:
        accessible = await list_accessible_customers_rest(
            developer_token=developer_token,
            login_customer_id=login_customer_id,
            client=client,
        )
        if customer_id in accessible:
            return

        if not login_customer_id:
            raise ToolError(
                f"customer_id '{customer_id}' is not directly accessible to this OAuth user. "
                "Provide login_customer_id (MCC) and ensure the advertiser is under that MCC."
            )

        if login_customer_id not in accessible:
            raise ToolError(
                f"login_customer_id '{login_customer_id}' is not accessible to this OAuth user."
            )

        # Verify customer_id is in the manager's customer_client tree.
        # Query runs against the MCC (login_customer_id).
        query = (
            "SELECT customer_client.client_customer, customer_client.manager "
            "FROM customer_client "
            f"WHERE customer_client.client_customer = 'customers/{customer_id}' "
            "LIMIT 1"
        )
        url = f"{ADS_API_BASE}/customers/{login_customer_id}/googleAds:searchStream"
        body = {"query": query}
        resp = await client.post(url, headers=_headers(token, developer_token, login_customer_id), json=body)
        if resp.status_code >= 400:
            raise ToolError(f"Google Ads API error {resp.status_code}: {resp.text}")

        any_result = False
        for obj in _parse_search_stream_batches(resp.text):
            results = obj.get("results") or []
            if isinstance(results, list) and results:
                any_result = True
                break
        if not any_result:
            raise ToolError(
                f"customer_id '{customer_id}' is not accessible under login_customer_id '{login_customer_id}' "
                "for this OAuth user."
            )


def coerce_string_list(
    value: Optional[Union[List[str], str]],
    *,
    name: str,
) -> Optional[List[str]]:
    """Accept native lists or JSON-array / comma-separated strings (some MCP clients send strings)."""
    if value is None:
        return None
    if isinstance(value, list):
        out = [str(x).strip() for x in value if str(x).strip()]
        return out or None
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                out = [str(x).strip() for x in parsed if str(x).strip()]
                return out or None
        except json.JSONDecodeError:
            pass
    out = [p.strip() for p in s.split(",") if p.strip()]
    return out or None


def coerce_int_limit(value: Optional[Union[int, str]]) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError as e:
        raise ToolError(f"limit must be an integer, got {value!r}.") from e


def finalize_gaql_query(query: str) -> str:
    """Strip trailing semicolon and append omit_unselected_resource_names if missing."""
    q = query.strip().rstrip(";")
    if not q:
        raise ToolError("gaql query is empty.")
    if "omit_unselected_resource_names" not in q.lower():
        q = f"{q} PARAMETERS omit_unselected_resource_names=true"
    return q


def validate_field_service_resource_prefix(name: str) -> str:
    """GAQL google_ads_field queries use LIKE '{prefix}.%' — restrict prefix to safe tokens."""
    n = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]+", n):
        raise ToolError(
            "resource_name must be alphanumeric or underscore (e.g. campaign, ad_group)."
        )
    return n


async def search_google_ads_fields_rest(
    *,
    query: str,
    developer_token: str,
    login_customer_id: Optional[str] = None,
    page_size: int = 1000,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """POST v24/googleAdsFields:search (paginated). Each result is a GoogleAdsField JSON object."""
    token = _token_string()
    url = f"{ADS_API_BASE}/googleAdsFields:search"
    headers = _headers(token, developer_token, login_customer_id)
    close_client = client is None
    if close_client:
        client = httpx.AsyncClient(timeout=120.0)
    out: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    try:
        while True:
            body: Dict[str, Any] = {"query": query, "pageSize": page_size}
            if page_token:
                body["pageToken"] = page_token
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                raise ToolError(f"Google Ads Field API error {resp.status_code}: {resp.text}")
            data = resp.json()
            for row in data.get("results") or []:
                if isinstance(row, dict):
                    out.append(row)
            page_token = data.get("nextPageToken") or None
            if not page_token:
                break
        return out
    finally:
        if close_client and client is not None:
            await client.aclose()


def build_gaql_query(
    *,
    fields: List[str],
    resource: str,
    conditions: Optional[List[str]] = None,
    orderings: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> str:
    if not fields:
        raise ToolError("fields must be a non-empty list.")
    if not resource or not resource.strip():
        raise ToolError("resource must be provided.")

    query_parts: List[str] = [f"SELECT {','.join(fields)} FROM {resource}"]

    if conditions:
        query_parts.append(f" WHERE {' AND '.join(conditions)}")
    if orderings:
        query_parts.append(f" ORDER BY {','.join(orderings)}")
    if limit is not None:
        query_parts.append(f" LIMIT {int(limit)}")

    query_parts.append(" PARAMETERS omit_unselected_resource_names=true")
    return "".join(query_parts)

