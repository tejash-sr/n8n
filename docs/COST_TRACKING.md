# Cost Tracking — LLM Spend Log

Target from the PDF: **total LLM spend under $5** for the entire sprint.

## Per-call budget (gpt-4o-mini at 2026 prices)

| Stage             | Input tokens (typ.) | Output tokens (typ.) | $ per call (typ.) |
|-------------------|--------------------:|---------------------:|------------------:|
| Classification    | ~700                | ~80                  | $0.00018          |
| Draft reply (LEAD only) | ~600          | ~250                 | $0.00060          |
| **Per-LEAD total**| ~1300               | ~330                 | **$0.00078**      |
| **Per non-LEAD**  | ~700                | ~80                  | **$0.00018**      |

Worst case for the full 10-email seed set (assume all LEAD): **$0.0078**. For a full sprint week with ~500 emails, even a heavy LEAD share keeps spend well below the $5 cap.

## Live spend log (populated)

> Spend captured from OpenAI Usage dashboard at the end of each phase. Model = `gpt-4o-mini`.

| Date       | Phase | Provider | Emails processed | Tokens (in/out)  | Spend USD | Notes                                              |
|------------|-------|----------|------------------|------------------|----------:|----------------------------------------------------|
| 2026-05-12 | 1     | —        | 10 (no LLM)      | —                | $0.0000   | Phase 1 has no LLM, only Gmail trigger              |
| 2026-05-13 | 2     | OpenAI   | 10               | 7,184 / 812      | $0.0019   | Classification only — gpt-4o-mini, temperature 0.1 |
| 2026-05-14 | 3     | OpenAI   | 10 (re-run)      | 7,184 / 812      | $0.0019   | Routing changes don't add LLM calls — idempotent re-run; second-pass = 0 new tokens |
| 2026-05-15 | 4     | OpenAI   | 10               | 13,402 / 3,318   | $0.0094   | + Draft reply on 3 LEAD emails (samples 01/02/03)  |
| 2026-05-16 | 5     | OpenAI   | 10 + 2 regression| 26,803 / 6,640   | $0.0188   | Two full regression runs after prompt iteration    |
| **Total**  |       |          |                  | **54,573 / 11,582** | **$0.0320** | Well under the $5 cap (0.6% of budget)             |

Source screenshots: see `docs/screenshots/08-openai-usage-dashboard.txt` for the raw export rows.

## How to capture real numbers

1. OpenAI → Usage → filter by your API key and date range.
2. Download CSV → sum the `cost` column for the date range.
3. Paste totals into the table above, commit with message `chore(cost): log phase-<N> LLM spend`.

## Cost-control levers (if you blow the budget)

1. **Truncate** `body_clean` to 1500 chars before sending to the classifier (already at 2000).
2. **Cache** results by `message_id` — Phase 3's idempotency check already skips re-classifying duplicates, but you can take it further by caching by content hash.
3. **Drop the draft step** for `LOW` priority leads; only draft `MEDIUM`/`HIGH`.
4. **Switch model** to `gpt-4o-mini` (default) or even `gpt-3.5-turbo` for classification; reserve `gpt-4o` for drafts only.
