# Runbook — Lead Inbox Triage Bot

For on-call use. Keep this short and actionable; deep design lives in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Where things live

| Component        | Location                                                           |
|------------------|---------------------------------------------------------------------|
| Workflow JSONs   | `workflows/v*.json` and `workflows/error-handler.json`             |
| Prompts          | `Set node "Prompts"` inside the main workflow                       |
| CRM Sheet        | Google Drive of the training Google account (`CRM_SPREADSHEET_ID`) |
| Slack channels   | `#training-leads`, `#training-leads-errors`                         |
| Executions log   | n8n UI → Executions                                                 |
| Error log        | `errors` tab of the CRM Sheet + `#training-leads-errors`            |

---

## 2. Standard checks (every morning, 30 seconds)

1. Open n8n → both workflows show **Active**.
2. Executions list — last 24h shows green checkmarks. Any red? → §3.
3. `errors` tab — empty since yesterday? If not → §4.
4. CRM `leads` tab — eyeball latest rows for sane categories, confidence > 0.5.
5. Gmail Drafts — count matches `leads` tab row delta for the same period.

---

## 3. An execution failed — what now?

1. Click the red execution in n8n → identify the failing node.
2. Look up the failing node in the table below:

| Failing node              | Most likely cause                                       | First-aid fix                                            |
|---------------------------|---------------------------------------------------------|----------------------------------------------------------|
| `Watch inbox`             | OAuth token revoked                                     | Re-authorise Gmail credential                            |
| `Classify email`          | OpenAI 429 / 500                                        | Wait 5 min; the node already retries 3× — check quota    |
| `Parse classification`    | AI returned non-JSON not handled by regex               | Inspect input, broaden regex in safety net               |
| `Already logged?`         | Sheets `Range not found`                                | Verify tab name (case-sensitive)                         |
| `Append leads`            | Sheets quota (300/min)                                  | Throttle: drop trigger interval; this is rare in training|
| `Create Gmail draft`      | Bad `threadId`                                          | Fall back: leave threadId empty → standalone draft       |
| `Post lead card`          | `not_in_channel`                                        | `/invite @bot` in Slack channel                          |
| **Anything** in error handler | Failed to write to `errors` tab                     | Manual log + escalate to Santhosh                        |

3. After fixing, re-run by clicking *Retry from failed node* in the execution view.

---

## 4. The `errors` tab has new rows

Each row tells you exactly where the main workflow blew up:

| Column          | Meaning                                                                 |
|-----------------|--------------------------------------------------------------------------|
| `message_id`    | Original Gmail message id (may be blank if failure was pre-trigger)     |
| `received_at`   | When the email landed                                                    |
| `error_stage`   | The node name that threw (`Classify email`, `Append leads`, …)           |
| `error_message` | First 500 chars of the n8n error                                         |
| `raw_payload`   | First 1000 chars of the failed-item JSON, for forensics                  |
| `created_at`    | When the error was logged                                                |

After triage, set `status = HANDLED` (manually) so daily checks stay short.

---

## 5. Common false positives & quick prompt fixes

| Symptom                                                  | Tweak                                                                 |
|----------------------------------------------------------|-----------------------------------------------------------------------|
| Marketing newsletters classified as `LEAD`               | Add "newsletter, list-unsubscribe header, 'view in browser' = SPAM" to system prompt few-shot |
| Customer bug reports classified as `OTHER`               | Add a SUPPORT few-shot example with a stack-trace excerpt              |
| Internal forwarded calendar invites classified as `LEAD` | Add OTHER few-shot example with "Invitation: <title>" pattern         |
| Confidence stuck at `0.99` on every email                | Lower `temperature` to `0.0` (deterministic), or instruct the model: "Use 0.6 if any uncertainty about category" |

After any prompt edit, **re-run the 10-email regression** and update accuracy in the `test_fixtures` tab. Commit message:

```
chore(prompt): tighten SPAM rules to catch list-unsubscribe newsletters
```

---

## 6. Cost & quota guardrails

- **OpenAI.** Set a hard monthly budget cap in OpenAI billing (recommended: `$10` while training, `$50` for any prod pilot). The bot's per-email cost is ≤ $0.004 (LEAD) / ≤ $0.0005 (non-LEAD).
- **Sheets.** Default per-user quota is 60 read/write requests per minute. The workflow uses 2 Sheets calls per email (lookup + append) — comfortably under quota at one email per ~6 seconds.
- **Gmail.** Polling every minute = 1440 reads/day, well under Gmail API quotas.
- **Slack.** 1 message per LEAD; no concern.

---

## 7. Rotation & rollback

| Task                       | How                                                                                          |
|----------------------------|----------------------------------------------------------------------------------------------|
| Rotate OpenAI key          | Generate new key → update n8n credential → archive old in 1Password → no workflow change     |
| Rotate Google OAuth secret | Generate new client secret in Cloud Console → update both Gmail & Sheets creds → re-consent  |
| Rollback to previous JSON  | Re-import the older `workflows/v*.json` → activate → deactivate the current one              |
| Pause the bot              | Toggle main workflow to **Inactive**. Error handler stays active so you still get alerts     |
| Wipe & restart locally     | `docker compose down -v && docker compose up -d` (re-import credentials & workflows)         |

---

## 8. Known operational caveats (read before deploying)

### 8.1 Gmail Trigger has no in-node retry
n8n's Gmail Trigger is a polling node — when its OAuth call fails it surfaces the error immediately and **does not** participate in the `retryOnFail / maxTries / waitBetweenTries` lattice that we configure on every downstream Gmail/Sheets/OpenAI/Slack node. The trigger relies on its own internal polling cycle to recover: if the first poll after a transient outage succeeds, the next poll a minute later will catch the email anyway. **Implication:** if Gmail OAuth is revoked or rate-limited, the workflow effectively pauses until the credential is re-authorised. The Error Trigger sub-workflow will catch executions that fail *downstream* of a successful trigger, but not the trigger itself. On-call response: monitor the n8n executions list for a > 5 min gap with no executions and re-authorise the credential.

### 8.2 PII handling — what leaves your n8n
The classifier sends `subject_clean` + the first 2,000 chars of `body_clean` to OpenAI. The reply-draft step sends sender name + domain + the same body slice. By contractual default OpenAI does **not** train on API-tier traffic, but the request still leaves your network. **Implication:** if you point this bot at a real customer inbox you must (a) confirm your OpenAI org has zero-retention enabled or sign a DPA, (b) decide whether to add a pre-classification PII scrubber (we recommend `presidio` or a regex pass for credit-card / SSN / phone patterns), and (c) document this in your data map. For training-only fixtures this is acceptable; for production data review with Santhosh + Legal first.

### 8.3 Sheets-only CRM is single-writer
Google Sheets is not a real database. The Lookup → IF → Append idempotency pattern is *cooperative* — if two workflow executions race on the same `message_id`, both may pass the Lookup and produce a duplicate row. In practice the 1-min polling cadence and per-message Gmail dedup make this nearly impossible, but for any prod port you should swap the CRM tab for an upsert-capable store (HubSpot, Salesforce, Postgres with a unique index on `message_id`).

## 9. Scaling beyond training (informational)

The training stack is intentionally SQLite. For a real prod deployment:

- Swap to Postgres (`DB_TYPE=postgresdb` + Postgres service in compose).
- Run n8n in `queue` execution mode with a Redis broker; horizontally scale workers.
- Replace Gmail polling with Gmail Push (`watch` API + Pub/Sub) for ~real-time delivery.
- Move secrets to AWS/GCP Secret Manager; mount via env at boot.
- Replace the CRM Sheet with a real CRM (HubSpot, Salesforce). The Switch node + per-category nodes already model what the upsert payload would look like.

These are noted in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) §"v2 ideas".
