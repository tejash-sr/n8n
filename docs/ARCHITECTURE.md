# Architecture — Lead Inbox Triage Bot

## 1. Design goals (in priority order)

1. **Safety first.** No outbound email is ever sent automatically. Gmail nodes are restricted to `Create Draft`.
2. **Reliability.** Every external call (LLM, Gmail, Sheets, Slack) has retries on transient failures. A separate Error Trigger workflow captures anything that still falls through.
3. **Determinism.** The classifier returns strict JSON with `temperature = 0.1` and a parse-safety net so downstream routing never crashes on a malformed model response.
4. **Idempotency.** Re-running the workflow on the same `message_id` is a no-op — no duplicate rows, no duplicate drafts, no double Slack pings.
5. **Maintainability.** Prompts live in a single `Set node "Prompts"` so non-engineers can iterate without opening the AI node. Every node has a human-readable name. The canvas is grouped into colored sticky-note sections that mirror the 5 phases.

## 2. Logical sections (mirrored on the canvas with sticky notes)

| Color  | Section            | Nodes (typical)                                                                |
|--------|--------------------|---------------------------------------------------------------------------------|
| Yellow | **Trigger**        | `Watch inbox` (Gmail Trigger)                                                  |
| Orange | **Pre-process**    | `Prompts` (Set), `Clean email` (Set/Function)                                  |
| Blue   | **Classify**       | `Classify email` (OpenAI/Anthropic), `Parse classification` (Function)         |
| Purple | **Route**          | `Route by category` (Switch with `LEAD/SUPPORT/SPAM/OTHER/fallback`)           |
| Green  | **Log to CRM**     | `Already logged?` (Sheets read) → `Append <category>` (Sheets append) × 4      |
| Pink   | **Draft & Notify** | `Draft reply` (AI), `Create Gmail draft`, `Post to Slack`                      |
| Red    | **Error handling** | `Error Trigger` (in sub-workflow), `Append errors row`, `Slack error alert`    |

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

// body_preview
{{ $node["Clean email"].json.body_clean.slice(0, 500) }}
```

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

## 9. Slack Block-Kit card

| Block         | Content                                                                 |
|---------------|--------------------------------------------------------------------------|
| `header`      | `🚀 New lead — {priority}` with emoji color per priority                 |
| `section.fields` | `From`, `Subject`, `Confidence` (`{{$json.confidence * 100}}%`), `Domain` |
| `section.text`| First 300 chars of `body_clean`                                          |
| `actions`     | Button **Open draft in Gmail** → `https://mail.google.com/mail/u/0/#drafts/{{$json.draft_id}}` |

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
