# Google Analytics 4 (MCP Gateway) — Astera Studio Skill

Use this skill when the user asks about **Google Analytics 4 (GA4)**: traffic, sessions, users, events, conversions, funnels, realtime, dimensions, metrics, **properties**, or **accounts** — not Google Ads campaigns.

## MCP tools you MUST use (gateway)

Do **not** use Ads tools (`ads_*`) for analytics questions. This gateway exposes:

| Step | Tool | Purpose |
|------|------|--------|
| Discovery | `ga4_list_accounts` | List GA4 **accounts** (Admin API). |
| Discovery | `ga4_list_properties` | List **properties**; read `name` like `properties/123456789` to get numeric **property_id**. |
| Reporting | `ga4_run_report` | Standard **Data API** report (`runReport`). |
| Realtime | `ga4_run_realtime_report` | **Realtime** snapshot (`runRealtimeReport`). |

## OAuth / scope

The user’s token must include **Google Analytics read** scope (e.g. `https://www.googleapis.com/auth/analytics.readonly`). If GA4 calls fail with 401/403, the connection may be Ads-only — re-authorize with the combined app or add Analytics scope.

## `ga4_run_report` — `request` body shape

The second argument **`request`** is a **JSON object** matching Google’s **`RunReportRequest`**, not GAQL and not Google Ads syntax.

Include as needed:

- `dateRanges` — e.g. `[{"startDate": "2025-01-01", "endDate": "2025-01-31"}]` (strings `YYYY-MM-DD` or relative like `yesterday`, `7daysAgo` per API docs).
- `dimensions` — e.g. `[{"name": "date"}], [{"name": "sessionSource"}]`.
- `metrics` — e.g. `[{"name": "sessions"}], [{"name": "totalUsers"}]`.
- Optional: `dimensionFilter`, `metricFilter`, `orderBys`, `limit`, `offset`, `keepEmptyRows`, etc.

**Property:** pass **`property_id`** as digits only (e.g. `123456789`) or `properties/123456789`. The gateway checks the property is **accessible** to the user.

## `ga4_run_realtime_report` — `request` body

Same idea: object matching **`RunRealtimeReportRequest`** — `dimensions`, `metrics`, optional filters. No historical `dateRanges` in the same way as standard reports; follow Data API realtime docs.

## Workflow (always)

1. If the user did not give a **property ID**, call **`ga4_list_properties`** (and optionally `ga4_list_accounts` to narrow by account).
2. Pick the correct **numeric** property id from `name` (`properties/...`).
3. Call **`ga4_run_report`** or **`ga4_run_realtime_report`** with a valid **`request`** dict.
4. Explain results using **dimension** and **metric** names returned in the response headers/rows.

## What NOT to do

- Do not use **`ads_search`** or **`ads_list_accessible_customers`** for GA4 questions.
- Do not invent property IDs — discover with **`ga4_list_properties`** first.
- Do not pass GAQL strings into `ga4_run_report`; use the **Data API** JSON structure only.

---

**Connector:** Same custom MCP gateway **`/mcp`** as Ads if OAuth includes **both** Ads and Analytics scopes; otherwise use a connector whose token includes **`analytics.readonly`**.
