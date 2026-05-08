from typing import Any, Dict, List

from mcp.types import ToolAnnotations

from ads_mcp_gateway.coordinator import mcp
from ads_mcp_gateway import ga4_utils


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_list_accounts() -> List[Dict[str, Any]]:
    """
    Google Analytics 4 — discovery: list Admin API accounts for the OAuth user.

    OAuth token must include Analytics scope (e.g. analytics.readonly). Use before
    picking a property; not for Google Ads (use `ads_list_accessible_customers`).

    Returns:
        List of account resource objects (see GA4 Admin API `accounts.list`).
    """
    return await ga4_utils.list_accounts()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_list_properties() -> List[Dict[str, Any]]:
    """
    Google Analytics 4 — discovery: list properties the user can access.

    Call before `ga4_run_report` to obtain numeric `property_id` values. Same scope
    requirement as `ga4_list_accounts`. Not for Google Ads data.

    Returns:
        List of property objects (includes `name` like `properties/123...`).
    """
    return await ga4_utils.list_properties()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_run_report(
    property_id: str,
    request: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Google Analytics 4 — reporting: run a Data API `runReport` for one property.

    Body must match the official request JSON: `dateRanges`, `dimensions`, `metrics`,
    optional `dimensionFilter`, `metricFilter`, `orderBys`, `limit`, etc. See Google
    Analytics Data API reference for `RunReportRequest`.

    The gateway checks that `property_id` is in the user’s accessible property list.

    Args:
        property_id: GA4 property id as digits or `properties/<digits>`.
        request: Full `RunReportRequest` object as a dict (not a GAQL string).

    Returns:
        API response dict (rows, dimensionHeaders, metricHeaders, etc.).
    """
    return await ga4_utils.run_report(property_id=property_id, body=request)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_run_realtime_report(
    property_id: str,
    request: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Google Analytics 4 — reporting: run `runRealtimeReport` (live snapshot).

    Same OAuth scope as other GA4 tools. `request` must match `RunRealtimeReportRequest`
    (dimensions, metrics, optional filters). Not for Google Ads.

    Args:
        property_id: GA4 property id as digits or `properties/<digits>`.
        request: Full `RunRealtimeReportRequest` as a dict.

    Returns:
        API response dict for the realtime report.
    """
    return await ga4_utils.run_realtime_report(property_id=property_id, body=request)

