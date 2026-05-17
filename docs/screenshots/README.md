# Canvas Screenshots

After importing and activating the workflow in your local n8n, capture the canvas at the end of each phase and save it here with the names below. Each screenshot should have the sticky-note section labels clearly visible.

| File                            | Captured at end of | What to show                                                                 |
|--------------------------------|--------------------|------------------------------------------------------------------------------|
| `01-trigger.png`               | Phase 1            | Sticky-noted skeleton + Gmail Trigger + Config Set node                       |
| `02-classify.png`              | Phase 2            | + Clean email + Prompts + Classify + Parse classification                     |
| `03-route-and-log.png`         | Phase 3            | + Switch + 4 idempotency checks + 4 Sheets append nodes                       |
| `04-draft-and-slack.png`       | Phase 4            | + Draft reply + Create Gmail draft + Post lead card (Slack)                   |
| `05-error-handler.png`         | Phase 5            | The second workflow: Error Trigger → Build error row → Sheets + Slack         |
| `06-full-canvas.png`           | Phase 5            | Full main workflow with every sticky-note section labelled                    |
| `07-loom-thumbnail.png`        | Phase 5            | Optional — thumbnail for the Loom (used in the README badge)                  |

## How to annotate

We use **draw.io** ([https://app.diagrams.net](https://app.diagrams.net)) for arrows and callouts. Workflow:

1. Take a clean PNG from n8n: zoom out so the whole graph fits, then *Settings → Download → PNG*.
2. Open in draw.io as a background image, add red callout arrows for the section names and any noteworthy connections.
3. Export as PNG, save here.

Each PNG should be ≤ 2 MB. If you exceed that, downscale to 1600 px wide.

## Why we don't commit raw PNGs from the runtime

Screenshots taken from a live n8n instance can show credential IDs in the URL bar. Crop those out, or use draw.io's redaction tools, before committing.
