"""
GA4 MCP tools.

Resilient to common LLM/client parameter naming variations:
  - property_id / property
  - request / body / report_request
  - dimensions and metrics accepted as top-level params (list of str or list of dicts)
  - dateRanges / date_ranges as top-level param
  - filter / account / account_id on ga4_list_properties
  - JSON-encoded string for the request body
"""

import json
from typing import Any, Dict, List, Optional

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp_gateway.coordinator import mcp
from ads_mcp_gateway import ga4_utils


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_property_id(property_id: Optional[str], property: Optional[str]) -> str:  # noqa: A002
    """Accept property_id or the alias 'property'."""
    raw = (property_id or "").strip() or (property or "").strip()
    if not raw:
        raise ToolError(
            "property_id is required. Pass the numeric GA4 property id "
            "(e.g. '123456789' or 'properties/123456789')."
        )
    return raw


def _coerce_dim_or_metric(value: Any) -> List[Dict[str, str]]:
    """
    Accept multiple forms:
      ["sessionSource", "date"]                    → [{"name":"sessionSource"}, {"name":"date"}]
      [{"name":"sessionSource"}, {"name":"date"}]   → pass-through
      "sessionSource,date"                          → split then wrap
      '["sessionSource"]'                           → parse JSON then wrap
    """
    if not value:
        return []
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("["):
            try:
                value = json.loads(s)
            except json.JSONDecodeError:
                value = [item.strip() for item in s.split(",") if item.strip()]
        else:
            value = [item.strip() for item in s.split(",") if item.strip()]
    if not isinstance(value, list):
        return []
    out: List[Dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            out.append({"name": item.strip()})
    return out


def _coerce_date_ranges(value: Any) -> Optional[List[Dict[str, str]]]:
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    return None


def _resolve_request(
    *,
    request: Optional[Any],
    body: Optional[Any],
    report_request: Optional[Any],
    dimensions: Optional[Any],
    metrics: Optional[Any],
    date_ranges: Optional[Any],
    date_range: Optional[Any],
) -> Dict[str, Any]:
    """
    Build the final RunReportRequest / RunRealtimeReportRequest dict from whichever
    combination of parameters the MCP client provided.

    Priority:
      1. `request` (canonical)
      2. `body` / `report_request` aliases
      3. Assemble from `dimensions`, `metrics`, `date_ranges` / `date_range` top-level params
    """
    # Resolve raw blob
    raw = request or body or report_request

    if raw is not None:
        # May arrive as a JSON-encoded string from some MCP clients
        if isinstance(raw, str):
            s = raw.strip()
            if s.startswith("{") or s.startswith("["):
                try:
                    raw = json.loads(s)
                except json.JSONDecodeError:
                    pass
        if isinstance(raw, dict):
            req = dict(raw)
            # Overlay any explicit top-level params (allow LLM to partially specify)
            d = _coerce_dim_or_metric(dimensions)
            m = _coerce_dim_or_metric(metrics)
            dr = _coerce_date_ranges(date_ranges or date_range)
            if d:
                req["dimensions"] = d
            if m:
                req["metrics"] = m
            if dr:
                req["dateRanges"] = dr
            return req

    # No blob — build from individual params
    req: Dict[str, Any] = {}
    d = _coerce_dim_or_metric(dimensions)
    m = _coerce_dim_or_metric(metrics)
    dr = _coerce_date_ranges(date_ranges or date_range)
    if d:
        req["dimensions"] = d
    if m:
        req["metrics"] = m
    if dr:
        req["dateRanges"] = dr
    return req


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_list_accounts() -> List[Dict[str, Any]]:
    """
    Google Analytics 4 — discovery: list Admin API accounts for the OAuth user.

    OAuth token must include Analytics scope (e.g. analytics.readonly). Use before
    picking a property; not for Google Ads (use `ads_list_accessible_customers`).

    Returns:
        List of account resource objects (each has `name` like `accounts/2625639`).
    """
    return await ga4_utils.list_accounts()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_list_properties(
    account_id: str | None = None,
    account: str | None = None,
    filter: str | None = None,  # noqa: A001
) -> List[Dict[str, Any]]:
    """
    Google Analytics 4 — discovery: list properties the user can access.

    The Admin API requires a parent account filter. This tool accepts:
      - `account_id`: numeric id (e.g. `2625639`) or `accounts/2625639`
      - `account`: alias of `account_id` (some clients use this name)
      - `filter`: `parent:accounts/ID` or `ancestor:accounts/ID` string

    If none are set, the gateway lists all linked accounts then all properties (deduped).

    Call before `ga4_run_report` to get the numeric `property_id` from the `name` field
    of each returned property object (e.g. `properties/123456789`).

    Args:
        account_id: Optional GA account id (digits or `accounts/...`).
        account: Alias for `account_id`.
        filter: Optional `parent:accounts/{id}` or `ancestor:accounts/{id}` string.

    Returns:
        List of property objects (includes `name` like `properties/123...`).
    """
    resolved = ga4_utils._normalize_account_id(account_id) or ga4_utils._normalize_account_id(account)
    if not resolved:
        resolved = ga4_utils._parse_account_from_filter(filter)
    return await ga4_utils.list_properties(account_id=resolved)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_run_report(
    property_id: str | None = None,
    request: Dict[str, Any] | str | None = None,
    # ---- common aliases ----
    property: str | None = None,           # noqa: A002
    body: Dict[str, Any] | str | None = None,
    report_request: Dict[str, Any] | str | None = None,
    # ---- direct top-level params (auto-assembled into request) ----
    dimensions: List[str] | str | None = None,
    metrics: List[str] | str | None = None,
    date_ranges: Any = None,
    date_range: Any = None,
) -> Dict[str, Any]:
    """
    Google Analytics 4 — run a Data API `runReport` for one property.

    The `request` body matches Google's `RunReportRequest`:
      dateRanges, dimensions, metrics, dimensionFilter, metricFilter, orderBys, limit, etc.

    For convenience, `dimensions`, `metrics`, and `date_ranges` / `date_range` may also be
    passed **directly** as top-level parameters (lists of strings or of `{name: ...}` dicts)
    instead of being nested inside `request`. If both are present, direct params win.

    The gateway verifies the property is accessible to the OAuth user.

    Args:
        property_id: GA4 property id as digits or `properties/<digits>`.
        request: Full `RunReportRequest` dict (or JSON string). Also: `body`, `report_request`.
        property: Alias for `property_id`.
        dimensions: Dimension list — strings or `{name: ...}` dicts.
        metrics: Metric list — strings or `{name: ...}` dicts.
        date_ranges / date_range: Date range(s) — `{startDate, endDate}` or list thereof.

    Returns:
        API response dict (rows, dimensionHeaders, metricHeaders, rowCount, etc.).
    """
    pid_raw = _resolve_property_id(property_id, property)
    req = _resolve_request(
        request=request,
        body=body,
        report_request=report_request,
        dimensions=dimensions,
        metrics=metrics,
        date_ranges=date_ranges,
        date_range=date_range,
    )
    return await ga4_utils.run_report(property_id=pid_raw, body=req)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def ga4_run_realtime_report(
    property_id: str | None = None,
    request: Dict[str, Any] | str | None = None,
    # ---- common aliases ----
    property: str | None = None,           # noqa: A002
    body: Dict[str, Any] | str | None = None,
    report_request: Dict[str, Any] | str | None = None,
    # ---- direct top-level params ----
    dimensions: List[str] | str | None = None,
    metrics: List[str] | str | None = None,
) -> Dict[str, Any]:
    """
    Google Analytics 4 — run `runRealtimeReport` (live snapshot).

    Same OAuth scope as other GA4 tools. `request` matches `RunRealtimeReportRequest`:
    dimensions, metrics, optional filters, limit. No historical `dateRanges`.

    `dimensions` and `metrics` may be passed directly as top-level params (strings or
    `{name: ...}` dicts) instead of inside `request`.

    Args:
        property_id: GA4 property id as digits or `properties/<digits>`.
        request: Full `RunRealtimeReportRequest` dict (or JSON string). Also: `body`, `report_request`.
        property: Alias for `property_id`.
        dimensions: Dimension list — strings or `{name: ...}` dicts.
        metrics: Metric list — strings or `{name: ...}` dicts.

    Returns:
        API response dict for the realtime report.
    """
    pid_raw = _resolve_property_id(property_id, property)
    req = _resolve_request(
        request=request,
        body=body,
        report_request=report_request,
        dimensions=dimensions,
        metrics=metrics,
        date_ranges=None,
        date_range=None,
    )
    return await ga4_utils.run_realtime_report(property_id=pid_raw, body=req)
