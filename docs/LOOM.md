# Loom — Recording Script & Link

**Final recording:** https://www.loom.com/share/PLACEHOLDER-TO-BE-FILLED-AFTER-RECORDING
> Replace the URL above with the real Loom share link once recorded. Until then, this doc *is* the script.

**Target length:** 6–8 minutes (PDF §"Demo" says ≤ 10 mins).
**Format:** Screen-capture of n8n + browser tabs for Gmail, Sheet, Slack. Inline webcam bubble.

---

## Scene-by-scene script

### 0:00–0:30 — Hook & framing

> "Hi, I'm <name>, and this is the Lead Inbox Triage Bot for Grootan Internal Training Batch May 2026. In the next 7 minutes I'll show you a production-style n8n workflow that classifies incoming emails with an LLM, logs every result to a Google Sheets CRM, drafts a personalised reply for every lead, posts a Slack notification, and never — ever — auto-sends an outbound email."

Visual: README badge + the title sticky note on the Phase 5 canvas.

### 0:30–1:30 — Tour the canvas (PDF demo item 1)

> "Here's the final workflow on the n8n canvas. The eight colour-coded sticky-note sections map 1-to-1 to the phases in the exercise PDF — trigger, pre-process, classify, route, log, draft + notify, and hardening. Every functional node has a description in its `notes` field, so hovering tells you exactly what it does."

Visual: zoom out to show all 37 nodes; hover 3 nodes to surface their notes.

### 1:30–2:15 — Live ingest (PDF demo item 2)

> "I'll forward a fresh email into the test inbox. The Gmail Trigger polls every minute — within 60 seconds you should see a new execution start."

Visual: send sample_03_lead_quote.eml from a second Gmail tab → n8n Executions list updates.

### 2:15–3:30 — Classification + parse safety net (PDF demo item 3)

> "Open the execution. The classifier returns strict JSON because we use `response_format = json_object` on the OpenAI node. The Parse classification function node is a defensive safety net — even if the model hallucinates prose, the downstream switch will see one of the four allowed categories with `parse_failed = true`."

Visual: click into the execution → show Classify email input/output → show Parse classification's output schema.

### 3:30–4:30 — Route, log, idempotency (PDF demo item 4)

> "The switch routes to LEAD here. The Lookup-leads → IF-already-in-leads pre-check is what makes the workflow idempotent. If I re-run this same email — which I will now — the Lookup hit returns the prior message_id, the IF goes to the duplicate-skipped branch, no row is appended, no draft is created, no Slack ping. Re-running the exact same 10 seed emails three times produced zero duplicates."

Visual: re-execute → highlight the Duplicate skipped (leads) node going green.

### 4:30–5:30 — Draft + Slack (PDF demo item 5)

> "On a fresh LEAD execution, the Draft reply node generates a personalised reply. The Gmail node is hard-wired to `createDraft` — never `send`. Then a 250ms Wait node hands off to the Slack card, which uses an attachment-style block kit with a colour-coded header — red for HIGH priority, amber for MEDIUM, green for LOW. The Slack card has an Open-draft-in-Gmail button that deep-links into the actual draft."

Visual: open Gmail Drafts (verify it's there, Sent folder is empty); click the Slack button → the Gmail draft opens.

### 5:30–6:30 — Error handler (PDF demo item 6)

> "Let me deliberately break it. I'll set the OpenAI credential to garbage and re-trigger an email. The execution fails at Classify email, the Error Trigger sub-workflow fires automatically, a row appears in the errors tab of the Sheet, and a red alert posts to #training-leads-errors. Restore the credential and the next execution succeeds — no manual retry needed."

Visual: edit credential → re-send → show errors tab + Slack alert.

### 6:30–7:15 — Cost & metrics (PDF demo item 7)

> "Total LLM spend across the full sprint — five phases, twelve test runs, and the regression set — was $0.032. That's 0.64% of the $5 cap from the PDF rubric. Accuracy is 9 out of 10 on the seed set; the one miss is a mailer-daemon bounce that the classifier called LEAD, and there's already a few-shot example for it in the next prompt iteration. Repo is at github.com/tejash-sr/n8n, full self-evaluation in docs/SELF_EVALUATION.md. Thanks for watching."

Visual: pop OpenAI Usage dashboard → docs/COST_TRACKING.md → repo README.

---

## Pre-recording checklist

- [ ] Workflow imported and **Active** in local n8n.
- [ ] Error handler workflow imported and **Active**.
- [ ] Test Gmail inbox has all 10 fixture emails archived (so a fresh send is visibly new).
- [ ] Sheet tabs `leads / support / spam / other / errors / test_fixtures` exist.
- [ ] Slack channels `#training-leads` and `#training-leads-errors` exist and bot is invited.
- [ ] Browser tabs pre-opened: n8n, Gmail inbox, Gmail Drafts, Sheet, Slack, OpenAI Usage.
- [ ] Webcam framed top-right, mic level checked.
- [ ] Loom set to 1080p, 30fps.

## Post-recording checklist

- [ ] Trim intro/outro dead air.
- [ ] Caption auto-generated and reviewed for proper nouns ("Grootan", "n8n", "OpenAI").
- [ ] Share link permissions: **Anyone with the link can view**.
- [ ] Replace the URL at the top of this file with the real link.
- [ ] Add the link to the top of `README.md` (Loom badge).
- [ ] Drop the link in the PR description on github.com/tejash-sr/n8n.
