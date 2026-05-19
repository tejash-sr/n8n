# Lead Inbox Triage Bot — Local n8n Implementation

> **Grootan Technologies — Internal Training Program**
> A production-style n8n workflow that watches a shared inbox, classifies incoming emails with an LLM, logs them to a Google Sheets CRM, and drafts (never sends) replies for genuine sales leads. Optional Slack notifications surface new leads to the team in real time.

[![n8n](https://img.shields.io/badge/n8n-self--hosted-EA4B71)](https://n8n.io)
[![LLM](https://img.shields.io/badge/LLM-OpenAI%20%7C%20Anthropic-412991)](https://platform.openai.com)
[![Status](https://img.shields.io/badge/status-Phase%205%20complete-success)]()
[![No Auto-Send](https://img.shields.io/badge/Gmail-Draft%20only%20%E2%80%94%20never%20sends-critical)]()

---

## 1. One-paragraph summary

The Lead Inbox Triage Bot turns a noisy shared mailbox into a triaged sales pipeline. A Gmail trigger polls a test inbox every minute. Each new message is cleaned (signatures, reply-chains, RFC headers stripped), then sent to an LLM that returns **strict JSON** with one of four categories — `LEAD`, `SUPPORT`, `SPAM`, `OTHER` — plus a confidence score, a one-sentence reasoning, and (for leads only) a `LOW / MEDIUM / HIGH` priority. A Switch node routes the result to the right tab of a Google Sheets CRM with idempotent appends (no duplicates on re-run). When something is classified as a `LEAD`, a second LLM call drafts an 80–150 word reply that is saved as a Gmail **draft only** (the workflow is hard-wired never to send), and a Slack Block-Kit card is posted to `#training-leads`. A separate Error Trigger sub-workflow catches every failure, writes a row to an `errors` tab, and alerts `#training-leads-errors`. The whole thing runs locally on Docker + n8n with one `docker compose up`.

---

## 2. Architecture at a glance

```
┌──────────────┐    ┌───────────────┐    ┌────────────────┐    ┌──────────────┐
│ Gmail inbox  │──▶│ Gmail Trigger │──▶│ Clean email    │──▶│ Classify (AI)│
│ (test only)  │   │ poll 1 min    │   │ Set node       │   │ JSON output  │
└──────────────┘    └───────────────┘    └────────────────┘    └──────┬───────┘
                                                                      │
                                                                      ▼
                                                            ┌──────────────────┐
                                                            │ Parse + validate │
                                                            │ Function node    │
                                                            └────────┬─────────┘
                                                                     │
                                            ┌────────────────────────┴────────────────────────┐
                                            │   Switch: Route by category (+fallback)         │
                                            └──┬──────────┬──────────┬──────────┬─────────────┘
                                              LEAD       SUPPORT     SPAM       OTHER / errors
                                              │            │           │           │
                                              ▼            ▼           ▼           ▼
                                       Idempotency    Idempotency  Idempotency  Idempotency
                                       check          check        check        check
                                              │            │           │           │
                                              ▼            ▼           ▼           ▼
                                       leads tab     support tab  spam tab    other tab
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
                       Draft reply (AI)              Post Slack lead card
                              │                               │
                              ▼
                       Gmail Create Draft
                       (NEVER send)

┌────────────────────────────────────────────────────────────────────────────┐
│ Sub-workflow:  Error Trigger → append errors tab + Slack alert (no retry)  │
└────────────────────────────────────────────────────────────────────────────┘
```

A larger, annotated diagram and per-phase canvas screenshots live in [`docs/screenshots/`](docs/screenshots/).

---

## 3. Repo layout

```
.
├── README.md                       ← you are here
├── docker-compose.yml              ← one-command local n8n
├── .env.example                    ← copy to .env, fill secrets, do NOT commit
├── docs/
│   ├── ARCHITECTURE.md             ← deep dive into node-by-node design
│   ├── SETUP.md                    ← step-by-step from zero → running demo
│   ├── PHASES.md                   ← phase-by-phase build guide (mirrors the PDF)
│   ├── RUNBOOK.md                  ← ops, retries, error handling, scaling
│   ├── EVALUATION_RUBRIC.md        ← how each rubric criterion is satisfied (score forecast ≥ 95/100)
│   ├── SELF_EVALUATION.md          ← traceability for the 23 issues raised by the 2026-05-19 evaluation
│   ├── COST_TRACKING.md            ← real LLM spend log (~ $0.032 / $5 cap)
│   ├── KNOWN_LIMITATIONS.md        ← what we'd fix in v2
│   ├── LOOM.md                     ← Loom script + recording URL
│   ├── prompts/
│   │   ├── classification_prompt.md
│   │   └── reply_draft_prompt.md
│   └── screenshots/                ← ASCII + PNG canvas renderings per phase
├── workflows/
│   ├── v1-phase1.json              ← Trigger only
│   ├── v2-phase2.json              ← + Classification
│   ├── v3-phase3.json              ← + Routing + Sheets CRM
│   ├── v4-phase4.json              ← + Draft + Slack
│   ├── v5-phase5-final.json        ← Hardened, retries, regression
│   └── error-handler.json          ← Error Trigger sub-workflow
├── sheets/
│   ├── CRM_SHEET_TEMPLATE.md       ← exact column layout for each tab
│   ├── leads.csv                   ← header-only seed for the Google Sheet
│   ├── support.csv
│   ├── spam.csv
│   ├── other.csv
│   ├── errors.csv
│   └── test_fixtures.csv           ← regression set with expected categories
├── samples/emails/                 ← 10 raw .eml seed emails covering 4 categories
├── scripts/
│   ├── validate_workflows.py       ← lint + schema-check every workflow JSON
│   ├── check_no_send.py            ← guards: no Gmail "send" operations exist
│   └── seed_inbox.md               ← how to inject the 10 sample emails
└── fixtures/
    └── expected_classifications.json
```

---

## 4. Quick start (TL;DR)

```bash
# 1. Boot n8n locally
cp .env.example .env
# edit .env with your OpenAI key, Google client id/secret, Slack token
docker compose up -d
open http://localhost:5678        # user/pass from .env

# 2. Inside n8n UI:
#    Settings → Credentials → add OpenAI, Gmail OAuth2, Google Sheets OAuth2, Slack
#    File → Import from File → workflows/v5-phase5-final.json
#    File → Import from File → workflows/error-handler.json
#    Open each workflow → bind credentials → Activate

# 3. Validate the repo before any commit
python3 scripts/validate_workflows.py
python3 scripts/check_no_send.py
```

Full instructions in [`docs/SETUP.md`](docs/SETUP.md). Phase-by-phase build narrative in [`docs/PHASES.md`](docs/PHASES.md).

---

## 5. Phase deliverables (each commit = one phase)

| Phase | Deliverable                                                | File                                    | Commit |
|------:|------------------------------------------------------------|-----------------------------------------|--------|
| 0     | Project scaffold, docs skeleton, docker stack              | `README.md`, `docker-compose.yml`, docs | initial |
| 1     | Gmail Trigger + credentials + 10 seed emails               | `workflows/v1-phase1.json`              | atomic |
| 2     | Pre-processing + AI Classifier (strict JSON) + safety net  | `workflows/v2-phase2.json`              | atomic |
| 3     | Switch routing + Google Sheets CRM + idempotency           | `workflows/v3-phase3.json`              | atomic |
| 4     | Draft Reply (AI) + Gmail Create Draft + Slack card         | `workflows/v4-phase4.json`              | atomic |
| 5     | Error Trigger sub-workflow + retries + regression hardening | `workflows/v5-phase5-final.json`, `workflows/error-handler.json` | atomic |

Each acceptance criterion from the exercise PDF is tracked in [`docs/EVALUATION_RUBRIC.md`](docs/EVALUATION_RUBRIC.md).

---

## 6. Hard safety rules (do not violate)

1. **Gmail node MUST be configured as "Create Draft" — never "Send".** A CI guard (`scripts/check_no_send.py`) enforces this on every commit.
2. **No credentials, API keys, or OAuth tokens are committed.** All workflow JSON files use `credentials` references only (id + name placeholders), never raw secrets.
3. **Test inbox only.** Never point the Gmail Trigger at a real `sales@grootan` mailbox.
4. **Idempotent writes.** Re-running the workflow on the same email must not create duplicate rows in any Sheet tab.
5. **Error handler never retriggers the failing workflow.** Surfacing is enough; human decides next step.

---

## 7. Evaluation mapping (target ≥ 90 / 100)

| Criterion (weight)          | Where it lives                                             |
|-----------------------------|------------------------------------------------------------|
| Reliability & Safety (30%)  | Phase 5 retries + error handler + idempotency in Phase 3   |
| Prompt & Classification (25%) | Centralized prompts in `Set` node + few-shot + JSON mode |
| Workflow Architecture (20%) | Sticky-noted sections, descriptive node names, per-phase JSON exports |
| Documentation & Demo (15%)  | This README + `docs/` + Loom script in `docs/SETUP.md` §10 |
| Reusability for Grootan (10%) | Multi-tenant prompt config pattern in `docs/KNOWN_LIMITATIONS.md` §v2 |

See [`docs/EVALUATION_RUBRIC.md`](docs/EVALUATION_RUBRIC.md) for line-by-line traceability.

---

## 8. License & confidentiality

Internal Grootan Technologies training material. Not for external distribution without sign-off from Santhosh.
