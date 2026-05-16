# CRM Google Sheet — Tab & Column Reference

Create one Google Sheet (any name) and add **6 tabs** with the headers below. The CSV files in this folder are header-only and meant to be pasted into row 1 of each tab.

> The spreadsheet ID (the `<X>` in `https://docs.google.com/spreadsheets/d/<X>/edit`) goes into your `.env` as `CRM_SPREADSHEET_ID` AND into the `Config` Set node inside n8n.

---

## Tab `test_fixtures` — regression set & accuracy meter

| Column            | Type     | Notes                                                                 |
|-------------------|----------|-----------------------------------------------------------------------|
| `sample_file`     | string   | Filename in `samples/emails/` (e.g. `sample_01_lead_rfp.eml`)         |
| `expected_category` | enum   | One of `LEAD`, `SUPPORT`, `SPAM`, `OTHER`                             |
| `message_id`      | string   | Populated after the email is delivered to the test inbox              |
| `actual_category` | formula  | `=IFERROR(VLOOKUP(C2, leads!A:G, 7, FALSE), …)` — see formula below   |
| `match`           | formula  | `=B2=D2` → TRUE / FALSE                                               |
| `notes`           | string   | Free text — why a miss happened                                       |

Top-of-tab accuracy cell:

| Cell | Formula |
|------|---------|
| `G1` | `="Accuracy: "&COUNTIF(E:E,TRUE)&"/"&(COUNTA(B:B)-1)` |

### `actual_category` lookup formula (paste into D2 and fill down)

The neat trick is to look the same `message_id` up in **every category tab** and report whichever one contains it.

```text
=IFERROR(IF(MATCH(C2, leads!A:A, 0)>0, "LEAD"),
   IFERROR(IF(MATCH(C2, support!A:A, 0)>0, "SUPPORT"),
     IFERROR(IF(MATCH(C2, spam!A:A, 0)>0, "SPAM"),
       IFERROR(IF(MATCH(C2, other!A:A, 0)>0, "OTHER"), ""))))
```

---

## Tab `leads` — sales pipeline

Columns (in order — **do not reorder**, n8n Sheets node maps by index):

```
message_id | received_at | sender_email | sender_domain | subject | body_preview | priority | confidence | status | created_at
```

| Field | Source | Notes |
|---|---|---|
| `message_id` | Gmail Trigger | Primary key for idempotency |
| `received_at` | Gmail Trigger | ISO 8601 UTC |
| `sender_email` | Cleaned by `Clean email` | Just the email, not the display name |
| `sender_domain` | Cleaned | Lowercased |
| `subject` | `subject_clean` | Re:/Fwd: stripped |
| `body_preview` | `body_preview` | First 500 chars of `body_clean` |
| `priority` | Classifier | `LOW` / `MEDIUM` / `HIGH` |
| `confidence` | Classifier | Float 0-1 |
| `status` | Literal | `NEW` on insert; humans update to `QUALIFYING` / `CLOSED` etc. |
| `created_at` | n8n `$now.toISO()` | ISO 8601 UTC |

---

## Tab `support` — bug reports / how-to questions

```
message_id | received_at | sender_email | subject | body_preview | confidence | created_at
```

---

## Tab `spam` — promotional / cold outreach

```
message_id | received_at | sender_email | subject | confidence | created_at
```

> No body preview — saves Sheet space and avoids surfacing SEO spam back to humans.

---

## Tab `other` — internal / automated / misc

```
message_id | received_at | sender_email | subject | confidence | created_at
```

---

## Tab `errors` — failure log (written by error-handler.json)

```
message_id | received_at | error_stage | error_message | raw_payload | created_at
```

| Field | Notes |
|---|---|
| `message_id` | Best-effort — may be blank if failure was pre-Trigger |
| `error_stage` | `$json.execution.lastNodeExecuted` |
| `error_message` | First 280 chars of `$json.execution.error.message` |
| `raw_payload` | `JSON.stringify($json).slice(0, 1000)` |

---

## Quick paste-in (Google Sheets keyboard recipe)

1. Open your sheet → create tab → name it exactly per above.
2. Open the matching CSV in this folder (`leads.csv`, etc.) in any plain editor.
3. Select-all → copy.
4. In the Sheet tab, cell `A1` → Ctrl+Shift+V (paste values) → "Split text to columns: comma".

You should now have row 1 populated with the header and rows 2+ empty.

---

## Why explicit column mapping (PDF §3.3)?

The PDF forbids "all fields" shortcuts in Sheets nodes. We map each field explicitly in n8n because:

1. **It catches schema drift.** If someone renames `subject_clean` to `clean_subject`, the Sheets node breaks loudly — better than silent garbage in a column.
2. **It documents the contract.** Reading the node tells you the exact shape of a row without running the workflow.
3. **It's idempotency-friendly.** The mapping happens before the lookup, so `message_id` is in a predictable place when we check for duplicates.
