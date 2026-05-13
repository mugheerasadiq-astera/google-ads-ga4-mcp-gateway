# Google Ads (MCP Gateway) — Astera Studio Skill

Use this skill when the user asks about **Google Ads**: campaigns, ad groups, keywords, budgets, metrics, conversions, customer accounts, MCC/manager access, GAQL reporting, or **changing** Ads entities (pause/enable, budgets, bids, negatives, new ad groups/RSAs).

If the user asks for **Google Analytics / GA4 / properties / sessions**, stop using Ads tools and use the **GA4** skill instead.

This gateway exposes **10 `ads_*` tools** (3 reads + 7 mutations) on `/mcp`, plus **4 `ga4_*` tools** — **14 tools total** on one connection when both product areas are enabled.

---

## MCP tools (gateway only — exact names)

Listed below: every **`ads_*`** tool (reads, then writes). Together with **`ga4_*`**, one MCP connection exposes **14 tools** total.

### Read tools (always available if OAuth + developer token work)

| Step | Tool | Purpose |
|------|------|--------|
| Discovery | `ads_list_accessible_customers` | Customer IDs the signed-in user can access **directly** from Google’s “accessible customers” list. |
| Metadata | `ads_get_resource_metadata` | Valid **selectable / filterable / sortable** field names for a GAQL `resource` before `ads_search`. |
| Reporting | `ads_search` | GAQL **`searchStream`** on **`customer_id`**: structured **`fields` / `resource` / …** or a full-query string via **`gaql`** or **`query`** (same meaning; some clients send `query`). |

### Write tools (mutations)

Registered on the server but **refused** unless the operator sets **`ADS_MCP_ENABLE_MUTATIONS=true`**. Every write defaults to **`validate_only=true`** (preview). Destructive writes need **`validate_only=false` + `confirm=true`** to apply. All accept optional **`login_customer_id`**, **`developer_token`** (or use server env **`GOOGLE_ADS_DEVELOPER_TOKEN`**).

| Tool | Purpose |
|------|--------|
| `ads_pause_resource` | Pause campaign / ad group / ad (`resource_name`, destructive). |
| `ads_enable_resource` | Enable paused campaign / ad group / ad. |
| `ads_add_campaign_negative_keyword` | Add campaign-level negative keyword (`campaign_id`, `text`, `match_type`). |
| `ads_update_campaign_budget` | Set campaign budget daily `amount_micros` (destructive). |
| `ads_update_ad_group_max_cpc` | Set ad group max CPC in micros (destructive). |
| `ads_create_ad_group` | Create **PAUSED** `SEARCH_STANDARD` ad group under a campaign. |
| `ads_create_responsive_search_ad` | Create **PAUSED** RSA (`headlines`, `descriptions`, `final_urls`, optional `path1`/`path2`). |

**Mutation parameters — important**

- **`customer_id`:** digits only (advertiser account), same as reads. Never use an MCC id here.
- **`resource_name`** (`ads_pause_resource` / `ads_enable_resource`): short form `campaigns/{id}`, `adGroups/{id}`, or `adGroupAds/{adGroupId}~{adId}` — **or** full form from `ads_search` such as `customers/{customer_id}/campaigns/{id}`. If full form is used, the embedded customer id **must** match **`customer_id`**.
- **`campaign_id`**, **`budget_id`**, **`ad_group_id`:** numeric id, or resource-style strings whose **last** segment is numeric (e.g. `customers/888/campaigns/123` → `123`); full `customers/...` forms must match **`customer_id`**.
- **RSA lists:** `headlines`, `descriptions`, and `final_urls` may be native JSON arrays **or** JSON-encoded strings (some MCP clients send strings).

Optional operator limits (see README): **`ADS_MCP_MUTATION_CUSTOMER_ALLOWLIST`**, **`ADS_MCP_MAX_DAILY_BUDGET_MICROS`**, **`ADS_MCP_AUDIT_LOG_PATH`**.

Do **not** use legacy names `search` or `list_accessible_customers` on this gateway.

Full mutation workflow and LLM rules: see **Writes (mutations)** below.

---

## `login_customer_id` (MCC / manager context) — when REQUIRED vs optional

**What it is:** The **manager (MCC) customer ID** Google uses as **login context** when the OAuth user accesses an **advertiser account that sits under a manager** in the hierarchy. API header equivalent: `login-customer-id`. Pass **digits only** (strip dashes), e.g. `9091929221`.

### A) `ads_list_accessible_customers`

- **`login_customer_id`:** Usually **omit** (leave unset / null). This call lists top-level accessible customers; extra login context is rarely needed.
- If a deployment doc says otherwise, follow product config — default is **not required**.

### B) `ads_get_resource_metadata`

- **`login_customer_id`:** **Not required** for normal use. Field metadata is global for the API version.
- **Optional:** Pass only if your org’s gateway policy requires it; omit by default.

### C) `ads_search` — decision rules (read in order)

1. **You do not know which `customer_id` to query**  
   - Call **`ads_list_accessible_customers`** first, then ask the user which account to use if several exist.

2. **Target account is “directly linked” to the user** (common: the advertiser ID appears in `ads_list_accessible_customers` and the user signs in as that account or has direct access without going through an MCC login)  
   - Try **`ads_search`** with **`login_customer_id` omitted** first.  
   - If the call **succeeds**, **do not** add MCC unless the user asks to switch context.

3. **Target account is under a manager (MCC) — typical corporate / agency setup**  
   - Google often requires **`login_customer_id` = the MCC (manager) customer ID** that **owns or links** the client `customer_id` you are querying.  
   - **Required on `ads_search`** in that case.  
   - **Do not guess** the MCC. Use, in this order:  
     - Connection / tenant config injected by Astera (if your product stores **MccCustomerId**), **or**  
     - A value the **user explicitly provided** (skill block, chat message), **or**  
     - Ask the user (see **“Ask the user”** below).

4. **You are unsure** whether the account is MCC-managed  
   - Run **`ads_list_accessible_customers`**.  
   - If the **`customer_id`** the user wants is **in** that list, try **`ads_search`** **without** `login_customer_id` first.  
   - If Google returns **permission / customer / login context** errors (often mentioning manager, login customer, or inaccessible customer), treat as **MCC context missing or wrong** → ask for **manager (MCC) customer ID** and retry with **`login_customer_id`**.

### D) All **`ads_*`** mutation tools (pause, budget, CPC, etc.)

Use the **same** `login_customer_id` rules as **`ads_search`**: when the advertiser is under an MCC, pass the manager id as **`login_customer_id`** on each mutation call if required for access. Never use the MCC as **`customer_id`**.

### “Ask the user” — MCC (copy/adapt)

If `login_customer_id` is missing and errors or product rules indicate MCC context:

> To query Google Ads account **`{customer_id}`**, I need the **Manager (MCC) customer ID** you use in Google Ads (digits only, no dashes — e.g. `9091929221`).  
> You can find it in Google Ads: **Tools & settings → Setup → Account access** (manager account), or ask your admin.  
> Once you send it, I’ll pass it as **`login_customer_id`** on each reporting call.

If the user says they **only** use a single account with **no** MCC, retry without `login_customer_id` and respect their answer — do not insist on MCC if not applicable.

---

## Spend, totals, and hierarchy (avoid the common 400s)

Skills reduce mistakes; they cannot **guarantee** a fixed error rate—models still mis-map names to IDs or retry randomly. Follow this playbook so **one** coherent path succeeds instead of many failing calls.

### Roles of IDs (do not swap)

| Role | Parameter | Use for |
|------|-----------|--------|
| **Advertiser account** (runs ads, has spend) | **`customer_id`** on `ads_search` | Any query that includes **`metrics.*`** (e.g. `metrics.cost_micros`). |
| **Manager (MCC)** | **`login_customer_id`** only | Login context when the OAuth user reaches a **client** account through a manager. **Not** the `customer_id` for spend totals. |

- If Google returns **`REQUESTED_METRICS_FOR_MANAGER`**, you used an **MCC id as `customer_id`**. Switch **`customer_id`** to the **client** advertiser id; keep the MCC in **`login_customer_id`** if required.

### GAQL for “how much did we spend last calendar year?”

1. Resolve the **client** numeric **`customer_id`** for the account that actually serves ads (ask the user if the name matches several ids).
2. Call **`ads_search`** with that **`customer_id`**. If the gateway says the id is **not directly accessible**, retry with the correct **`login_customer_id`** (MCC) — do **not** iterate every id from **`ads_list_accessible_customers`** as `customer_id` unless each is a deliberate candidate the user cares about.
3. Prefer **`FROM customer`** or **`FROM campaign`** with **`metrics.cost_micros`** and **`segments.date`** in the **`WHERE`** (e.g. `BETWEEN '2025-01-01' AND '2025-12-31'`). Sum **`cost_micros`** in your reply and divide by **1_000_000** for currency.
4. **Do not** `SELECT metrics.cost_micros ... FROM customer_client` — Google returns **`PROHIBITED_METRIC_IN_SELECT_OR_WHERE_CLAUSE`** (that resource does not support those metrics). Listing clients is separate from **spend** queries.

### After a Google API error

Read the **`queryError`** / message once, then change **one** thing (wrong id role, missing `login_customer_id`, or invalid resource/metric combo). **Do not** blindly cycle through unrelated customer ids or alternate MCCs unless the user asked to compare accounts.

---

## `developer_token`

- **Required** for all Ads tools. The gateway resolves it in this order: **`developer_token` on the tool call** → else **`GOOGLE_ADS_DEVELOPER_TOKEN`** in the **MCP server process environment**.
- **Astera Studio / LLM:** clients often do **not** pass `developer_token` on each call. In that case the **operator must set `GOOGLE_ADS_DEVELOPER_TOKEN`** on the machine (or `.env` next to the gateway) **before** `py -m ads_mcp_gateway.server`, then restart the server. Without it, `ads_list_accessible_customers` fails immediately with *"developer_token is required"*.
- In SaaS, prefer **per-call** `developer_token` from tenant/connection settings when your product supports injecting it into MCP tool calls.
- If the tool errors about developer token, tell the user: set **`GOOGLE_ADS_DEVELOPER_TOKEN`** for the gateway process, or configure the connection to send **`developer_token`** per call.

---

## Workflow (recommended order)

1. Resolve **`customer_id`** (user input or `ads_list_accessible_customers`).
2. Apply **`login_customer_id`** rules above; **ask the user** if MCC is required and unknown.
3. Optionally call **`ads_get_resource_metadata`** for the **`resource`** you will use in `FROM` when building a **structured** `ads_search` (skip if you already pass a complete **`gaql`** or **`query`** string).
4. Call **`ads_search`** with valid GAQL (structured args, **`gaql`**, or **`query`**); on failure, check errors for **wrong/missing `login_customer_id`** before rewriting the whole query. If Google returns **`UNRECOGNIZED_FIELD`**, call **`ads_get_resource_metadata`** for that resource and only select fields listed as **selectable** — do not invent names (e.g. `campaign.start_date` / `campaign.end_date` are often invalid; use metadata for the API version you run against).
5. Treat money metrics as **micros** unless already converted in the response.
6. **If the user asked to change Ads entities** (pause, budget, bids, negatives, create ad group/RSA): use the **write tools** in **Writes (mutations)** only after confirming mutations are enabled; otherwise explain the server needs **`ADS_MCP_ENABLE_MUTATIONS=true`**. Use **`ads_search`** first to obtain **`resource_name`**, **`campaign_id`**, **`budget_id`**, and **`ad_group_id`** values you pass into mutation tools.

---

## GAQL (v24)

- Use **`ads_get_resource_metadata`** instead of guessing **`resource`**, **`fields`**, or **`conditions`**.
- Date filters: **`YYYY-MM-DD`** with dashes where used (e.g. `segments.date`).
- **`customer_id`** on the tool call: digits only, no dashes.

**How to pass the query into `ads_search`:**

1. **Structured** — `resource`, `fields`, and optional `conditions`, `orderings`, `limit`. Prefer **native JSON arrays** for list parameters. Some MCP clients serialize lists as strings (including JSON array text like `'["campaign.name"]'`); the gateway accepts that, but native arrays are clearer for validators and logs.
2. **`gaql`** or **`query`** — One string with the full `SELECT … FROM …` plus optional `WHERE` / `ORDER BY` / `LIMIT`. **`query`** is an alias for **`gaql`** (Astera / some clients send the GAQL string as `query` like Google’s REST body key). If both are set, **`gaql`** wins. When either is set, the structured list arguments above are ignored. If the string omits it, the gateway appends **`PARAMETERS omit_unselected_resource_names=true`** (same as the structured builder).

---

## Writes (mutations)

Mutation tools are only available when the server has **`ADS_MCP_ENABLE_MUTATIONS=true`** set. If mutations are disabled, every write tool returns a clear error — never try read tools as a workaround.

### Write tool reference

| Tool | Purpose | Destructive? | `confirm` needed to apply? |
|------|---------|:---:|:---:|
| `ads_pause_resource` | Pause a campaign, ad group, or ad | Yes | Yes |
| `ads_enable_resource` | Enable a paused campaign, ad group, or ad | No | No |
| `ads_add_campaign_negative_keyword` | Add a negative keyword to a campaign | No | No |
| `ads_update_campaign_budget` | Change a campaign budget's daily amount | Yes | Yes |
| `ads_update_ad_group_max_cpc` | Change an ad group's max CPC bid | Yes | Yes |
| `ads_create_ad_group` | Create a new ad group in PAUSED state | No | No |
| `ads_create_responsive_search_ad` | Create a new RSA in PAUSED state | No | No |

**Destructive** = the change stops serving traffic or increases spend.

### Hard rules for the LLM (follow exactly)

1. **Never call a mutation tool unless the user explicitly asked to change something.** Read tools are always the default. Do not "speculatively apply" to confirm a hypothesis.

2. **Always run a `validate_only=true` call first.** Show the preview (or any Google validation errors) to the user before offering to apply.

3. **Before asking the user to confirm, spell out the real-world consequences in plain language.** Do not just say "this will pause the campaign" — say *what that means*: ads stop running, impressions drop to zero, any scheduled promotions won't serve. For budget/bid changes, state the old value, the new value, and the estimated spend impact if you can calculate it.

4. **Ask the user for explicit confirmation before applying.** Only call again with `validate_only=false` after the user says "yes, apply it" (or equivalent). Never infer consent from context.

5. **For destructive tools also pass `confirm=true` when applying** — the server refuses without it. Only set `confirm=true` after the user has explicitly acknowledged the destructive nature.

6. **MCC rules are unchanged for writes.** Never invent `customer_id` or `login_customer_id`. Apply the same login context resolution as for `ads_search`.

7. **Never use an MCC id as `customer_id` for mutations.** Same rule as reads: MCC can only be `login_customer_id`.

8. **Warn before adding `BROAD` match negative keywords** on Manual CPC Search campaigns — broad negatives can block legitimate traffic unexpectedly. Recommend PHRASE or EXACT and confirm with the user.

9. **For budget and bid changes, always show the user: current value → proposed value → what this means for daily/monthly spend.** Users have full control over their own budgets; your job is to make sure they understand exactly what they are approving.

### "Ask the user before applying" — copy/adapt

After a successful `validate_only=true` call, show a message like this (adapt to the specific operation):

---

> Google Ads validated this change successfully — no changes have been made yet. Here is exactly what would happen if you apply it:
>
> **Operation:** Pause campaign "Summer Sale" (`campaigns/1234567890`)
> **Effect:** All ads in this campaign will stop serving immediately. Clicks and impressions will drop to zero until the campaign is re-enabled.
> **Current status:** ENABLED
> **After applying:** PAUSED
>
> **Do you want me to apply this?** Type "yes, apply" to confirm, or "no" to cancel.

---

For budget changes, include:

> **Current daily budget:** $XX.XX
> **Proposed daily budget:** $YY.YY
> **Change:** +$ZZ.ZZ/day (~$ZZZ.ZZ/month extra at full spend)

Wait for an affirmative before calling again with `validate_only=false` (and `confirm=true` for destructive ops).

### Explicitly out of scope (v1)

- **Removing / deleting entities** — only pausing is exposed; deletion is not available.
- **Bidding strategy switching** (e.g. MANUAL_CPC → MAXIMIZE_CLICKS) — not in v1.
- **Shared negative keyword lists** — next iteration.
- **Performance Max changes** — too high risk for v1.
- **GA4 writes** — no write tools for Analytics.

---

## What NOT to do

- Do not invent **`login_customer_id`** or **`customer_id`**.
- Do not use **`customer_id` = MCC** when selecting **`metrics.*`** (use client id + optional **`login_customer_id`**).
- Do not use **`ads_*`** for GA4 / Analytics questions.
- Do not assume **Standard** access on external accounts with a **Test** developer token.
- Do not apply mutations without first previewing with `validate_only=true` and getting user confirmation.
- Do not apply destructive mutations without `confirm=true`.

---

**Connector:** Custom MCP → gateway **`/mcp`**, OAuth including **`https://www.googleapis.com/auth/adwords`**.
