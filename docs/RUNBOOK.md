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
| `error_message` | First 280 chars of the n8n error                                         |
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

## 8. Scaling beyond training (informational)

The training stack is intentionally SQLite. For a real prod deployment:

- Swap to Postgres (`DB_TYPE=postgresdb` + Postgres service in compose).
- Run n8n in `queue` execution mode with a Redis broker; horizontally scale workers.
- Replace Gmail polling with Gmail Push (`watch` API + Pub/Sub) for ~real-time delivery.
- Move secrets to AWS/GCP Secret Manager; mount via env at boot.
- Replace the CRM Sheet with a real CRM (HubSpot, Salesforce). The Switch node + per-category nodes already model what the upsert payload would look like.

These are noted in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) §"v2 ideas".
