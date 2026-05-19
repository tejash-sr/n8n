# Canvas Screenshots

This folder holds the visual evidence for each phase. Two kinds of files live here:

1. **`*.txt` ASCII renderings (committed today).** Generated directly from the workflow JSONs by `scripts/build_workflows.py` — they describe every node, every connection, every sticky note, and every important parameter. Reviewers who don't want to spin up a local n8n can read these and verify the canvas matches the spec.
2. **`*.png` live screenshots (to be captured during the demo).** Captured from a running n8n instance after import. These should match the ASCII renderings 1:1; the ASCII version is the authoritative source if there is ever a divergence.

## File inventory

| File                                  | Captured at end of | What to show                                                                 |
|---------------------------------------|--------------------|------------------------------------------------------------------------------|
| `01-trigger.txt` / `.png`             | Phase 1            | Sticky-noted skeleton + Gmail Trigger + Config Set node                       |
| `02-classify.txt` / `.png`            | Phase 2            | + Prompts (separate Set) + Clean email + Compute preview + Classify + Parse  |
| `03-route-and-log.txt` / `.png`       | Phase 3            | + Switch + 4 idempotency chains + 4 Sheets appends + fallback-to-errors      |
| `04-draft-and-slack.txt` / `.png`     | Phase 4            | + Draft reply + Create Gmail draft + Wait + colour-coded Slack card           |
| `05-error-handler.txt` / `.png`       | Phase 5            | Error handler sub-workflow w/ retry on Sheets + Slack                         |
| `06-full-canvas.txt` / `.png`         | Phase 5            | Full main workflow with all eight sticky-note sections                        |
| `07-loom-thumbnail.txt` / `.png`      | Phase 5            | Loom thumbnail composition guide                                              |
| `08-openai-usage-dashboard.txt` / `.png` | Phase 5         | Raw OpenAI Usage rows backing `docs/COST_TRACKING.md`                         |

## Why ASCII at all?

The CI builder runs in a headless sandbox without n8n's Electron frontend — there's no way to script a real PNG capture from here. Committing ASCII renderings instead of `[image to be added]` placeholders means:

- A reviewer reading the repo on GitHub sees the canvas immediately.
- The text is grep-able — `grep -R "Wait for draft id" docs/screenshots/` works.
- When you later capture a real PNG and commit it next to the `.txt`, the two stay in sync — if they ever drift the `.txt` rebuilds from JSON and flags the diff.

## How to capture the live PNGs

1. Spin up n8n locally per `docs/SETUP.md`.
2. Import the workflow JSON for the phase you're documenting.
3. Zoom out (Ctrl/Cmd + `−`) until the full graph fits the viewport.
4. Use the browser's screenshot tool *or* n8n's *⋯ → Download → PNG* if available.
5. Open the PNG in [draw.io](https://app.diagrams.net), add red callout arrows for section labels and the four "key actions" (Classify · Append · Create Draft · Post Slack).
6. Export as PNG, save here with the matching filename.
7. Keep each PNG ≤ 2 MB — downscale to 1600 px wide if you exceed.

## Redaction checklist before committing a PNG

Screenshots from a live n8n instance can leak:

- Credential IDs in the URL bar  → crop the browser chrome.
- Real `spreadsheetId` values    → blur/box-out the Config node fields.
- Active Slack channel IDs       → blur the Slack node `channel` field.

For the training repo none of these are sensitive, but the habit matters for the prod port.
