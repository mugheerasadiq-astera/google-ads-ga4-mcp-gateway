from typing import Any, Dict, List, Optional

import httpx
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp_gateway.coordinator import mcp
from ads_mcp_gateway import utils


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ads_list_accessible_customers(
    login_customer_id: str | None = None,
    developer_token: str | None = None,
) -> List[str]:
    """
    Google Ads — discovery: list customer IDs the signed-in user can access (API v24).

    Call this first when the user has not given a customer id. IDs are digit strings
    (strip dashes from UI values). For MCC-managed advertisers, you still need
    `login_customer_id` on `ads_search`, not necessarily here.

    Equivalent to official google-ads-mcp `list_accessible_customers`; name is `ads_*`
    so it is not confused with `ga4_*` tools on the same server.

    Args:
        login_customer_id: Optional MCC/manager id (digits only); rarely needed for this call.
        developer_token: Google Ads developer token; per-call in SaaS, or env GOOGLE_ADS_DEVELOPER_TOKEN.

    Returns:
        List of customer id strings.
    """
    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    return await utils.list_accessible_customers_rest(developer_token=dt, login_customer_id=lcid)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def ads_search(
    customer_id: str,
    fields: List[str] | str | None = None,
    resource: str | None = None,
    conditions: List[str] | str | None = None,
    orderings: List[str] | str | None = None,
    limit: int | str | None = None,
    gaql: str | None = None,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
) -> List[Dict[str, Any]]:
    """
    Google Ads — reporting: run a GAQL query via searchStream (read-only, API v24).

    Use for campaigns, ad groups, metrics, segments, etc. Do not use for Google Analytics;
    use `ga4_run_report` instead.

    Workflow: (1) `ads_list_accessible_customers` → pick advertiser `customer_id`;
    (2) if the account is under an MCC, pass `login_customer_id` (manager id, digits only);
    (3) pass `developer_token` (or set server env). Host may inject MCC/token from tenant config.

    Equivalent to official google-ads-mcp `search`; named `ads_search` here to avoid
    ambiguity with GA4 on the same MCP endpoint.

    Args:
        customer_id: Target advertiser customer id (digits only, no dashes).
        fields: GAQL SELECT field list (native JSON array preferred). Some MCP clients send a
            JSON-encoded string (e.g. '["campaign.name"]'); that is accepted too.
        resource: GAQL FROM resource (e.g. campaign). Required with structured `fields` unless `gaql` is set.
        conditions: Optional WHERE fragments, combined with AND (list or JSON string).
        orderings: Optional ORDER BY field list (list or JSON string).
        limit: Optional LIMIT (integer or numeric string).
        gaql: Optional full GAQL string (SELECT ... FROM ...). When set, `fields` / `resource` /
            `conditions` / `orderings` / `limit` are ignored. `PARAMETERS omit_unselected_resource_names=true`
            is appended if missing.
        login_customer_id: MCC/manager id when querying a client account through a manager.
        developer_token: Google Ads developer token (required for the API).

    Returns:
        List of row dicts (flattened result objects from the API stream).
    """
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits (e.g. 1234567890).")
    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)

    # Enforce minimal access policy.
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    gaql_stripped = (gaql or "").strip()
    if gaql_stripped:
        query = utils.finalize_gaql_query(gaql_stripped)
    else:
        fields_list = utils.coerce_string_list(fields, name="fields")
        if not fields_list:
            raise ToolError(
                "Provide either gaql (full query) or a non-empty fields list with resource."
            )
        res = (resource or "").strip()
        if not res:
            raise ToolError("resource is required when gaql is not provided.")
        conditions_list = utils.coerce_string_list(conditions, name="conditions")
        orderings_list = utils.coerce_string_list(orderings, name="orderings")
        limit_int = utils.coerce_int_limit(limit)
        query = utils.build_gaql_query(
            fields=fields_list,
            resource=res,
            conditions=conditions_list,
            orderings=orderings_list,
            limit=limit_int,
        )

    token = utils._token_string()
    url = f"{utils.ADS_API_BASE}/customers/{cid}/googleAds:searchStream"
    body = {"query": query}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=utils._headers(token, dt, lcid), json=body)
        if resp.status_code >= 400:
            raise ToolError(f"Google Ads API error {resp.status_code}: {resp.text}")

        return utils._flatten_search_stream_rows(resp.text)


def _merge_google_ads_field_rows(
    rows: List[Dict[str, Any]],
    resource_prefix: str,
    selectable: set,
    filterable: set,
    sortable: set,
    *,
    require_prefix: bool,
) -> None:
    for row in rows:
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        if require_prefix and not name.startswith(f"{resource_prefix}."):
            continue
        if row.get("selectable") is True:
            selectable.add(name)
        if row.get("filterable") is True:
            filterable.add(name)
        if row.get("sortable") is True:
            sortable.add(name)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ads_get_resource_metadata(
    resource_name: str,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
) -> Dict[str, Any]:
    """
    Google Ads — metadata: selectable / filterable / sortable field names for a GAQL resource.

    Call before `ads_search` when unsure which fields exist. Same role as official
    google-ads-mcp `get_resource_metadata`; uses REST `googleAdsFields:search` (v24).

    Args:
        resource_name: GAQL resource root (e.g. `campaign`, `ad_group`).
        login_customer_id: Optional MCC id (digits only); rarely needed for Field Service.
        developer_token: Google Ads developer token (or env fallback).

    Returns:
        Dict with keys `resource`, `selectable`, `filterable`, `sortable` (sorted lists).
    """
    rn = utils.validate_field_service_resource_prefix(resource_name)
    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)

    selectable: set[str] = set()
    filterable: set[str] = set()
    sortable: set[str] = set()

    q_attr = (
        "SELECT name, selectable, filterable, sortable FROM google_ads_field "
        f"WHERE name LIKE '{rn}.%' AND category = 'ATTRIBUTE'"
    )
    try:
        rows1 = await utils.search_google_ads_fields_rest(
            query=q_attr, developer_token=dt, login_customer_id=lcid
        )
        _merge_google_ads_field_rows(rows1, rn, selectable, filterable, sortable, require_prefix=False)
    except ToolError as e:
        q_fb = (
            "SELECT name, selectable, filterable, sortable FROM google_ads_field "
            f"WHERE name LIKE '{rn}.%'"
        )
        try:
            rows_fb = await utils.search_google_ads_fields_rest(
                query=q_fb, developer_token=dt, login_customer_id=lcid
            )
            _merge_google_ads_field_rows(
                rows_fb, rn, selectable, filterable, sortable, require_prefix=True
            )
        except ToolError as e2:
            raise ToolError(f"Field metadata query failed: {e}\nFallback failed: {e2}") from e2

    q_ms = (
        "SELECT name, selectable, filterable, sortable FROM google_ads_field "
        f"WHERE selectable_with CONTAINS ANY('{rn}')"
    )
    try:
        rows2 = await utils.search_google_ads_fields_rest(
            query=q_ms, developer_token=dt, login_customer_id=lcid
        )
        _merge_google_ads_field_rows(rows2, rn, selectable, filterable, sortable, require_prefix=False)
    except ToolError:
        pass

    return {
        "resource": rn,
        "selectable": sorted(selectable),
        "filterable": sorted(filterable),
        "sortable": sorted(sortable),
    }
