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

## Live spend log (fill in per phase)

| Date       | Phase | Provider | Emails processed | Tokens (in/out) | Spend USD | Notes                                |
|------------|-------|----------|------------------|-----------------|----------:|---------------------------------------|
| YYYY-MM-DD | 1     | —        | 10 (no LLM calls yet) | —          | $0.00     | Phase 1 has no LLM, only trigger      |
| YYYY-MM-DD | 2     | OpenAI   | 10               | ~7,000 / ~800   | $0.0018   | Classification only                   |
| YYYY-MM-DD | 3     | OpenAI   | 10 (re-run)      | ~7,000 / ~800   | $0.0018   | Routing changes don't add LLM calls   |
| YYYY-MM-DD | 4     | OpenAI   | 10               | ~13,000 / ~3,300| $0.0090   | + Draft reply on 4 LEADs              |
| YYYY-MM-DD | 5     | OpenAI   | 10 + regression  | ~26,000 / ~6,600| $0.0180   | Two full regression runs              |
| **Total**  |       |          |                  |                 | **≈ $0.03** | Well under $5 cap                     |

> The numbers above are calibrated estimates. Replace them with the real values from your OpenAI Usage dashboard (https://platform.openai.com/usage) when you finish each phase.

## How to capture real numbers

1. OpenAI → Usage → filter by your API key and date range.
2. Download CSV → sum the `cost` column for the date range.
3. Paste totals into the table above, commit with message `chore(cost): log phase-<N> LLM spend`.

## Cost-control levers (if you blow the budget)

1. **Truncate** `body_clean` to 1500 chars before sending to the classifier (already at 2000).
2. **Cache** results by `message_id` — Phase 3's idempotency check already skips re-classifying duplicates, but you can take it further by caching by content hash.
3. **Drop the draft step** for `LOW` priority leads; only draft `MEDIUM`/`HIGH`.
4. **Switch model** to `gpt-4o-mini` (default) or even `gpt-3.5-turbo` for classification; reserve `gpt-4o` for drafts only.
