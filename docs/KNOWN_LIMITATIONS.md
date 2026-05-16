# Known Limitations & v2 Ideas

If we got another week, this is the queue.

---

## 1. Limitations of the current implementation

### 1.1 Gmail polling vs push

We poll every 60s, so the worst-case email-to-draft latency is roughly 60s + AI roundtrip. For training that's fine. For a real `sales@` mailbox, switch to **Gmail Push** (`users.watch` + Pub/Sub) — n8n has a webhook trigger that consumes Pub/Sub messages.

### 1.2 SQLite for state

n8n's default SQLite is single-writer. Fine for one workflow + ~100 emails/day, but a real deployment should swap to Postgres (`DB_TYPE=postgresdb`) and run n8n in queue mode with Redis workers.

### 1.3 Idempotency is per-tab, not global

Pattern A in [`ARCHITECTURE.md`](ARCHITECTURE.md) §7 catches duplicates within a category. If the classifier flips `support → other` between two runs, both rows survive. Pattern B (master `all_messages` tab keyed on `message_id`) closes this hole — it's queued for v2.

### 1.4 Classifier sees plain text only

We strip HTML during cleaning, losing visual signals (logos, buttons) that are strong tells for promotional email. v2 could pass an `is_html_marketing` boolean flag from a cheap regex pre-classifier.

### 1.5 No language detection

The classifier prompt is English-only. A non-English email gets classified anyway, often wrongly. v2: detect language with a cheap call and short-circuit to `OTHER` (or translate first).

### 1.6 Slack card lacks "snooze" / "claim"

Today the Slack card has only an "Open draft" link. v2 should add `Claim` / `Snooze 1h` buttons that write back into the Sheet (status column) via an interactive webhook.

### 1.7 No PII redaction in `errors` tab

`raw_payload` may contain PII from the original email. For a Grootan client deployment we'd add a redaction pass (regex on emails / phones / common ID patterns) before logging.

### 1.8 Single LLM provider

The workflow assumes OpenAI in the JSON. Anthropic works with a two-line node swap, but a failover route (OpenAI down → fall back to Anthropic) would harden the SLA. Pattern: HTTP Request to OpenAI with a 5s timeout, on error fall through to an Anthropic node.

---

## 2. v2 ideas (bonus challenges from the PDF, with concrete designs)

### 2.1 Bonus A — Multi-tenant Prompt Templates

**Goal.** Serve `sales@grootan` and `hello@deliverhub` from one workflow with different reply tones / channels — without duplicating the workflow.

**Design.**

1. Add a `tenants` tab to the CRM Sheet:

   | tenant_id | inbox_label | classification_examples_json | reply_tone | slack_channel | signature |
   |-----------|-------------|------------------------------|------------|---------------|-----------|
   | grootan   | training-triage | […few-shots…]            | warm, professional | #leads-grootan | "— Grootan Team" |
   | deliverhub| dh-triage   | […few-shots…]                | concise, casual    | #leads-dh      | "— DeliverHub" |

2. After the Gmail Trigger, a `Sheets read tenants` node loads all configs.
3. A `Set` node `Resolve tenant` matches `to` address (or label) to a `tenant_id` and projects the right `reply_tone`, `slack_channel`, `signature`, etc.
4. The classifier prompt template uses `{{ $json.tenant.classification_examples_json }}` for few-shots, the reply prompt uses `{{ $json.tenant.reply_tone }}`, and the Slack node uses `{{ $json.tenant.slack_channel }}`.

Zero workflow duplication, all behaviour driven by sheet rows.

### 2.2 Bonus B — Self-improving Classifier (feedback loop)

**Goal.** Capture human corrections, accumulate them, and use them as few-shots once the dataset is rich enough.

**Design.**

1. Add a `Webhook` trigger workflow `LeadInboxTriageBot_Feedback`. POST `/feedback` with `{message_id, correct_category, corrected_by}`.
2. Append the row to a `feedback` tab in the CRM Sheet.
3. Once the tab has ≥ 20 rows, a scheduled workflow rebuilds `Set node "Prompts"` field `classification_few_shots` by sampling 2 examples per category from the feedback tab.
4. Future classifications include those few-shots automatically.
5. Track a precision metric in the `feedback` tab to confirm the loop helps (not hurts).

### 2.3 Bonus C — Daily Digest

**Goal.** Slack a daily summary at 09:00 IST on weekdays.

**Design.**

1. New workflow `LeadInboxTriageBot_DailyDigest`. Trigger: Schedule node, weekdays 09:00 `Asia/Kolkata`.
2. Sheets read on `leads`, `support`, `errors` filtered to last 24h.
3. Function node aggregates: total leads, top 3 by priority, count unresolved support, count errors.
4. Slack node posts a Block-Kit card to `#training-leads` with sections per metric.

---

## 3. Productization paths

These take the bot out of "training" and into a real Grootan offering.

- **Grootan Labs template.** Repackage as a 1-click n8n template. Replace credentials with placeholder env vars and add a setup wizard JSON.
- **DeliverHub feature.** Inline the bot as a "Lead Inbox" feature in DeliverHub. The CRM Sheet becomes a Postgres table, and the Gmail OAuth flow becomes part of DeliverHub onboarding.
- **Client pre-sales asset.** Drop into a customer's tenant as a 7-day trial. Pre-built dashboards (Sheet → Looker Studio) make the value visible from day 1.

---

## 4. Quality gates we did NOT add (intentionally)

Listed so reviewers don't think they were missed:

- **No automatic prompt A/B testing.** Out of scope; the PDF asks for a centralized prompt, not an experimentation framework.
- **No fine-tuning.** A 4-way classifier with strict JSON is plenty for the seed-set size. Fine-tuning kicks in around 1k+ labelled examples — we have 10.
- **No SMS / WhatsApp channel.** PDF restricts to email + Slack.
- **No multi-language reply drafting.** PDF examples are English-only.
