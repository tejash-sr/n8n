# Architecture — Lead Inbox Triage Bot

## 1. Design goals (in priority order)

1. **Safety first.** No outbound email is ever sent automatically. Gmail nodes are restricted to `Create Draft`.
2. **Reliability.** Every external call (LLM, Gmail, Sheets, Slack) has retries on transient failures. A separate Error Trigger workflow captures anything that still falls through.
3. **Determinism.** The classifier returns strict JSON with `temperature = 0.1` and a parse-safety net so downstream routing never crashes on a malformed model response.
4. **Idempotency.** Re-running the workflow on the same `message_id` is a no-op — no duplicate rows, no duplicate drafts, no double Slack pings.
5. **Maintainability.** Prompts live in a single `Set node "Prompts"` so non-engineers can iterate without opening the AI node. Every node has a human-readable name. The canvas is grouped into colored sticky-note sections that mirror the 5 phases.

## 2. Logical sections (mirrored on the canvas with sticky notes)

| Color  | Section            | Nodes (typical)                                                                                                  |
|--------|--------------------|------------------------------------------------------------------------------------------------------------------|
| Grey   | **Phase banner**   | Big top-of-canvas sticky note titled `Phase <N> · <name>` (1180×160 px) — first thing a reviewer sees on import. |
| Yellow | **Trigger**        | `Watch inbox` (Gmail Trigger, `markAsRead = true`)                                                               |
| Orange | **Pre-process**    | `Config` (Set), `Prompts` (separate Set — system prompts + model + temperature), `Clean email` (Set), `Compute preview` (Set — second pass) |
| Blue   | **Classify**       | `Classify email` (OpenAI, `response_format = json_object`), `Parse classification` (Function safety net)         |
| Purple | **Route**          | `Route by category` (Switch with `LEAD/SUPPORT/SPAM/OTHER` outputs + fallback → `Log unknown category to errors`) |
| Green  | **Log to CRM**     | `Lookup <category>` (Sheets read) → `Already in <category>?` (IF) → either `Duplicate skipped (<category>)` (Set) or `Append <category>` (Sheets append) — × 4 categories |
| Pink   | **Draft & Notify** | `Draft reply` (AI) → `Create Gmail draft` → `Wait for draft id` (Wait) → `Post lead card` (Slack, attachment-style block kit with color-coded priority) |
| Red    | **Error handling** | `Error Trigger` (in sub-workflow) → `Build error row` (Set) → `Append errors row` (Sheets, with retries) + `Post error alert` (Slack, with retries) |

## 3. Data shape (end-to-end)

After the Gmail Trigger:
```json
{
  "id": "MESSAGE-ID@mail.gmail.com",
  "threadId": "1810…",
  "from": "Alice Chen <alice@acme.com>",
  "to":   "triage-test@example.com",
  "subject": "Fwd: Re: Quote for Q3 rollout",
  "body":   "<plain-text body, may contain quoted reply chain>",
  "receivedAt": "2026-05-11T14:30:00Z"
}
```

After the `Clean email` Set node:
```json
{
  "message_id":     "MESSAGE-ID@mail.gmail.com",
  "thread_id":      "1810…",
  "sender_email":   "alice@acme.com",
  "sender_name":    "Alice Chen",
  "sender_domain":  "acme.com",
  "subject_clean":  "Quote for Q3 rollout",
  "body_clean":     "Hi, we're a 200-person ops team … <quoted block stripped> … signature stripped",
  "body_preview":   "Hi, we're a 200-person ops team …",
  "received_at":    "2026-05-11T14:30:00Z"
}
```

After the `Parse classification` Function node:
```json
{
  "category":           "LEAD",
  "confidence":         0.92,
  "reasoning":          "Sender from acme.com is asking for a quote with team size and timeline.",
  "suggested_priority": "HIGH",
  "parse_failed":       false
}
```

After the `Append leads` Sheets node, a new row appears with the columns documented in [`../sheets/CRM_SHEET_TEMPLATE.md`](../sheets/CRM_SHEET_TEMPLATE.md). After the `Create Gmail draft` node, `data.id` is the draft id used to build the deep-link URL for Slack.

## 4. Cleaning logic (the `Clean email` Set node)

We use **n8n expressions** (JS-style) rather than a Function node because they are clearer in code review and don't require a runtime context. The signature-stripping regex is intentionally conservative — better to leave a one-line signature than to truncate a real paragraph.

```js
// subject_clean
{{$json.subject.replace(/^\s*(Re:|RE:|Fwd:|FW:|Fw:)\s*/gi, '').trim()}}

// sender_domain
{{($json.from.match(/<([^>]+)>/)?.[1] ?? $json.from)
   .split('@')[1].toLowerCase().trim()}}

// sender_name  (best-effort: "First Last <email>" → "First Last", else email local-part)
{{ (() => {
     const m = $json.from.match(/^(.*?)\s*<[^>]+>$/);
     if (m && m[1]) return m[1].replace(/['"]/g, '').trim();
     return $json.from.split('@')[0];
   })() }}

// body_clean  (strip quoted reply chains + signature blocks)
{{ $json.body
     .replace(/On\s+.+?wrote:[\s\S]*$/m, '')             // Gmail-style quote header
     .replace(/-----Original Message-----[\s\S]*$/m, '') // Outlook-style quote header
     .replace(/\n--\s*\n[\s\S]*$/m, '')                  // RFC 3676 signature delimiter
     .replace(/\n_{2,}\n[\s\S]*$/m, '')                  // long underscore separators
     .trim() }}

// body_preview  — computed in the SECOND Set node "Compute preview"
//                 rather than alongside body_clean. The reason: inside a
//                 single Set node, expression evaluation order is not
//                 guaranteed by n8n, so referencing a sibling field that
//                 was just computed can resolve to undefined. Splitting
//                 into a two-pass design (Clean email → Compute preview)
//                 makes the dataflow unambiguous and bug-free.
{{ $json.body_clean.slice(0, 500) }}
```

### 4.1 Why the Prompts node is separate from Config

`Config` holds **deployment-time** values (spreadsheet id, Slack channel, signature, model name, temperature). `Prompts` holds **prompt-engineering-time** values (the two system prompts). They are split because:

1. Prompt-engineering iterations should produce a one-field diff on the `Prompts` node only — easy to review.
2. The Set node UI hits a usability cliff above ~8 fields; splitting keeps both nodes scannable.
3. Different humans own the two: ops owns Config, the AI Engineer owns Prompts. The split makes ownership visible on the canvas.

## 5. Classification node configuration

- **Model:** `gpt-4o-mini` (or `claude-3-5-haiku-20241022`) — cheap, fast, deterministic enough for 4-way classification.
- **Temperature:** `0.1`
- **Top-p:** `1`
- **Response format:** `json_object` (OpenAI) / structured XML tag wrapper (Anthropic).
- **System message:** see [`prompts/classification_prompt.md`](prompts/classification_prompt.md).
- **User message:** templated with `subject_clean` + `body_clean` (truncated to ~2k chars to control cost).

## 6. Safety net (`Parse classification` Function node)

Pseudo-code lives in the node body. Key invariants:

```js
// Returns a normalized object, NEVER throws.
let raw = $json.message?.content ?? $json.text ?? $json.output ?? '';
let parsed = {};
try { parsed = JSON.parse(raw); } catch { parsed = {}; }

const ALLOWED = ['LEAD','SUPPORT','SPAM','OTHER'];
const PRIOS   = ['LOW','MEDIUM','HIGH'];

const category   = ALLOWED.includes(parsed.category) ? parsed.category : 'OTHER';
const confidence = Math.max(0, Math.min(1, Number(parsed.confidence) || 0));
const reasoning  = String(parsed.reasoning || '').slice(0, 280);
const suggested_priority =
  category === 'LEAD' && PRIOS.includes(parsed.suggested_priority)
    ? parsed.suggested_priority
    : (category === 'LEAD' ? 'MEDIUM' : null);
const parse_failed = !raw || !parsed.category;

return [{ json: { ...$json, category, confidence, reasoning, suggested_priority, parse_failed } }];
```

This guarantees downstream nodes see one of the four valid categories on **100%** of inputs — even when the AI hallucinates prose.

## 7. Idempotency strategy

Two acceptable patterns; we implement **Pattern A** because it requires no schema change:

**Pattern A — per-tab lookup (chosen).** Before each `Append <category>` Sheets node, a Sheets `Lookup` node reads column `message_id` for the matching tab and an IF node short-circuits the workflow with `duplicate_skipped=true` if a match is found.

**Pattern B — master `all_messages` tab.** Single tab keyed on `message_id`. Cleaner for analytics; requires an extra append on every run. Implementation note left in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).

## 8. Draft reply generation (LEAD branch only)

- Model: `gpt-4o-mini`, `temperature = 0.4` (slight creativity for natural prose).
- Hard constraints in the system prompt: 80–150 words, no pricing, no fabrication, propose a 20-minute call, fixed signature block (read from the `Config` Set node so it is editable in one place).
- Output is fed directly into `Gmail Create Draft` with:
  - `operation = createDraft`
  - `threadId = {{$json.thread_id}}` (so the draft attaches to the original Gmail conversation)
  - `subject  = Re: <subject_clean>` (only prepended if not already starting with `Re:`)
  - `recipients = $json.sender_email`
- The node's `Send` operation is **explicitly absent** from the workflow JSON. `scripts/check_no_send.py` parses every workflow and fails CI if any node has `operation == "send"`.

## 9. Slack notification (parallel branch + color-coded attachment)

After `Append leads` succeeds, the LEAD branch fans out into a **parallel pair**:

```
Append leads ──▶ Draft reply ──▶ Create Gmail draft ──▶ Wait for draft id ──▶ Post lead card
```

`Wait for draft id` is a tiny `Wait` node (250 ms) inserted **only** to give `Create Gmail draft`'s response a chance to materialise in `$json.id` before the Slack node templates it into the *Open draft in Gmail* button. Without it, parallel execution can race and produce a Slack card with an empty draft link.

The Slack payload uses the **attachment-style Block Kit** (not just `blocks`) because the attachment wrapper is the only documented place Slack honours a `color` field, which we use to colour-code priority:

| Priority | Color hex   | Reason                       |
|----------|-------------|------------------------------|
| HIGH     | `#E01E5A`   | Slack-brand red — eye-catch  |
| MEDIUM   | `#ECB22E`   | Slack-brand amber            |
| LOW      | `#2EB67D`   | Slack-brand green            |

Card contents:

| Block            | Content                                                                                                       |
|------------------|----------------------------------------------------------------------------------------------------------------|
| `header`         | `🚨 / ⚠️ / 🟢 New lead — {priority}` with priority emoji + brand color via the enclosing attachment             |
| `section.fields` | `From`, `Subject`, `Confidence` (`{{$json.confidence * 100}}%`), `Domain`                                      |
| `section.text`   | `body_preview` (already capped at 500 chars by the `Compute preview` Set node)                                 |
| `actions`        | Button **Open draft in Gmail** → `https://mail.google.com/mail/u/0/#drafts/{{$node["Create Gmail draft"].json.id}}` |

## 10. Error handler sub-workflow

Trigger: `Error Trigger` node. Receives the full failed-execution payload.

```text
Error Trigger
   │
   ▼
[Set: Build error row]   ← message_id (best-effort), error_stage = $json.execution.lastNodeExecuted,
                            error_message = $json.execution.error.message,
                            raw_payload   = JSON.stringify($json).slice(0, 1000),
                            created_at    = now()
   │
   ├──▶ [Google Sheets append → tab=errors]
   │
   └──▶ [Slack post → #training-leads-errors with mention]
```

This workflow has `Continue on Fail = true` and **no retries** so it cannot itself become a source of failures. It deliberately does NOT re-trigger the main workflow.

## 11. Retry policy

Configured on every external-call node (`Classify`, `Draft reply`, `Append <category>`, `Lookup duplicates`, `Create Gmail draft`, `Post to Slack`):

- `Retry On Fail = true`
- `Max Tries = 3`
- `Wait Between Tries = 5000 ms`
- `Continue On Fail = false` (let the Error Trigger handle it)

We do **not** retry the Gmail Trigger itself — it has its own internal polling.

## 12. Cost & latency budget

| Stage           | Budget per email | Tactic                                                        |
|-----------------|------------------|---------------------------------------------------------------|
| Classification  | ≤ $0.0005        | `gpt-4o-mini`, body truncated to 2k chars, `json_object` mode |
| Draft (LEAD only) | ≤ $0.003       | Same model, ~400 output tokens                                |
| Total per LEAD  | ≤ $0.004         | Bounded by output size                                        |
| Wall-clock      | ≤ 8s per email   | Gmail poll 1m + AI ~2s + Sheets ~1s + Slack ~0.5s             |

A typical sprint stays well under the **$5 LLM spend** rubric cap from §V of the PDF. See [`COST_TRACKING.md`](COST_TRACKING.md) for the live log.
