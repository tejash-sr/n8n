# Setup Guide — Lead Inbox Triage Bot (local n8n)

This guide takes you from a clean laptop to a running, demonstrable workflow. Estimated time: **45–60 minutes** end-to-end (most of which is Google OAuth consent screens).

---

## 0. Prerequisites

| Tool                | Version              | Why                                  |
|---------------------|----------------------|--------------------------------------|
| Docker Desktop      | ≥ 24.x               | Runs n8n + persistent volume         |
| Docker Compose      | ≥ v2 (bundled)       | Orchestration                        |
| Python              | ≥ 3.10               | Validation scripts in `scripts/`     |
| A Gmail account     | Test account only    | Source of incoming leads             |
| Google Cloud project | Free tier OK        | Hosts the OAuth client               |
| OpenAI **or** Anthropic key | Pay-as-you-go | LLM provider                        |
| Slack workspace     | Free tier OK         | Notifications (optional but graded)  |

> ⚠️ **Do not use a personal Gmail you actually live in.** Create a throwaway Google account for training — Section 4 will explicitly read and modify Gmail data.

---

## 1. Clone & boot n8n

```bash
git clone https://github.com/tejash-sr/n8n.git lead-inbox-triage-bot
cd lead-inbox-triage-bot

cp .env.example .env
# Generate a stable encryption key. Store the same key forever for this instance.
echo "N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)" >> .env

docker compose up -d
docker compose logs -f n8n        # wait until you see "Editor is now accessible on …"
```

Open <http://localhost:5678>, log in with `N8N_BASIC_AUTH_USER / N8N_BASIC_AUTH_PASSWORD`, and finish n8n's own first-run setup wizard (owner email + password). This owner account is separate from the basic-auth gate.

---

## 2. Create Google OAuth client (Gmail + Sheets)

1. Go to <https://console.cloud.google.com/> → create or select a project.
2. **APIs & Services → Library** → enable:
   - Gmail API
   - Google Sheets API
   - Google Drive API (Sheets needs it for file metadata)
3. **OAuth consent screen** → External → fill app name `Grootan Lead Triage Training`, add yourself as a test user. Add scopes:
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/spreadsheets`
   - `https://www.googleapis.com/auth/drive.file`
4. **Credentials → Create credentials → OAuth client ID** → **Web application**.
   - Authorized redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`
   - Save the Client ID and Client Secret into your `.env`.

In n8n UI → **Credentials → New**:

- **Gmail OAuth2** — paste the client id/secret, click *Connect my account*, accept on Google.
- **Google Sheets OAuth2** — same client id/secret, separate credential entry, also connect.

> The `redirect URI` MUST match exactly, including the trailing `/callback`.

---

## 3. Create the LLM credential

Inside n8n → **Credentials → New → OpenAI**:

- API key: from your `.env` (`OPENAI_API_KEY`).
- Save as **OpenAI - Triage Training**.

If you prefer Anthropic, create an **Anthropic** credential instead and swap the AI node `model` field — both providers are wired the same way (system message + JSON mode prompt).

---

## 4. Create Slack credential (optional, recommended)

1. <https://api.slack.com/apps> → Create new app → From scratch → name `Lead Triage Notifier`.
2. **OAuth & Permissions** → add Bot Token Scopes:
   - `chat:write`
   - `chat:write.public` (to post to channels the bot isn't invited to)
3. Install the app to your workspace, copy the `xoxb-…` bot token.
4. In Slack: create channels `#training-leads` and `#training-leads-errors`, invite the bot.
5. In n8n: **Credentials → New → Slack** → paste the bot token. Save as **Slack - Triage Bot**.

---

## 5. Create the CRM Google Sheet

1. Create a new Google Sheet named `Lead Triage CRM - Training - <your initials>`.
2. Create 6 tabs with the exact column headers in [`../sheets/CRM_SHEET_TEMPLATE.md`](../sheets/CRM_SHEET_TEMPLATE.md):
   - `test_fixtures`, `leads`, `support`, `spam`, `other`, `errors`
3. Copy the spreadsheet ID from the URL (`https://docs.google.com/spreadsheets/d/<THIS-IS-THE-ID>/edit`) into `.env` as `CRM_SPREADSHEET_ID`. Restart n8n: `docker compose restart n8n`.
4. Quick way to populate headers: paste the contents of each CSV from `../sheets/*.csv` into row 1 of the matching tab.

---

## 6. Import the workflows

Inside n8n:

1. **Workflows → Import from File** → `workflows/v5-phase5-final.json`.
2. Open the workflow → for each Gmail / Sheets / OpenAI / Slack node, click into it and bind the credential you created in Sections 2–4. (n8n cannot guess credential IDs across instances.)
3. In the **Set node "Config"** (top of canvas), set:
   - `spreadsheetId` → your `CRM_SPREADSHEET_ID`
   - `slackChannel` → `#training-leads`
4. Click **Save**, then **Activate**.

Repeat for `workflows/error-handler.json`. Open the main workflow → **Settings → Error workflow** → select `LeadInboxTriageBot_ErrorHandler`.

> If you want to retrace the build, you can also import `v1-phase1.json`, `v2-phase2.json`, etc. one at a time and see the workflow grow. Only the final two need to be active.

---

## 7. Seed the test inbox

You have 10 sample emails in [`../samples/emails/`](../samples/emails/) — one per file, plain `.eml`. Two reliable ways to inject them:

**Option A — Forward by hand (5 mins, no setup):** open each `.eml` in your local mail client and forward to the test inbox. Slowest but most realistic.

**Option B — Gmail "Insert" via Python script:** see [`../scripts/seed_inbox.md`](../scripts/seed_inbox.md). Uses the same OAuth client credentials as n8n.

After seeding, watch **n8n → Executions** — within ~60 seconds, ten executions should appear, one per email.

---

## 8. Validate the repo before any commit

```bash
python3 scripts/validate_workflows.py    # JSON schema + node naming + sticky notes
python3 scripts/check_no_send.py         # hard guard: no Gmail "send" operations
```

Both must exit with `0`. If `check_no_send.py` ever fails, **do not push** — fix the workflow first.

---

## 9. Run the regression test set

1. Open the `test_fixtures` tab of the CRM sheet. Each row has `expected_category`.
2. With the workflow Active, re-trigger the 10 seed emails (re-forward, or use Gmail's "Mark as unread" + remove our `triaged` label).
3. After all 10 executions finish, the workflow will have logged actual rows to `leads/support/spam/other` tabs.
4. Open the `test_fixtures` tab and verify `actual_category` was populated via the cross-tab formula provided in `CRM_SHEET_TEMPLATE.md`. The `match` column auto-calculates `TRUE/FALSE`.
5. The cell **`G1`** holds `Accuracy: =COUNTIF(D:D,TRUE)&"/"&COUNTA(B:B)-1`. Target: **≥ 8/10**.

---

## 10. Loom demo script (≤ 3 mins)

> Recommended structure — script is also in [`PHASES.md`](PHASES.md) §Phase 5.

1. **(0:00–0:20)** Show the n8n canvas, point out colored sticky-note sections.
2. **(0:20–0:50)** Send a fresh `LEAD`-style email to the inbox from another account.
3. **(0:50–1:30)** Switch to n8n → Executions → open the new run → walk through Clean → Classify → Switch (LEAD path) → Sheets → Draft + Slack.
4. **(1:30–2:00)** Show the new row in `leads` tab, the draft in Gmail, the card in `#training-leads`.
5. **(2:00–2:30)** Revoke the OpenAI key in n8n credentials → re-send → show the error handler firing → new row in `errors` tab and alert in `#training-leads-errors`.
6. **(2:30–3:00)** Show accuracy in `test_fixtures` (`G1`) and total LLM spend in the OpenAI dashboard. Cut.

---

## 11. Troubleshooting

| Symptom                                          | Likely cause                                          | Fix                                                              |
|--------------------------------------------------|-------------------------------------------------------|------------------------------------------------------------------|
| Gmail trigger never fires                        | OAuth scope missing `gmail.modify`                    | Re-create credential with full scope, re-consent                  |
| `redirect_uri_mismatch` on Google consent        | Mismatched callback in Cloud Console                  | Use exactly `http://localhost:5678/rest/oauth2-credential/callback` |
| `INVALID_ARGUMENT: Unable to parse range`        | Sheet tab name typo                                   | Tab names must be exactly `leads`, `support`, `spam`, `other`, `errors`, `test_fixtures` |
| AI returns prose instead of JSON                 | Wrong model or `response_format` missing              | Use `gpt-4o-mini` (or `claude-3-5-sonnet`) with `json_object` mode |
| Workflow keeps re-processing same email          | "Mark as read" not set on Gmail Trigger               | Open trigger → Additional Fields → `Mark as read = true`         |
| Slack `not_in_channel`                           | Bot never invited                                     | `/invite @Lead Triage Notifier` in both channels                  |
| Duplicates in `leads` tab on re-run              | Idempotency lookup using wrong column                 | Make sure the lookup Sheets node reads column `A` (`message_id`) |

Anything else → escalate to Santhosh per the PDF's 2-hour rule.
