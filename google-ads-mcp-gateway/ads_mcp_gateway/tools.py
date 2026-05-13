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
    select: List[str] | str | None = None,        # alias for `fields`
    resource: str | None = None,
    conditions: List[str] | str | None = None,
    where: List[str] | str | None = None,          # alias for `conditions`
    orderings: List[str] | str | None = None,
    order_by: List[str] | str | None = None,       # alias for `orderings`
    limit: int | str | None = None,
    gaql: str | None = None,
    query: str | None = None,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
) -> List[Dict[str, Any]]:
    """
    Google Ads — reporting: run a GAQL query via searchStream (read-only, API v24).

    Use for campaigns, ad groups, metrics, segments, etc. Do not use for Google Analytics;
    use `ga4_run_report` instead.

    Workflow: (1) `ads_list_accessible_customers` → pick advertiser `customer_id`;
    (2) if the account is under an MCC, pass `login_customer_id` (manager id, digits only);
    (3) pass `developer_token` (or set server env).

    Equivalent to official google-ads-mcp `search`; named `ads_search` here to avoid
    ambiguity with GA4 on the same MCP endpoint.

    Args:
        customer_id: Target advertiser customer id (digits only, no dashes).
        fields / select: GAQL SELECT field list (array or JSON string). `select` is an alias.
        resource: GAQL FROM resource (e.g. `campaign`). Required with structured fields unless
            `gaql` / `query` is set.
        conditions / where: Optional WHERE fragments, combined with AND (array or JSON string).
            `where` is an alias for `conditions`.
        orderings / order_by: Optional ORDER BY field list. `order_by` is an alias.
        limit: Optional LIMIT (integer or numeric string).
        gaql: Optional full GAQL string (SELECT ... FROM ...). When set, structured params are
            ignored. `PARAMETERS omit_unselected_resource_names=true` is appended if missing.
        query: Same as `gaql` — alias accepted by some MCP clients. `gaql` wins if both set.
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

    full_input = ((gaql or "").strip() or (query or "").strip())
    if full_input:
        final_gaql = utils.finalize_gaql_query(full_input)
    else:
        # Resolve aliases
        eff_fields = fields if fields is not None else select
        eff_conditions = conditions if conditions is not None else where
        eff_orderings = orderings if orderings is not None else order_by

        fields_list = utils.coerce_string_list(eff_fields, name="fields")
        if not fields_list:
            raise ToolError(
                "Provide either `gaql`/`query` (full GAQL string), or a non-empty `fields`/`select` "
                "list together with `resource`."
            )
        res = (resource or "").strip()
        if not res:
            raise ToolError("resource is required when neither `gaql` nor `query` is provided.")
        conditions_list = utils.coerce_string_list(eff_conditions, name="conditions")
        orderings_list = utils.coerce_string_list(eff_orderings, name="orderings")
        limit_int = utils.coerce_int_limit(limit)
        final_gaql = utils.build_gaql_query(
            fields=fields_list,
            resource=res,
            conditions=conditions_list,
            orderings=orderings_list,
            limit=limit_int,
        )

    token = utils._token_string()
    url = f"{utils.ADS_API_BASE}/customers/{cid}/googleAds:searchStream"
    body = {"query": final_gaql}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=utils._headers(token, dt, lcid), json=body)
        if resp.status_code >= 400:
            raise ToolError(
                utils.enhance_google_ads_http_error_message(
                    status_code=resp.status_code,
                    response_text=resp.text,
                    login_customer_id=lcid,
                )
            )

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
    resource_name: str | None = None,
    resource: str | None = None,          # alias for resource_name
    login_customer_id: str | None = None,
    developer_token: str | None = None,
) -> Dict[str, Any]:
    """
    Google Ads — metadata: selectable / filterable / sortable field names for a GAQL resource.

    Call before `ads_search` when unsure which fields exist. Same role as official
    google-ads-mcp `get_resource_metadata`; uses REST `googleAdsFields:search` (v24).
    Field-service queries must **not** include a `FROM` clause (unlike GAQL on `searchStream`).

    Args:
        resource_name: GAQL resource root (e.g. `campaign`, `ad_group`). Also `resource`.
        resource: Alias for `resource_name` (some MCP clients send this name).
        login_customer_id: Optional MCC id (digits only); rarely needed for Field Service.
        developer_token: Google Ads developer token (or env fallback).

    Returns:
        Dict with keys `resource`, `selectable`, `filterable`, `sortable` (sorted lists).
    """
    raw_resource = (resource_name or "").strip() or (resource or "").strip()
    if not raw_resource:
        raise ToolError("resource_name (or `resource`) is required.")
    rn = utils.validate_field_service_resource_prefix(raw_resource)
    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)

    selectable: set[str] = set()
    filterable: set[str] = set()
    sortable: set[str] = set()

    q_attr = (
        "SELECT name, selectable, filterable, sortable "
        f"WHERE name LIKE '{rn}.%' AND category = 'ATTRIBUTE'"
    )
    try:
        rows1 = await utils.search_google_ads_fields_rest(
            query=q_attr, developer_token=dt, login_customer_id=lcid
        )
        _merge_google_ads_field_rows(rows1, rn, selectable, filterable, sortable, require_prefix=False)
    except ToolError as e:
        q_fb = (
            "SELECT name, selectable, filterable, sortable "
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
        "SELECT name, selectable, filterable, sortable "
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
