"""
Safety helpers for Google Ads mutation tools.

All guards are stateless functions — no class needed — so tools can
call them inline without ceremony.

Environment variables read at call time (so restart is not required
to pick up a change in tests; prod operators should restart):
  ADS_MCP_ENABLE_MUTATIONS            "true" to allow writes (default: disabled)
  ADS_MCP_MAX_DAILY_BUDGET_MICROS     optional hard cap in micros for budget updates;
                                      if NOT set, no cap is enforced — users control
                                      their own budgets and the LLM asks for confirmation.
                                      Only set this if you want an additional operator-level
                                      ceiling (e.g. a shared demo environment).
  ADS_MCP_MUTATION_CUSTOMER_ALLOWLIST CSV of allowed customer_ids; empty = all allowed
  ADS_MCP_AUDIT_LOG_PATH              path to JSON-lines audit file; empty = stderr
"""

import json
import logging
import os
import time
from typing import Any, Optional

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------

def mutations_enabled() -> bool:
    return os.environ.get("ADS_MCP_ENABLE_MUTATIONS", "").strip().lower() == "true"


def enforce_mutations_enabled() -> None:
    if not mutations_enabled():
        raise ToolError(
            "Google Ads mutation tools are disabled on this server. "
            "Set ADS_MCP_ENABLE_MUTATIONS=true in the server environment to enable writes."
        )


# ---------------------------------------------------------------------------
# Destructive-op confirm gate
# ---------------------------------------------------------------------------

def enforce_destructive_confirm(*, validate_only: bool, confirm: bool) -> None:
    """
    For destructive tools (pause, budget change, bid change) the caller must:
      - Either keep validate_only=True (safe preview), OR
      - Pass both validate_only=False AND confirm=True to apply.

    This prevents the LLM from applying a destructive change without an
    explicit double-acknowledgement.
    """
    if not validate_only and not confirm:
        raise ToolError(
            "This is a destructive operation. To apply it, pass both "
            "validate_only=false AND confirm=true after reviewing the preview. "
            "Call with validate_only=true first to see what would change."
        )


# ---------------------------------------------------------------------------
# Budget cap (optional — only enforced when operator explicitly sets the env var)
# ---------------------------------------------------------------------------

def enforce_budget_cap(amount_micros: int) -> None:
    """
    Enforce an operator-set budget ceiling, if configured.

    ADS_MCP_MAX_DAILY_BUDGET_MICROS is intentionally opt-in with no default.
    In SaaS deployments the operator cannot know each user's budget scale, so
    no server-side cap is applied unless the operator explicitly sets the variable.
    User-level safety comes from the LLM always previewing (validate_only=true)
    and asking for explicit confirmation before applying.
    """
    cap_str = os.environ.get("ADS_MCP_MAX_DAILY_BUDGET_MICROS", "").strip()
    if not cap_str:
        return  # No cap configured — user controls their own budgets.

    try:
        cap = int(cap_str)
    except ValueError:
        logger.warning(
            "ADS_MCP_MAX_DAILY_BUDGET_MICROS=%r is not a valid integer; ignoring cap.", cap_str
        )
        return

    if amount_micros > cap:
        cap_dollars = cap / 1_000_000
        requested_dollars = amount_micros / 1_000_000
        raise ToolError(
            f"Requested daily budget ${requested_dollars:.2f} exceeds the operator cap "
            f"of ${cap_dollars:.2f} set on this server. Contact your administrator to "
            "adjust ADS_MCP_MAX_DAILY_BUDGET_MICROS if this cap is too restrictive."
        )


# ---------------------------------------------------------------------------
# Customer allowlist
# ---------------------------------------------------------------------------

def enforce_customer_allowlist(customer_id: str) -> None:
    raw = os.environ.get("ADS_MCP_MUTATION_CUSTOMER_ALLOWLIST", "").strip()
    if not raw:
        return
    allowed = {c.strip() for c in raw.split(",") if c.strip()}
    if customer_id not in allowed:
        raise ToolError(
            f"Mutations are not allowed for customer_id '{customer_id}' on this server. "
            "Contact your administrator to update ADS_MCP_MUTATION_CUSTOMER_ALLOWLIST."
        )


# ---------------------------------------------------------------------------
# Audit logger
# ---------------------------------------------------------------------------

def audit(
    tool_name: str,
    *,
    customer_id: str,
    login_customer_id: Optional[str] = None,
    validate_only: bool,
    confirm: bool = False,
    outcome: str,
    request_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool_name,
        "customer_id": customer_id,
        "login_customer_id": login_customer_id,
        "validate_only": validate_only,
        "confirm": confirm,
        "outcome": outcome,
    }
    if request_id:
        record["request_id"] = request_id
    if extra:
        record.update(extra)

    line = json.dumps(record, ensure_ascii=False)

    log_path = os.environ.get("ADS_MCP_AUDIT_LOG_PATH", "").strip()
    if log_path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return
        except OSError as e:
            logger.warning("ads_mcp audit log write failed (%s); falling back to stderr.", e)

    logger.info("ads_mcp_audit %s", line)
