# Google Ads (MCP Gateway) — Astera Studio Skill

Use this skill when the user asks about **Google Ads**: campaigns, ad groups, keywords, budgets, metrics, conversions, customer accounts, MCC/manager access, or GAQL reporting.

If the user asks for **Google Analytics / GA4 / properties / sessions**, stop using Ads tools and use the **GA4** skill instead.

---

## MCP tools (gateway only — exact names)

| Step | Tool | Purpose |
|------|------|--------|
| Discovery | `ads_list_accessible_customers` | Customer IDs the signed-in user can access **directly** from Google’s “accessible customers” list. |
| Metadata | `ads_get_resource_metadata` | Valid **selectable / filterable / sortable** field names for a GAQL `resource` before `ads_search`. |
| Reporting | `ads_search` | GAQL **`searchStream`** on **`customer_id`** (read-only): structured **`fields` / `resource` / …** or a single full-query **`gaql`** string. |

Do **not** use legacy names `search` or `list_accessible_customers` on this gateway.

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

- **Required** for all Ads tools unless the hosted gateway sets **`GOOGLE_ADS_DEVELOPER_TOKEN`**.
- In SaaS, prefer **per-call** `developer_token` from tenant/connection settings when available.
- If the tool errors about developer token, ask the user to confirm token is set on the connection or server.

---

## Workflow (recommended order)

1. Resolve **`customer_id`** (user input or `ads_list_accessible_customers`).
2. Apply **`login_customer_id`** rules above; **ask the user** if MCC is required and unknown.
3. Optionally call **`ads_get_resource_metadata`** for the **`resource`** you will use in `FROM` when building a **structured** `ads_search` (skip if you already pass a complete **`gaql`** string).
4. Call **`ads_search`** with valid GAQL (structured args or **`gaql`**); on failure, check errors for **wrong/missing `login_customer_id`** before rewriting the whole query.
5. Treat money metrics as **micros** unless already converted in the response.

---

## GAQL (v24)

- Use **`ads_get_resource_metadata`** instead of guessing **`resource`**, **`fields`**, or **`conditions`**.
- Date filters: **`YYYY-MM-DD`** with dashes where used (e.g. `segments.date`).
- **`customer_id`** on the tool call: digits only, no dashes.

**How to pass the query into `ads_search`:**

1. **Structured** — `resource`, `fields`, and optional `conditions`, `orderings`, `limit`. Prefer **native JSON arrays** for list parameters. Some MCP clients serialize lists as strings (including JSON array text like `'["campaign.name"]'`); the gateway accepts that, but native arrays are clearer for validators and logs.
2. **`gaql`** — One string with the full `SELECT … FROM …` plus optional `WHERE` / `ORDER BY` / `LIMIT`. When **`gaql`** is set, the structured list arguments above are ignored. If the string omits it, the gateway appends **`PARAMETERS omit_unselected_resource_names=true`** (same as the structured builder).

---

## What NOT to do

- Do not invent **`login_customer_id`** or **`customer_id`**.
- Do not use **`customer_id` = MCC** when selecting **`metrics.*`** (use client id + optional **`login_customer_id`**).
- Do not use **`ads_*`** for GA4 / Analytics questions.
- Do not assume **Standard** access on external accounts with a **Test** developer token.

---

**Connector:** Custom MCP → gateway **`/mcp`**, OAuth including **`https://www.googleapis.com/auth/adwords`**.
