"""
Google Ads mutation tools for the Astera MCP gateway.

All tools require ADS_MCP_ENABLE_MUTATIONS=true on the server to execute.
All tools default to validate_only=True so the LLM cannot apply changes
without an explicit second call with validate_only=False.
Destructive tools additionally require confirm=True when applying.

v1 tool surface:
  ads_pause_resource            — pause campaign / ad group / ad
  ads_enable_resource           — enable campaign / ad group / ad
  ads_add_campaign_negative_keyword
  ads_update_campaign_budget
  ads_update_ad_group_max_cpc
  ads_create_ad_group           — creates in PAUSED state
  ads_create_responsive_search_ad — creates in PAUSED state
"""

import re as _re
from typing import Any, Dict, List, Optional

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp_gateway.coordinator import mcp
from ads_mcp_gateway import utils, safety

# Short prefixes (without customers/{id}/ prefix) — what the LLM might type directly.
_RESOURCE_SHORT_PREFIXES = ("campaigns/", "adGroups/", "adGroupAds/")

# Full form: customers/{customerId}/{type}/{rest}  — capture both the customer id
# and the rest so we can validate the customer id matches the parameter and rebuild
# a canonical short form.
_FULL_RESOURCE_RE = _re.compile(
    r"^customers/(?P<cust>\d+)/(?P<rest>(?:campaigns|adGroups|adGroupAds)/.+)$"
)


def _extract_numeric_id(
    value: str,
    *,
    name: str,
    customer_id: Optional[str] = None,
) -> str:
    """
    Extract a numeric Google Ads entity id from any reasonable user input.

    Accepts:
      "1234567890"                              → "1234567890"
      "campaigns/1234567890"                    → "1234567890"
      "campaignBudgets/123"                     → "123"
      "customers/{customer_id}/campaigns/1234567890" → "1234567890"   (when customer_id matches)

    Rejects:
      ""                  empty
      "abc"               no digits
      "campaigns/abc"     non-numeric last segment
      "12 34"             contains whitespace / non-digit between digits
      "customers/999/campaigns/123" when customer_id="888"   (account mismatch)

    NEVER use this for adGroupAd resource names (which carry "{group}~{ad}" id).
    """
    s = (value or "").strip()
    if not s:
        raise ToolError(f"{name} must not be empty.")
    if s.isdigit():
        return s

    # Full form: customers/{digits}/{type}/{id}  — verify customer matches if provided.
    full_match = _re.match(r"^customers/(\d+)/[A-Za-z]+/(\d+)$", s)
    if full_match:
        embedded_cust, entity_id = full_match.group(1), full_match.group(2)
        if customer_id is not None and embedded_cust != customer_id:
            raise ToolError(
                f"{name} value {value!r} targets customer '{embedded_cust}' but the "
                f"customer_id parameter is '{customer_id}'. These must match. "
                f"Pass the numeric id only (e.g. '{entity_id}') or use the matching customer_id."
            )
        return entity_id

    # Short form: type/{id}
    short_match = _re.match(r"^[A-Za-z]+/(\d+)$", s)
    if short_match:
        return short_match.group(1)

    raise ToolError(
        f"{name} must be a numeric id (e.g. 1234567890) or a resource name "
        f"like 'campaigns/1234567890'. Got: {value!r}"
    )


def _require_resource_name(*, resource_name: str, customer_id: str) -> str:
    """
    Validate and canonicalize a campaign/adGroup/adGroupAd resource name.

    Accepts both short and full forms. For the full form, the embedded
    customer id MUST match `customer_id` — otherwise we would silently
    target the wrong account.

    Accepted inputs:
      Short:  campaigns/1234567890
              adGroups/9876543210
              adGroupAds/123~456
      Full:   customers/{customer_id}/campaigns/1234567890   (as returned by ads_search)
              customers/{customer_id}/adGroups/9876543210
              customers/{customer_id}/adGroupAds/123~456

    Returns the canonical short form (campaigns/..., adGroups/..., adGroupAds/...)
    so _resolve_status_op always builds the full resourceName from scratch with
    the verified customer_id.
    """
    stripped = (resource_name or "").strip()
    if not stripped:
        raise ToolError("resource_name must not be empty.")

    # Short form is the most common LLM input.
    if any(stripped.startswith(p) for p in _RESOURCE_SHORT_PREFIXES):
        return stripped

    # Full form — must agree with customer_id.
    m = _FULL_RESOURCE_RE.match(stripped)
    if m:
        embedded_cust = m.group("cust")
        if embedded_cust != customer_id:
            raise ToolError(
                f"resource_name targets customer '{embedded_cust}' but the "
                f"customer_id parameter is '{customer_id}'. These must match. "
                "Pass the matching customer_id, or pass the short form "
                "'campaigns/{id}' / 'adGroups/{id}' / 'adGroupAds/{id}~{id}'."
            )
        return m.group("rest")

    raise ToolError(
        "resource_name must be a Google Ads resource name for a campaign, ad group, or ad. "
        "Accepted formats:\n"
        "  Short:  campaigns/1234567890\n"
        "  Short:  adGroups/9876543210\n"
        "  Short:  adGroupAds/1234567890~9876543210\n"
        "  Full:   customers/{customer_id}/campaigns/1234567890  (as returned by ads_search)\n"
        f"Got: {stripped!r}"
    )


def _resolve_status_op(customer_id: str, resource_name: str, status: str) -> Dict[str, Any]:
    """
    Build a single updateMask=status mutate operation.
    resource_name must be in short form (campaigns/..., adGroups/..., adGroupAds/...)
    as returned by _require_resource_name.
    """
    rn_full = f"customers/{customer_id}/{resource_name}"
    update = {"resourceName": rn_full, "status": status}
    if resource_name.startswith("campaigns/"):
        return {"campaignOperation": {"update": update, "updateMask": "status"}}
    if resource_name.startswith("adGroups/"):
        return {"adGroupOperation": {"update": update, "updateMask": "status"}}
    # adGroupAds/
    return {"adGroupAdOperation": {"update": update, "updateMask": "status"}}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
async def ads_pause_resource(
    customer_id: str,
    resource_name: str,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
    validate_only: bool = True,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Google Ads — pause a campaign, ad group, or ad (status → PAUSED).

    DESTRUCTIVE: pausing stops an entity from serving. Always call with
    validate_only=True first to preview, then validate_only=False + confirm=True
    only after the user has explicitly approved.

    ADS_MCP_ENABLE_MUTATIONS must be true on the server.

    Args:
        customer_id: Advertiser account id (digits only).
        resource_name: Resource name for the entity to pause. Accepts:
            campaigns/1234567890
            adGroups/9876543210
            adGroupAds/1234567890~9876543210   (ad group id and ad id joined by '~')
            customers/{customer_id}/campaigns/1234567890  (full form from ads_search)
        login_customer_id: MCC id if needed.
        developer_token: Google Ads developer token (or server env).
        validate_only: True (default) = preview only. False = apply (requires confirm=True).
        confirm: Must be True when validate_only=False to apply a destructive change.

    Returns:
        API response dict (mutateOperationResponses) or validation result.
    """
    safety.enforce_mutations_enabled()
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits.")
    safety.enforce_destructive_confirm(validate_only=validate_only, confirm=confirm)
    rn = _require_resource_name(resource_name=resource_name, customer_id=cid)
    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    safety.enforce_customer_allowlist(cid)
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    op = _resolve_status_op(cid, rn, "PAUSED")
    try:
        result = await utils.mutate_google_ads_rest(
            customer_id=cid,
            mutate_operations=[op],
            validate_only=validate_only,
            developer_token=dt,
            login_customer_id=lcid,
        )
        safety.audit(
            "ads_pause_resource",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            confirm=confirm,
            outcome="success",
            request_id=result.get("requestId"),
            extra={"resource_name": rn},
        )
        return result
    except ToolError as e:
        safety.audit(
            "ads_pause_resource",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            confirm=confirm,
            outcome=f"error: {e}",
            extra={"resource_name": rn},
        )
        raise


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
async def ads_enable_resource(
    customer_id: str,
    resource_name: str,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """
    Google Ads — enable a paused campaign, ad group, or ad (status → ENABLED).

    Call with validate_only=True first to preview, then validate_only=False to apply
    after the user confirms.

    ADS_MCP_ENABLE_MUTATIONS must be true on the server.

    Args:
        customer_id: Advertiser account id (digits only).
        resource_name: e.g. campaigns/1234567890, adGroups/9876543210.
        login_customer_id: MCC id if needed.
        developer_token: Google Ads developer token (or server env).
        validate_only: True (default) = preview only. False = apply.

    Returns:
        API response dict.
    """
    safety.enforce_mutations_enabled()
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits.")
    rn = _require_resource_name(resource_name=resource_name, customer_id=cid)
    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    safety.enforce_customer_allowlist(cid)
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    op = _resolve_status_op(cid, rn, "ENABLED")
    try:
        result = await utils.mutate_google_ads_rest(
            customer_id=cid,
            mutate_operations=[op],
            validate_only=validate_only,
            developer_token=dt,
            login_customer_id=lcid,
        )
        safety.audit(
            "ads_enable_resource",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome="success",
            request_id=result.get("requestId"),
            extra={"resource_name": rn},
        )
        return result
    except ToolError as e:
        safety.audit(
            "ads_enable_resource",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome=f"error: {e}",
            extra={"resource_name": rn},
        )
        raise


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def ads_add_campaign_negative_keyword(
    customer_id: str,
    campaign_id: str,
    text: str,
    match_type: str = "BROAD",
    login_customer_id: str | None = None,
    developer_token: str | None = None,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """
    Google Ads — add a negative keyword to a campaign.

    Supported match_type values: BROAD, PHRASE, EXACT.
    WARNING: Adding BROAD negative keywords on Search campaigns with
    Manual CPC bidding can block valid traffic. Prefer PHRASE or EXACT
    and confirm with the user before applying.

    Call with validate_only=True first, then validate_only=False to apply.
    ADS_MCP_ENABLE_MUTATIONS must be true.

    Args:
        customer_id: Advertiser account id (digits only).
        campaign_id: Numeric campaign id (digits only, no dashes).
        text: Negative keyword text (without match-type symbols).
        match_type: BROAD (default), PHRASE, or EXACT.
        login_customer_id: MCC id if needed.
        developer_token: Google Ads developer token (or server env).
        validate_only: True (default) = preview. False = apply.

    Returns:
        API response dict.
    """
    safety.enforce_mutations_enabled()
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits.")
    camp_id = _extract_numeric_id(campaign_id, name="campaign_id", customer_id=cid)
    mt = match_type.strip().upper()
    if mt not in ("BROAD", "PHRASE", "EXACT"):
        raise ToolError("match_type must be BROAD, PHRASE, or EXACT.")
    kw_text = text.strip()
    if not kw_text:
        raise ToolError("text must not be empty.")

    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    safety.enforce_customer_allowlist(cid)
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    op = {
        "campaignCriterionOperation": {
            "create": {
                "campaign": f"customers/{cid}/campaigns/{camp_id}",
                "negative": True,
                "keyword": {
                    "text": kw_text,
                    "matchType": mt,
                },
            }
        }
    }
    try:
        result = await utils.mutate_google_ads_rest(
            customer_id=cid,
            mutate_operations=[op],
            validate_only=validate_only,
            developer_token=dt,
            login_customer_id=lcid,
        )
        safety.audit(
            "ads_add_campaign_negative_keyword",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome="success",
            request_id=result.get("requestId"),
            extra={"campaign_id": camp_id, "text": kw_text, "match_type": mt},
        )
        return result
    except ToolError as e:
        safety.audit(
            "ads_add_campaign_negative_keyword",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome=f"error: {e}",
            extra={"campaign_id": camp_id, "text": kw_text, "match_type": mt},
        )
        raise


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
async def ads_update_campaign_budget(
    customer_id: str,
    budget_id: str,
    amount_micros: int,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
    validate_only: bool = True,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Google Ads — update a campaign budget's daily amount.

    DESTRUCTIVE: changing the budget directly affects how much can be spent.
    Amounts are in micros: $10/day = 10_000_000 micros.

    Always call with validate_only=True first to show the user the old vs new value.
    Apply with validate_only=False + confirm=True only after the user explicitly approves.
    ADS_MCP_ENABLE_MUTATIONS must be true.

    Args:
        customer_id: Advertiser account id (digits only).
        budget_id: Numeric campaign budget id (from campaign.campaignBudget field).
        amount_micros: New daily budget in micros (1 USD = 1_000_000 micros).
        login_customer_id: MCC id if needed.
        developer_token: Google Ads developer token (or server env).
        validate_only: True (default) = preview. False = apply (requires confirm=True).
        confirm: Must be True when applying.

    Returns:
        API response dict.
    """
    safety.enforce_mutations_enabled()
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits.")
    bid = _extract_numeric_id(budget_id, name="budget_id", customer_id=cid)
    if not isinstance(amount_micros, int) or isinstance(amount_micros, bool):
        raise ToolError("amount_micros must be an integer.")
    if amount_micros <= 0:
        raise ToolError("amount_micros must be a positive integer.")
    safety.enforce_destructive_confirm(validate_only=validate_only, confirm=confirm)
    safety.enforce_budget_cap(amount_micros)

    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    safety.enforce_customer_allowlist(cid)
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    op = {
        "campaignBudgetOperation": {
            "update": {
                "resourceName": f"customers/{cid}/campaignBudgets/{bid}",
                "amountMicros": str(amount_micros),
            },
            "updateMask": "amountMicros",
        }
    }
    try:
        result = await utils.mutate_google_ads_rest(
            customer_id=cid,
            mutate_operations=[op],
            validate_only=validate_only,
            developer_token=dt,
            login_customer_id=lcid,
        )
        safety.audit(
            "ads_update_campaign_budget",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            confirm=confirm,
            outcome="success",
            request_id=result.get("requestId"),
            extra={"budget_id": bid, "amount_micros": amount_micros},
        )
        return result
    except ToolError as e:
        safety.audit(
            "ads_update_campaign_budget",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            confirm=confirm,
            outcome=f"error: {e}",
            extra={"budget_id": bid, "amount_micros": amount_micros},
        )
        raise


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
async def ads_update_ad_group_max_cpc(
    customer_id: str,
    ad_group_id: str,
    cpc_bid_micros: int,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
    validate_only: bool = True,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Google Ads — update an ad group's manual CPC bid cap.

    DESTRUCTIVE: raising bids increases potential spend.
    Applies only to ad groups with Manual CPC bidding.
    Amounts in micros: $1.00 CPC = 1_000_000.

    Call with validate_only=True first. Apply with validate_only=False + confirm=True.
    ADS_MCP_ENABLE_MUTATIONS must be true.

    Args:
        customer_id: Advertiser account id (digits only).
        ad_group_id: Numeric ad group id.
        cpc_bid_micros: New max CPC bid in micros.
        login_customer_id: MCC id if needed.
        developer_token: Google Ads developer token (or server env).
        validate_only: True (default) = preview. False = apply (requires confirm=True).
        confirm: Must be True when applying.

    Returns:
        API response dict.
    """
    safety.enforce_mutations_enabled()
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits.")
    agid = _extract_numeric_id(ad_group_id, name="ad_group_id", customer_id=cid)
    if not isinstance(cpc_bid_micros, int) or isinstance(cpc_bid_micros, bool):
        raise ToolError("cpc_bid_micros must be an integer.")
    if cpc_bid_micros <= 0:
        raise ToolError("cpc_bid_micros must be a positive integer.")
    safety.enforce_destructive_confirm(validate_only=validate_only, confirm=confirm)

    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    safety.enforce_customer_allowlist(cid)
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    op = {
        "adGroupOperation": {
            "update": {
                "resourceName": f"customers/{cid}/adGroups/{agid}",
                "cpcBidMicros": str(cpc_bid_micros),
            },
            "updateMask": "cpcBidMicros",
        }
    }
    try:
        result = await utils.mutate_google_ads_rest(
            customer_id=cid,
            mutate_operations=[op],
            validate_only=validate_only,
            developer_token=dt,
            login_customer_id=lcid,
        )
        safety.audit(
            "ads_update_ad_group_max_cpc",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            confirm=confirm,
            outcome="success",
            request_id=result.get("requestId"),
            extra={"ad_group_id": agid, "cpc_bid_micros": cpc_bid_micros},
        )
        return result
    except ToolError as e:
        safety.audit(
            "ads_update_ad_group_max_cpc",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            confirm=confirm,
            outcome=f"error: {e}",
            extra={"ad_group_id": agid, "cpc_bid_micros": cpc_bid_micros},
        )
        raise


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def ads_create_ad_group(
    customer_id: str,
    campaign_id: str,
    name: str,
    cpc_bid_micros: Optional[int] = None,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """
    Google Ads — create a new ad group in PAUSED state inside an existing campaign.

    The ad group is always created PAUSED — it will not serve until explicitly enabled.
    Type is SEARCH_STANDARD. Call with validate_only=True first to preview, then
    validate_only=False to apply after user confirmation.

    ADS_MCP_ENABLE_MUTATIONS must be true.

    Args:
        customer_id: Advertiser account id (digits only).
        campaign_id: Numeric campaign id.
        name: Ad group name.
        cpc_bid_micros: Optional Manual CPC max bid in micros. Omit for smart bidding campaigns.
        login_customer_id: MCC id if needed.
        developer_token: Google Ads developer token (or server env).
        validate_only: True (default) = preview. False = apply.

    Returns:
        API response dict (includes new ad group resourceName on success).
    """
    safety.enforce_mutations_enabled()
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits.")
    camp_id = _extract_numeric_id(campaign_id, name="campaign_id", customer_id=cid)
    ag_name = name.strip()
    if not ag_name:
        raise ToolError("name must not be empty.")
    if cpc_bid_micros is not None:
        if not isinstance(cpc_bid_micros, int) or isinstance(cpc_bid_micros, bool):
            raise ToolError("cpc_bid_micros must be an integer.")
        if cpc_bid_micros <= 0:
            raise ToolError("cpc_bid_micros must be a positive integer.")

    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    safety.enforce_customer_allowlist(cid)
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    ad_group: Dict[str, Any] = {
        "campaign": f"customers/{cid}/campaigns/{camp_id}",
        "name": ag_name,
        "status": "PAUSED",
        "type": "SEARCH_STANDARD",
    }
    if cpc_bid_micros is not None:
        ad_group["cpcBidMicros"] = str(cpc_bid_micros)

    op = {"adGroupOperation": {"create": ad_group}}
    try:
        result = await utils.mutate_google_ads_rest(
            customer_id=cid,
            mutate_operations=[op],
            validate_only=validate_only,
            developer_token=dt,
            login_customer_id=lcid,
        )
        safety.audit(
            "ads_create_ad_group",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome="success",
            request_id=result.get("requestId"),
            extra={"campaign_id": camp_id, "name": ag_name},
        )
        return result
    except ToolError as e:
        safety.audit(
            "ads_create_ad_group",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome=f"error: {e}",
            extra={"campaign_id": camp_id, "name": ag_name},
        )
        raise


# Character limits enforced server-side to match Google's RSA requirements.
_RSA_MAX_HEADLINES = 15
_RSA_MIN_HEADLINES = 3
_RSA_MAX_DESCRIPTIONS = 4
_RSA_MIN_DESCRIPTIONS = 2
_RSA_HEADLINE_MAX_CHARS = 30
_RSA_DESCRIPTION_MAX_CHARS = 90


def _validate_rsa_assets(headlines: List[str], descriptions: List[str]) -> None:
    if not (_RSA_MIN_HEADLINES <= len(headlines) <= _RSA_MAX_HEADLINES):
        raise ToolError(
            f"Provide {_RSA_MIN_HEADLINES}–{_RSA_MAX_HEADLINES} headlines "
            f"(got {len(headlines)})."
        )
    if not (_RSA_MIN_DESCRIPTIONS <= len(descriptions) <= _RSA_MAX_DESCRIPTIONS):
        raise ToolError(
            f"Provide {_RSA_MIN_DESCRIPTIONS}–{_RSA_MAX_DESCRIPTIONS} descriptions "
            f"(got {len(descriptions)})."
        )
    for i, h in enumerate(headlines):
        if len(h) > _RSA_HEADLINE_MAX_CHARS:
            raise ToolError(
                f"Headline {i + 1} is {len(h)} characters; max is {_RSA_HEADLINE_MAX_CHARS}. "
                f"Shorten: {h!r}"
            )
    for i, d in enumerate(descriptions):
        if len(d) > _RSA_DESCRIPTION_MAX_CHARS:
            raise ToolError(
                f"Description {i + 1} is {len(d)} characters; max is "
                f"{_RSA_DESCRIPTION_MAX_CHARS}. Shorten: {d!r}"
            )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def ads_create_responsive_search_ad(
    customer_id: str,
    ad_group_id: str,
    headlines: List[str] | str,
    descriptions: List[str] | str,
    final_urls: List[str] | str,
    path1: str | None = None,
    path2: str | None = None,
    login_customer_id: str | None = None,
    developer_token: str | None = None,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """
    Google Ads — create a Responsive Search Ad (RSA) in PAUSED state.

    The ad is always created PAUSED. Server enforces Google's RSA character
    limits before any API call.

    RSA requirements:
      - headlines: 3–15 items, each ≤ 30 characters.
      - descriptions: 2–4 items, each ≤ 90 characters.
      - final_urls: at least 1 landing page URL.
      - path1/path2: optional display path segments (≤ 15 chars each).

    All list parameters accept native JSON arrays OR a JSON-encoded string
    (e.g. '["Buy Now", "Shop Today"]') for compatibility with MCP clients that
    serialize arrays as strings.

    Call with validate_only=True first to preview, then validate_only=False
    to apply after user confirmation.
    ADS_MCP_ENABLE_MUTATIONS must be true.

    Args:
        customer_id: Advertiser account id (digits only).
        ad_group_id: Numeric ad group id.
        headlines: List of headline strings (3–15, ≤ 30 chars each).
        descriptions: List of description strings (2–4, ≤ 90 chars each).
        final_urls: Landing page URLs (at least 1).
        path1: Optional display URL path 1 (≤ 15 chars).
        path2: Optional display URL path 2 (≤ 15 chars, requires path1).
        login_customer_id: MCC id if needed.
        developer_token: Google Ads developer token (or server env).
        validate_only: True (default) = preview. False = apply.

    Returns:
        API response dict (includes new ad resourceName on success).
    """
    safety.enforce_mutations_enabled()
    cid = utils._normalize_customer_id(customer_id)
    if not cid:
        raise ToolError("customer_id must be digits.")
    agid = _extract_numeric_id(ad_group_id, name="ad_group_id", customer_id=cid)

    # Coerce all list args — some MCP clients send them as JSON-encoded strings.
    h_list = utils.coerce_string_list(headlines, name="headlines") or []
    d_list = utils.coerce_string_list(descriptions, name="descriptions") or []
    url_list = utils.coerce_string_list(final_urls, name="final_urls") or []

    if not url_list:
        raise ToolError("final_urls must contain at least one URL.")
    if path2 and not path1:
        raise ToolError("path2 requires path1 to be set.")
    if path1 and len(path1) > 15:
        raise ToolError(f"path1 exceeds 15 characters: {path1!r}")
    if path2 and len(path2) > 15:
        raise ToolError(f"path2 exceeds 15 characters: {path2!r}")

    _validate_rsa_assets(h_list, d_list)

    lcid = utils._normalize_customer_id(login_customer_id)
    dt = utils._developer_token(developer_token)
    safety.enforce_customer_allowlist(cid)
    await utils.assert_customer_access(customer_id=cid, developer_token=dt, login_customer_id=lcid)

    rsa: Dict[str, Any] = {
        "headlines": [{"text": h} for h in h_list],
        "descriptions": [{"text": d} for d in d_list],
    }
    if path1:
        rsa["path1"] = path1
    if path2:
        rsa["path2"] = path2

    ad: Dict[str, Any] = {
        "finalUrls": url_list,
        "responsiveSearchAd": rsa,
    }
    op = {
        "adGroupAdOperation": {
            "create": {
                "adGroup": f"customers/{cid}/adGroups/{agid}",
                "status": "PAUSED",
                "ad": ad,
            }
        }
    }
    try:
        result = await utils.mutate_google_ads_rest(
            customer_id=cid,
            mutate_operations=[op],
            validate_only=validate_only,
            developer_token=dt,
            login_customer_id=lcid,
        )
        safety.audit(
            "ads_create_responsive_search_ad",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome="success",
            request_id=result.get("requestId"),
            extra={"ad_group_id": agid, "headline_count": len(h_list)},
        )
        return result
    except ToolError as e:
        safety.audit(
            "ads_create_responsive_search_ad",
            customer_id=cid,
            login_customer_id=lcid,
            validate_only=validate_only,
            outcome=f"error: {e}",
            extra={"ad_group_id": agid},
        )
        raise
