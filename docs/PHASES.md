# Build Guide — Phase by Phase

This document walks through how to **build** the workflow incrementally so each commit in `git log` matches a phase from the exercise PDF. If you only want to run the finished bot, jump to [`SETUP.md`](SETUP.md). If you are a reviewer mapping deliverables to acceptance criteria, jump to [`EVALUATION_RUBRIC.md`](EVALUATION_RUBRIC.md).

Each phase below lists:

- **Course topics** (from the PDF, for traceability)
- **Objective** (verbatim from the PDF)
- **Build steps** (what you actually do in n8n)
- **Acceptance criteria** (the verifications from the PDF)
- **Commit message** (so your git log reads cleanly)

---

## Phase 1 — Workflow Foundation & Gmail Trigger

> **Course topics.** n8n Workflow Setup, Credentials, Gmail Trigger Node, Test Data Generation, Naming & Hygiene.
>
> **Objective.** Set up the n8n workflow, configure Gmail and AI credentials safely, build a Gmail trigger that watches a test inbox, and seed the inbox with realistic sample emails. By the end of this phase, every new email that lands in the inbox should appear in n8n's execution log within 60 seconds.

### Build steps

1. **Create the workflow.** n8n → Workflows → New. Name it `LeadInboxTriageBot_<initials>`. Set the description to 2–3 lines (see the template in `v1-phase1.json`). Tags: `training`, `<your-batch>`.
2. **Create credentials in this order** (see [`SETUP.md`](SETUP.md) §2–4):
   - Gmail OAuth2 → *Gmail - Triage Training*
   - Google Sheets OAuth2 → *Sheets - Triage Training*
   - OpenAI (or Anthropic) → *OpenAI - Triage Training*
   - Slack (optional) → *Slack - Triage Bot*
3. **Add a Set node "Config"** at the very top-left of the canvas with these values — they are environment-style switches every downstream node reads:
   - `spreadsheetId` → your CRM spreadsheet ID
   - `slackChannel` → `#training-leads`
   - `signature` → `— Triage Bot (Grootan Training)\nReply not sent automatically. Reviewed by a human before send.`
4. **Add the Gmail Trigger** named `Watch inbox`:
   - Operation: *On New Email*
   - Poll Interval: `1 minute`
   - Label IDs to include: `INBOX` (or a custom `training-triage` label)
   - Additional Fields → *Mark Read = true*, *Format = resolved*, *Attachments = ignore*
5. **Add sticky notes** (right-click canvas → Add Sticky Note) for sections `Trigger`, `Classify`, `Route`, `Log`, `Notify`, `Errors`. Even though only Trigger has nodes today, the empty placeholders prevent later refactors.
6. **Seed the inbox.** Either forward the 10 `.eml` files in [`samples/emails/`](../samples/emails/) into the test inbox by hand, or use the script in [`scripts/seed_inbox.md`](../scripts/seed_inbox.md). Document expected categories by pasting [`sheets/test_fixtures.csv`](../sheets/test_fixtures.csv) into the `test_fixtures` tab.
7. **Export** the workflow JSON via *⋯ → Download* and overwrite [`workflows/v1-phase1.json`](../workflows/v1-phase1.json).

### Acceptance criteria (verbatim from PDF)

- ✅ Gmail trigger captures all 8–10 seed emails reliably.
- ✅ All 3 (or 4) credentials connected and not exposed in node parameters.
- ✅ Workflow named, described, tagged, and exported to repo as `v1-phase1.json`.
- ✅ Test fixtures documented in the `test_fixtures` tab with expected category for each sample.

### Commit message

```
feat(phase-1): bootstrap workflow, Gmail trigger, seed data + test fixtures
```

---

## Phase 2 — AI Classification with Structured Output

> **Course topics.** Set Node, Expression Syntax, AI Node Configuration, Structured Output Prompts, JSON Parsing, Function Node Basics.
>
> **Objective.** Pre-process incoming emails (strip signatures and quoted replies), then classify each email into one of four categories using an AI node that returns strict JSON. Output must be deterministic enough that downstream nodes can route on it without parsing failures.

### Build steps

1. **Add a Set node `Prompts`** in the Classify section. Store the full classification system prompt under field `classification_system_prompt` (see [`prompts/classification_prompt.md`](prompts/classification_prompt.md)). Add `model = gpt-4o-mini` and `temperature = 0.1` here too, so they are editable without opening the AI node.
2. **Add a Set node `Clean email`** with these expressions from [`ARCHITECTURE.md`](ARCHITECTURE.md) §4: `subject_clean`, `body_clean`, `sender_domain`, `sender_name`, plus pass-through `message_id`, `thread_id`, `received_at`, `sender_email`. Enable *Keep Only Set* = false so the original fields remain available.
2a. **Add a second Set node `Compute preview`** directly after `Clean email` with one field: `body_preview = {{$json.body_clean.slice(0,500)}}`. The split is deliberate — n8n does not guarantee evaluation order of sibling fields inside a single Set node, so deriving `body_preview` from the just-computed `body_clean` requires a fresh node boundary. See [`ARCHITECTURE.md`](ARCHITECTURE.md) §4 for the why.
3. **Add an OpenAI Chat node `Classify email`** wired after `Clean email`:
   - Model: `={{ $node["Prompts"].json.model }}`
   - Temperature: `={{ $node["Prompts"].json.temperature }}`
   - Response Format: `json_object`
   - Messages:
     - `system` → `={{ $node["Prompts"].json.classification_system_prompt }}`
     - `user` → `Subject: {{$json.subject_clean}}\n\nBody:\n{{$json.body_clean.slice(0,2000)}}`
4. **Add a Function node `Parse classification`** with the safety-net code from [`ARCHITECTURE.md`](ARCHITECTURE.md) §6. This guarantees one of `LEAD/SUPPORT/SPAM/OTHER` on 100% of inputs.
5. **Run on all 10 seed emails.** In Executions, open each run and confirm the JSON structure. Aim for ≥7/10 accuracy at this stage; iteration on the prompt is expected.
6. **Mangle test.** Temporarily change the prompt to ask the model for poetry → re-run → confirm `Parse classification` returns `category=OTHER, parse_failed=true` without crashing the workflow.
7. **Export** as [`workflows/v2-phase2.json`](../workflows/v2-phase2.json).

### Acceptance criteria

- ✅ Cleaning step handles quoted replies and signatures.
- ✅ Classifier returns strict JSON with 4 fields on 100% of inputs (even malformed AI responses).
- ✅ At least 70% classification accuracy on the seed set.
- ✅ Prompt centralized in a Set node.
- ✅ Workflow exported as `v2-phase2.json`.

### Commit message

```
feat(phase-2): add Clean email + AI classifier with strict JSON safety net
```

---

## Phase 3 — Routing & CRM Logging

> **Course topics.** Switch Node, IF Node, Google Sheets Append, Data Mapping, Idempotency.
>
> **Objective.** Route each classified email to the correct destination using a Switch node, then log every email to the right Google Sheet tab. Logging must be idempotent — re-running a workflow on the same email must not create duplicate rows.

### Build steps

1. **Create CRM tabs** with the schema in [`sheets/CRM_SHEET_TEMPLATE.md`](../sheets/CRM_SHEET_TEMPLATE.md).
2. **Add a Switch node `Route by category`** with 4 named outputs (LEAD / SUPPORT / SPAM / OTHER) **and** a fallback output. Set `options.fallbackOutput = "extra"` and wire the fallback to a new Google Sheets node `Log unknown category to errors` (tab=`errors`) so that any unknown / future category produced by the LLM (e.g. an unexpected enum value) lands in the errors tab instead of being silently dropped.
3. **Add an idempotency pre-check** on each branch (per-category, not collapsed into a single tab):
   - `Lookup <category>` (Google Sheets `Lookup` operation, range `<category>!A:A`, lookup column `message_id`).
   - **IF node `Already in <category>?`** — true branch → `Duplicate skipped (<category>)` Set node which writes `duplicate_skipped=true` + `skip_reason=already_in_<category>_tab` → end. false branch → `Append <category>`.
4. **Add `Append <category>` Sheets nodes** with **explicit column mapping** (no “Map all fields”):
   - `leads` columns: `message_id, received_at, sender_email, sender_domain, subject, body_preview, priority, confidence, status, created_at`
   - `status` literal value `NEW`
   - `created_at` expression `={{$now.toISO()}}`
5. **Test idempotency.** Re-run the workflow on the same 10 emails → 0 new rows. The IF branch should short-circuit.
6. **Fallback test.** Inject a manual execution where `category=UNKNOWN` (use a Set node temporarily) → confirm it reaches the `errors` tab.
7. **Export** as [`workflows/v3-phase3.json`](../workflows/v3-phase3.json).

### Acceptance criteria

- ✅ All 10 seed emails routed to the correct Sheet tab.
- ✅ Idempotency verified by re-running on the same inputs with no duplicates.
- ✅ All Sheet tabs have full column coverage.
- ✅ Workflow exported as `v3-phase3.json`.

### Commit message

```
feat(phase-3): switch routing + Sheets CRM with idempotent appends
```

---

## Phase 4 — Draft Reply Generation & Slack Notification

> **Course topics.** AI Agent Node, Gmail Create Draft Node, Slack Node, Tone Control, Templating, Safety Rails.
>
> **Objective.** For every email classified as LEAD, generate a personalized draft reply using an AI node, save it as a Gmail draft (NEVER send), and post a notification card to a Slack channel so a human sales rep can pick it up.

### Build steps

1. **Add the reply prompt to `Prompts`** Set node under field `reply_system_prompt` (see [`prompts/reply_draft_prompt.md`](prompts/reply_draft_prompt.md)).
2. **Add an OpenAI Chat node `Draft reply`** on the LEAD branch (after the leads append):
   - Model: `={{ $node["Prompts"].json.model }}`
   - Temperature: `0.4`
   - Messages:
     - `system` → `={{ $node["Prompts"].json.reply_system_prompt }}` + signature
     - `user` → JSON-encoded `{name, domain, priority, body}` so the model has all context.
3. **Add a Gmail node `Create Gmail draft`** — *Operation = Create Draft* (and only Create Draft, never Send):
   - `Email Type = text`
   - `To = {{$json.sender_email}}`
   - `Subject = {{$json.subject_clean.startsWith("Re:") ? $json.subject_clean : "Re: " + $json.subject_clean}}`
   - `Message = {{ $node["Draft reply"].json.message.content }}`
   - `Thread ID = {{$json.thread_id}}` *(reply lands in the original conversation)*
4. **Insert a tiny `Wait for draft id` node (Wait, 250 ms)** after `Create Gmail draft`. This is *not* defensive padding — it's a hand-off boundary so `$node["Create Gmail draft"].json.id` is guaranteed to be populated by the time the Slack card templates the `Open draft in Gmail` deep-link.
5. **Add a Slack node `Post lead card`** **after** the `Wait for draft id` node. Use the **attachment-style** Block Kit (not just `blocks`) so the `color` field is honoured by Slack — colour-code by priority: HIGH `#E01E5A`, MEDIUM `#ECB22E`, LOW `#2EB67D`. Include the *Open draft in Gmail* button pointing at `https://mail.google.com/mail/u/0/#drafts/{{$node["Create Gmail draft"].json.id}}`.
5. **Spot-check tone.** Send a "Hi, interested in your services" email → confirm the draft asks clarifying questions instead of fabricating specifics.
6. **Sent-folder check.** Open Gmail → *Sent* → there must be **zero** new outbound emails after the run.
7. **Export** as [`workflows/v4-phase4.json`](../workflows/v4-phase4.json).

### Acceptance criteria

- ✅ Drafts created for every LEAD with no auto-sent emails.
- ✅ Slack notification posted for every LEAD with full card content.
- ✅ Draft length and tone meet the spec on at least 4 of 5 manual samples.
- ✅ Workflow exported as `v4-phase4.json`.

### Commit message

```
feat(phase-4): AI draft reply + Gmail Create Draft + Slack lead card
```

---

## Phase 5 — Error Handling, Demo Prep & Final Hardening

> **Course topics.** Error Trigger Workflow, Retries, n8n Executions Log, Documentation, Demo, README.
>
> **Objective.** Make the workflow safe to leave running unattended. Handle every plausible failure mode (AI down, Sheets quota exceeded, Gmail rate-limited, malformed payloads). Document the workflow clearly enough that someone outside the team could pick it up and run it. Then demo it on Day 5.

### Build steps

1. **Create the second workflow** `LeadInboxTriageBot_ErrorHandler_<initials>`. Add an `Error Trigger` node, then:
   - Set node `Build error row` — see [`ARCHITECTURE.md`](ARCHITECTURE.md) §10.
   - Google Sheets node `Append errors row` → tab `errors`.
   - Slack node `Post error alert` → `#training-leads-errors`.
   - Activate this workflow.
2. **Wire it in.** Open the main workflow → *Settings → Error Workflow* → select `LeadInboxTriageBot_ErrorHandler_<initials>`. Save.
3. **Configure retries** on every external-call node: `Retry On Fail = true`, `Max Tries = 3`, `Wait Between Tries = 5000`. This includes the error handler's own `Append errors row` + `Post error alert` nodes — if the *error logging* itself flakes (e.g. Sheets quota during a thundering herd), retrying it 3× is the only chance you have of capturing the trail. Do **not** retry on validation errors — n8n's HTTP error class distinction handles this if you keep the default *Retry on HTTP errors only* checkbox enabled. Otherwise restrict explicitly. **Note:** the Gmail Trigger has no in-node retry surface — it relies on its own internal 1-minute polling cycle (see [`RUNBOOK.md`](RUNBOOK.md) §8.1).
4. **Regression run.** Re-send all 10 seed emails. Open the `test_fixtures` tab → the formulas in column `actual_category` and `match` (see template) should fill in automatically. Target ≥ 8/10 — iterate the classification prompt if you fall short.
5. **Simulated failure test.** In n8n → Credentials → temporarily set the OpenAI key to garbage → send a new email → confirm the error handler logs a row in `errors` and posts to Slack. Restore the credential.
6. **Loom recording.** Follow the script in [`SETUP.md`](SETUP.md) §10.
7. **Export** as [`workflows/v5-phase5-final.json`](../workflows/v5-phase5-final.json) and the error handler as [`workflows/error-handler.json`](../workflows/error-handler.json).

### Acceptance criteria

- ✅ Error handler workflow runs on simulated failures.
- ✅ Retries configured on all external-call nodes.
- ✅ Seed-set accuracy ≥80%.
- ✅ README complete.
- ✅ Loom recorded.
- ✅ Final workflow exported as `v5-phase5-final.json`.

### Commit message

```
feat(phase-5): error handler workflow + retries + regression hardening
```

---

## Bonus Challenges (optional)

The PDF lists three bonuses (multi-tenant prompts, self-improving classifier, daily digest). Design notes for each live in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) §"v2 ideas" — if you ship any, add a Phase 6 commit and a screenshot.
