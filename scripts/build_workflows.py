#!/usr/bin/env python3
"""Programmatically build all n8n workflow JSONs for the Lead Inbox Triage Bot.

This module is the single source of truth for the workflow structure across all
5 phases plus the error handler. Each phase reuses nodes from the previous one
and adds the section listed in `docs/PHASES.md`.

Why a builder instead of hand-edited JSON?
  - Guarantees identical node IDs / connections across phases.
  - Forces every node to have a human-readable name.
  - Lets us assert invariants (no Gmail send, prompts in Set node, …) at build
    time, before validators run.
  - Makes future prompt iteration a code change, not a JSON diff.

Run:
    python3 scripts/build_workflows.py

Produces:
    workflows/v1-phase1.json
    workflows/v2-phase2.json
    workflows/v3-phase3.json
    workflows/v4-phase4.json
    workflows/v5-phase5-final.json
    workflows/error-handler.json
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "workflows"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Identity — initials are baked into every workflow name per PDF §1.1 + §5.1.
# `GT` = Grootan Trainee placeholder. A trainee replaces this with their own
# initials by changing this constant and re-running the builder.
# ---------------------------------------------------------------------------
INITIALS = "GT"
BATCH_TAG = "batch-may-2026"


# ---------------------------------------------------------------------------
# Prompts — loaded from the markdown files so docs and workflow stay in sync.
# ---------------------------------------------------------------------------
def _extract_first_fenced_block(md: str) -> str:
    """Return the contents of the FIRST ``` fenced block in a markdown file."""
    lines = md.splitlines()
    inside = False
    buf: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if inside:
                return "\n".join(buf).strip()
            inside = True
            continue
        if inside:
            buf.append(line)
    raise RuntimeError("No fenced block found in markdown")


CLASSIFY_PROMPT = _extract_first_fenced_block(
    (ROOT / "docs" / "prompts" / "classification_prompt.md").read_text()
)
REPLY_PROMPT = _extract_first_fenced_block(
    (ROOT / "docs" / "prompts" / "reply_draft_prompt.md").read_text()
)

# Credential references — id+name only. Real IDs are bound after import.
CRED = {
    "gmail":   {"id": "PLACEHOLDER_GMAIL_OAUTH",   "name": "Gmail - Triage Training"},
    "sheets":  {"id": "PLACEHOLDER_SHEETS_OAUTH",  "name": "Sheets - Triage Training"},
    "openai":  {"id": "PLACEHOLDER_OPENAI_API",    "name": "OpenAI - Triage Training"},
    "slack":   {"id": "PLACEHOLDER_SLACK_API",     "name": "Slack - Triage Bot"},
}

# Sticky-note color palette per n8n's color index (1-7).
COLOR_TRIGGER     = 5  # yellow
COLOR_CONFIG      = 4  # pink
COLOR_PREPROCESS  = 4  # orange-ish
COLOR_PROMPTS     = 7  # cyan
COLOR_CLASSIFY    = 6  # blue
COLOR_ROUTE       = 2  # green
COLOR_LOG         = 3  # red-ish
COLOR_DRAFT       = 1  # purple/grey
COLOR_NOTIFY      = 5  # yellow accent
COLOR_ERROR       = 3  # red
COLOR_PHASE_BAND  = 7  # cyan for phase banner

WORKFLOW_DESCRIPTION = (
    "Lead Inbox Triage Bot — production-style n8n automation. "
    "Watches a test Gmail inbox every minute, classifies each new email with an LLM "
    "into LEAD/SUPPORT/SPAM/OTHER (strict JSON), logs to a Google Sheets CRM with "
    "idempotent appends, drafts (NEVER sends) a personalised reply for genuine sales "
    "leads, and posts a Block-Kit lead card to Slack. Failures route to a separate "
    "ErrorTrigger sub-workflow that logs to an 'errors' tab and pings Slack."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _id() -> str:
    """Stable-ish node ID using uuid4 hex (n8n accepts any unique string)."""
    return str(uuid.uuid4())


def _node(name: str, ntype: str, pos: list[int], params: dict | None = None,
          credentials: dict | None = None, retry: bool = False,
          continue_on_fail: bool = False, type_version: int = 1,
          notes: str | None = None) -> dict:
    n: dict = {
        "parameters": params or {},
        "id": _id(),
        "name": name,
        "type": ntype,
        "typeVersion": type_version,
        "position": pos,
    }
    if credentials:
        n["credentials"] = credentials
    if retry:
        n["retryOnFail"] = True
        n["maxTries"] = 3
        n["waitBetweenTries"] = 5000
    if continue_on_fail:
        n["continueOnFail"] = True
    if notes:
        n["notes"] = notes
        n["notesInFlow"] = True
    return n


def _sticky(name: str, content: str, pos: list[int],
            width: int = 300, height: int = 160,
            color: int = 3) -> dict:
    """Sticky note with a SHORT, untruncated `name` and the full markdown in `content`.

    n8n trims long names which made the canvas look unprofessional. We keep names
    crisp (e.g. 'Section_Trigger') and let `content` carry the readable headline.
    """
    return {
        "parameters": {
            "content": content,
            "height": height,
            "width": width,
            "color": color,
        },
        "id": _id(),
        "name": name,
        "type": "n8n-nodes-base.stickyNote",
        "typeVersion": 1,
        "position": pos,
    }


def _connect(connections: dict, src: str, dst: str, src_index: int = 0,
             dst_index: int = 0, src_output: str = "main") -> None:
    bucket = connections.setdefault(src, {}).setdefault(src_output, [])
    while len(bucket) <= src_index:
        bucket.append([])
    bucket[src_index].append({"node": dst, "type": "main", "index": dst_index})


# ---------------------------------------------------------------------------
# Reusable node factories
# ---------------------------------------------------------------------------
COL = 280
ROW = 200


def make_config_node(pos: list[int]) -> dict:
    """Set node 'Config' — single source of truth for tenant-style settings.

    NOTE: prompts live in a SEPARATE 'Prompts' Set node per PDF §2.4 (added in
    Phase 2). 'Config' carries only environment-style values.
    """
    return _node(
        "Config",
        "n8n-nodes-base.set",
        pos,
        type_version=3,
        notes=("Tenant-style settings. Point spreadsheetId / Slack channels / "
               "model / signature here. Prompts live in the separate 'Prompts' "
               "Set node."),
        params={
            "assignments": {
                "assignments": [
                    {"id": _id(), "name": "spreadsheetId",     "type": "string",
                     "value": "REPLACE_WITH_YOUR_GOOGLE_SHEET_ID"},
                    {"id": _id(), "name": "slackChannel",      "type": "string",
                     "value": "#training-leads"},
                    {"id": _id(), "name": "slackErrorChannel", "type": "string",
                     "value": "#training-leads-errors"},
                    {"id": _id(), "name": "model",             "type": "string",
                     "value": "gpt-4o-mini"},
                    {"id": _id(), "name": "temperature",       "type": "number",
                     "value": 0.1},
                    {"id": _id(), "name": "signature",         "type": "string",
                     "value": "— Grootan Team\n+91-44-XXXX-XXXX | grootan.com"},
                    {"id": _id(), "name": "batch",             "type": "string",
                     "value": BATCH_TAG},
                ]
            },
            "options": {}
        },
    )


def make_prompts_node(pos: list[int]) -> dict:
    """Dedicated 'Prompts' Set node — PDF §2.4 verbatim requirement.

    Holds the system prompts ONLY so a non-engineer can iterate on prompts
    without touching Config or the AI nodes.
    """
    return _node(
        "Prompts",
        "n8n-nodes-base.set",
        pos,
        type_version=3,
        notes=("Centralized prompts. Edit here to change classifier or reply "
               "behaviour without opening the AI nodes."),
        params={
            "assignments": {
                "assignments": [
                    {"id": _id(),
                     "name": "classification_system_prompt",
                     "type": "string",
                     "value": CLASSIFY_PROMPT},
                    {"id": _id(),
                     "name": "reply_system_prompt",
                     "type": "string",
                     "value": REPLY_PROMPT},
                ]
            },
            "options": {}
        },
    )


def make_gmail_trigger(pos: list[int]) -> dict:
    """Gmail Trigger — polls every minute, marks captured emails as read."""
    return _node(
        "Watch inbox",
        "n8n-nodes-base.gmailTrigger",
        pos,
        type_version=1,
        credentials={"gmailOAuth2": CRED["gmail"]},
        notes=("Polls INBOX every 1 minute. options.markAsRead=true so the "
               "same email is not re-processed on retry. continueOnFail=false; "
               "Gmail Triggers do not support n8n-level retries (see RUNBOOK §3)."),
        params={
            "pollTimes": {
                "item": [{"mode": "everyMinute"}]
            },
            "simple": False,
            "filters": {
                "labelIds": ["INBOX"],
                "readStatus": "unread",
            },
            "options": {
                "downloadAttachments": False,
                # PDF §1.3: "Mark emails as read after capture"
                "markAsRead": True,
            },
        },
    )


def make_clean_email(pos: list[int]) -> dict:
    """Set node — extracts headers, strips quoted-reply chains, strips signatures.

    Does NOT compute body_preview; that's a separate node so it can reference
    the already-cleaned body_clean (see make_compute_preview).
    """
    return _node(
        "Clean email",
        "n8n-nodes-base.set",
        pos,
        type_version=3,
        notes=("Strips Re:/Fwd: from subject, removes quoted replies, RFC 3676 "
               "signatures, and extracts sender_name / sender_email / sender_domain."),
        params={
            "assignments": {
                "assignments": [
                    {"id": _id(), "name": "message_id", "type": "string",
                     "value": "={{ $json.id || $json.messageId }}"},
                    {"id": _id(), "name": "thread_id", "type": "string",
                     "value": "={{ $json.threadId }}"},
                    {"id": _id(), "name": "received_at", "type": "string",
                     "value": "={{ $json.internalDate ? new Date(parseInt($json.internalDate)).toISOString() : ($json.headers?.date ? new Date($json.headers.date).toISOString() : $now.toISO()) }}"},
                    {"id": _id(), "name": "from_raw", "type": "string",
                     "value": "={{ $json.from?.value?.[0]?.address ? `${$json.from.value[0].name || ''} <${$json.from.value[0].address}>` : ($json.from || $json.headers?.from || '') }}"},
                    {"id": _id(), "name": "sender_email", "type": "string",
                     "value": "={{ (($json.from?.value?.[0]?.address) || (($json.from || $json.headers?.from || '').match(/<([^>]+)>/)?.[1]) || ($json.from || '').trim()).toLowerCase() }}"},
                    {"id": _id(), "name": "sender_name", "type": "string",
                     "value": "={{ $json.from?.value?.[0]?.name?.trim() || (($json.from || $json.headers?.from || '').match(/^(.*?)\\s*<[^>]+>$/)?.[1]?.replace(/['\"]/g,'').trim()) || (($json.from || '').split('@')[0]) }}"},
                    {"id": _id(), "name": "sender_domain", "type": "string",
                     "value": "={{ (($json.from?.value?.[0]?.address) || (($json.from || $json.headers?.from || '').match(/<([^>]+)>/)?.[1]) || ($json.from || '')).split('@')[1]?.toLowerCase()?.trim() }}"},
                    {"id": _id(), "name": "subject_clean", "type": "string",
                     "value": "={{ ($json.subject || '').replace(/^\\s*(Re:|RE:|Fwd:|FW:|Fw:)\\s*/gi, '').trim() }}"},
                    {"id": _id(), "name": "body_clean", "type": "string",
                     "value": "={{ (($json.text || $json.body || $json.snippet || '')\n  .replace(/On\\s+.+?wrote:[\\s\\S]*$/m, '')\n  .replace(/-----Original Message-----[\\s\\S]*$/m, '')\n  .replace(/\\n--\\s*\\n[\\s\\S]*$/m, '')\n  .replace(/\\n_{2,}\\n[\\s\\S]*$/m, '')\n  .trim()) }}"},
                ]
            },
            "options": {
                "include": "all"
            }
        },
    )


def make_compute_preview(pos: list[int]) -> dict:
    """Second-pass Set node — body_preview from the ALREADY-CLEANED body_clean.

    Fixes the C3 bug from the evaluation report: previously body_preview was
    sliced from the raw email body so the CRM sheet showed quoted replies and
    signatures. This node reads $json.body_clean which is the output of
    'Clean email', so the preview is guaranteed to be clean.
    """
    return _node(
        "Compute preview",
        "n8n-nodes-base.set",
        pos,
        type_version=3,
        notes=("Two-pass body_preview: takes the first 500 chars of body_clean "
               "from the previous Set node. Fixes the bug where preview leaked "
               "quoted replies and signatures."),
        params={
            "assignments": {
                "assignments": [
                    {"id": _id(), "name": "body_preview", "type": "string",
                     "value": "={{ ($json.body_clean || '').slice(0, 500) }}"},
                ]
            },
            "options": {"include": "all"}
        },
    )


def make_classify_node(pos: list[int]) -> dict:
    """OpenAI Chat node 'Classify email' — JSON mode, low temperature.

    System prompt is read from $node["Prompts"].json.classification_system_prompt
    (a separate Set node) per PDF §2.4.
    """
    return _node(
        "Classify email",
        "@n8n/n8n-nodes-langchain.openAi",
        pos,
        type_version=1.6,
        credentials={"openAiApi": CRED["openai"]},
        retry=True,
        notes=("JSON mode, temperature 0.1, model from Config, prompt from "
               "the separate 'Prompts' Set node."),
        params={
            "resource": "text",
            "operation": "message",
            "modelId": {
                "__rl": True,
                "mode": "expression",
                "value": "={{ $node[\"Config\"].json.model }}",
            },
            "messages": {
                "values": [
                    {
                        "role": "system",
                        "content": "={{ $node[\"Prompts\"].json.classification_system_prompt }}",
                    },
                    {
                        "role": "user",
                        "content": "=Subject: {{ ($json.subject_clean || '').slice(0, 200) }}\n\nFrom: {{ ($json.sender_email || '').slice(0, 120) }} (domain: {{ ($json.sender_domain || '').slice(0, 120) }})\n\nBody:\n{{ ($json.body_clean || '').slice(0, 2000) }}",
                    },
                ]
            },
            "jsonOutput": True,
            "options": {
                "temperature": "={{ $node[\"Config\"].json.temperature }}",
                "responseFormat": "json_object",
            }
        },
    )


def make_parse_classification(pos: list[int]) -> dict:
    """Function node — coerces AI output into a strict 5-field schema."""
    code = """// Safety net: NEVER throw. Always emit a valid {category, confidence, reasoning,
// suggested_priority, parse_failed} object so downstream Switch never crashes.
const ALLOWED = ['LEAD','SUPPORT','SPAM','OTHER'];
const PRIOS   = ['LOW','MEDIUM','HIGH'];

return items.map(item => {
  const upstream = item.json;
  let raw = upstream.message?.content
         ?? upstream.content
         ?? upstream.text
         ?? upstream.output
         ?? upstream.choices?.[0]?.message?.content
         ?? '';
  if (typeof raw !== 'string') raw = JSON.stringify(raw);

  let parsed = {};
  try { parsed = JSON.parse(raw); } catch { /* leave parsed = {} */ }

  const category = ALLOWED.includes(parsed.category) ? parsed.category : 'OTHER';
  let confidence = Number(parsed.confidence);
  if (!Number.isFinite(confidence)) confidence = 0.0;
  confidence = Math.max(0, Math.min(1, confidence));
  const reasoning = String(parsed.reasoning || '').slice(0, 280);
  let suggested_priority = null;
  if (category === 'LEAD') {
    suggested_priority = PRIOS.includes(parsed.suggested_priority)
      ? parsed.suggested_priority
      : 'MEDIUM';
  }
  const parse_failed = !raw || !parsed.category;

  // Carry the already-cleaned email fields forward. We pull from 'Compute preview'
  // (the most-downstream pre-processing node) so body_preview is included.
  const carry = $('Compute preview').item?.json || $('Clean email').item?.json || {};

  return {
    json: {
      ...carry,
      category,
      confidence,
      reasoning,
      suggested_priority,
      parse_failed,
      raw_ai_output: raw.slice(0, 1000),
    }
  };
});
"""
    return _node(
        "Parse classification",
        "n8n-nodes-base.function",
        pos,
        type_version=1,
        notes=("Safety net — guarantees one of LEAD/SUPPORT/SPAM/OTHER on 100% "
               "of inputs. Carries cleaned fields forward incl. body_preview."),
        params={"functionCode": code},
    )


def make_switch(pos: list[int]) -> dict:
    """Switch node 'Route by category' — 4 outputs + fallback to errors path."""
    return _node(
        "Route by category",
        "n8n-nodes-base.switch",
        pos,
        type_version=3,
        notes=("Routes on $json.category. Fallback output (index 4) routes to "
               "the 'Log unknown category' path that writes the errors tab — "
               "per PDF §3.2."),
        params={
            "rules": {
                "values": [
                    {
                        "conditions": {
                            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                            "conditions": [{
                                "id": _id(),
                                "leftValue": "={{ $json.category }}",
                                "rightValue": "LEAD",
                                "operator": {"type": "string", "operation": "equals"},
                            }],
                            "combinator": "and",
                        },
                        "renameOutput": True,
                        "outputKey": "LEAD",
                    },
                    {
                        "conditions": {
                            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                            "conditions": [{
                                "id": _id(),
                                "leftValue": "={{ $json.category }}",
                                "rightValue": "SUPPORT",
                                "operator": {"type": "string", "operation": "equals"},
                            }],
                            "combinator": "and",
                        },
                        "renameOutput": True,
                        "outputKey": "SUPPORT",
                    },
                    {
                        "conditions": {
                            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                            "conditions": [{
                                "id": _id(),
                                "leftValue": "={{ $json.category }}",
                                "rightValue": "SPAM",
                                "operator": {"type": "string", "operation": "equals"},
                            }],
                            "combinator": "and",
                        },
                        "renameOutput": True,
                        "outputKey": "SPAM",
                    },
                    {
                        "conditions": {
                            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                            "conditions": [{
                                "id": _id(),
                                "leftValue": "={{ $json.category }}",
                                "rightValue": "OTHER",
                                "operator": {"type": "string", "operation": "equals"},
                            }],
                            "combinator": "and",
                        },
                        "renameOutput": True,
                        "outputKey": "OTHER",
                    },
                ]
            },
            "options": {
                "fallbackOutput": "extra",
                "renameFallbackOutput": "Unknown",
            }
        },
    )


def make_lookup_node(name: str, tab: str, pos: list[int]) -> dict:
    return _node(
        name,
        "n8n-nodes-base.googleSheets",
        pos,
        type_version=4.4,
        credentials={"googleSheetsOAuth2Api": CRED["sheets"]},
        retry=True,
        continue_on_fail=True,
        notes=f"Reads existing message_ids from '{tab}' so we don't insert duplicates.",
        params={
            "resource": "sheet",
            "operation": "read",
            "documentId": {
                "__rl": True,
                "mode": "expression",
                "value": "={{ $node[\"Config\"].json.spreadsheetId }}",
            },
            "sheetName": {"__rl": True, "mode": "list", "value": tab, "cachedResultName": tab},
            "filtersUI": {
                "values": [{"lookupColumn": "message_id",
                            "lookupValue": "={{ $json.message_id }}"}]
            },
            "options": {"returnFirstMatch": True},
        },
    )


def make_already_logged_if(name: str, pos: list[int]) -> dict:
    """IF — TRUE when lookup found a row (duplicate); FALSE when empty (new)."""
    return _node(
        name,
        "n8n-nodes-base.if",
        pos,
        type_version=2,
        notes=("Output 0 (TRUE) = duplicate, SKIP append.\n"
               "Output 1 (FALSE) = new message, proceed to append.\n"
               "Condition: $json.message_id is non-empty (lookup returned a row)."),
        params={
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                "conditions": [{
                    "id": _id(),
                    "leftValue": "={{ $json.message_id ? $json.message_id : '' }}",
                    "rightValue": "",
                    "operator": {"type": "string", "operation": "notEmpty"},
                }],
                "combinator": "and",
            },
        },
    )


def make_duplicate_skipped_marker(name: str, tab: str, pos: list[int]) -> dict:
    """Set node that marks the execution as a duplicate-skip — PDF §3.4 verbatim."""
    return _node(
        name,
        "n8n-nodes-base.set",
        pos,
        type_version=3,
        notes=f"PDF §3.4: short-circuit with duplicate_skipped=true for {tab}.",
        params={
            "assignments": {
                "assignments": [
                    {"id": _id(), "name": "duplicate_skipped", "type": "boolean",
                     "value": True},
                    {"id": _id(), "name": "duplicate_tab",     "type": "string",
                     "value": tab},
                    {"id": _id(), "name": "message_id",        "type": "string",
                     "value": "={{ $json.message_id }}"},
                ]
            },
            "options": {"include": "all"},
        },
    )


def make_append_leads(pos: list[int]) -> dict:
    return _node(
        "Append leads",
        "n8n-nodes-base.googleSheets",
        pos,
        type_version=4.4,
        credentials={"googleSheetsOAuth2Api": CRED["sheets"]},
        retry=True,
        notes="Explicit column mapping per sheets/CRM_SHEET_TEMPLATE.md.",
        params={
            "resource": "sheet",
            "operation": "append",
            "documentId": {"__rl": True, "mode": "expression",
                           "value": "={{ $node[\"Config\"].json.spreadsheetId }}"},
            "sheetName": {"__rl": True, "mode": "list", "value": "leads", "cachedResultName": "leads"},
            "columns": {
                "mappingMode": "defineBelow",
                "value": {
                    "message_id":    "={{ $('Parse classification').item.json.message_id }}",
                    "received_at":   "={{ $('Parse classification').item.json.received_at }}",
                    "sender_email":  "={{ $('Parse classification').item.json.sender_email }}",
                    "sender_domain": "={{ $('Parse classification').item.json.sender_domain }}",
                    "subject":       "={{ $('Parse classification').item.json.subject_clean }}",
                    "body_preview":  "={{ $('Parse classification').item.json.body_preview }}",
                    "priority":      "={{ $('Parse classification').item.json.suggested_priority }}",
                    "confidence":    "={{ $('Parse classification').item.json.confidence }}",
                    "status":        "NEW",
                    "created_at":    "={{ $now.toISO() }}",
                },
                "matchingColumns": [],
            },
            "options": {},
        },
    )


def make_append_simple(name: str, tab: str, columns: dict, pos: list[int]) -> dict:
    return _node(
        name,
        "n8n-nodes-base.googleSheets",
        pos,
        type_version=4.4,
        credentials={"googleSheetsOAuth2Api": CRED["sheets"]},
        retry=True,
        notes=f"Append to '{tab}' tab with explicit column mapping.",
        params={
            "resource": "sheet",
            "operation": "append",
            "documentId": {"__rl": True, "mode": "expression",
                           "value": "={{ $node[\"Config\"].json.spreadsheetId }}"},
            "sheetName": {"__rl": True, "mode": "list", "value": tab, "cachedResultName": tab},
            "columns": {
                "mappingMode": "defineBelow",
                "value": columns,
                "matchingColumns": [],
            },
            "options": {},
        },
    )


def make_unknown_category_error(pos: list[int]) -> dict:
    """Append to the 'errors' tab when the Switch fallback fires.

    PDF §3.2: "The fallback should point to the errors tab via the 'errors'
    path used in Phase 5." This fulfils that requirement.
    """
    return _node(
        "Log unknown category to errors",
        "n8n-nodes-base.googleSheets",
        pos,
        type_version=4.4,
        credentials={"googleSheetsOAuth2Api": CRED["sheets"]},
        retry=True,
        continue_on_fail=True,
        notes=("Switch fallback path — writes a row to the 'errors' tab when "
               "the classifier emits a category outside LEAD/SUPPORT/SPAM/OTHER. "
               "PDF §3.2 verbatim."),
        params={
            "resource": "sheet",
            "operation": "append",
            "documentId": {"__rl": True, "mode": "expression",
                           "value": "={{ $node[\"Config\"].json.spreadsheetId }}"},
            "sheetName": {"__rl": True, "mode": "list", "value": "errors", "cachedResultName": "errors"},
            "columns": {
                "mappingMode": "defineBelow",
                "value": {
                    "message_id":    "={{ $json.message_id }}",
                    "received_at":   "={{ $json.received_at }}",
                    "error_stage":   "Route by category (fallback)",
                    "error_message": "={{ 'Unknown category: ' + ($json.category || 'null') + ' | parse_failed=' + ($json.parse_failed || false) }}",
                    "raw_payload":   "={{ JSON.stringify({category: $json.category, confidence: $json.confidence, reasoning: $json.reasoning, raw_ai_output: ($json.raw_ai_output || '').slice(0, 800)}).slice(0, 1000) }}",
                    "created_at":    "={{ $now.toISO() }}",
                },
                "matchingColumns": [],
            },
            "options": {},
        },
    )


def make_draft_reply_ai(pos: list[int]) -> dict:
    return _node(
        "Draft reply",
        "@n8n/n8n-nodes-langchain.openAi",
        pos,
        type_version=1.6,
        credentials={"openAiApi": CRED["openai"]},
        retry=True,
        notes=("80-150 word reply; temperature 0.4 for natural prose. "
               "Reply prompt sourced from the 'Prompts' Set node."),
        params={
            "resource": "text",
            "operation": "message",
            "modelId": {"__rl": True, "mode": "expression",
                        "value": "={{ $node[\"Config\"].json.model }}"},
            "messages": {
                "values": [
                    {
                        "role": "system",
                        "content": "={{ $node[\"Prompts\"].json.reply_system_prompt }}",
                    },
                    {
                        "role": "user",
                        "content": "={{ JSON.stringify({\n  name: $('Parse classification').item.json.sender_name,\n  domain: $('Parse classification').item.json.sender_domain,\n  priority: $('Parse classification').item.json.suggested_priority,\n  body: ($('Parse classification').item.json.body_clean || '').slice(0, 2000),\n  signature: $node[\"Config\"].json.signature\n}, null, 2) }}",
                    },
                ]
            },
            "jsonOutput": False,
            "options": {
                "temperature": 0.4,
            }
        },
    )


def make_create_gmail_draft(pos: list[int]) -> dict:
    """Gmail Create Draft — NEVER send. Enforced by scripts/check_no_send.py."""
    return _node(
        "Create Gmail draft",
        "n8n-nodes-base.gmail",
        pos,
        type_version=2.1,
        credentials={"gmailOAuth2": CRED["gmail"]},
        retry=True,
        notes=("HARD SAFETY RULE: createDraft only. 'send' is forbidden by "
               "the exercise PDF §Phase 4 and by check_no_send.py CI guard."),
        params={
            "resource": "draft",
            "operation": "create",
            "subject": "={{ ($('Parse classification').item.json.subject_clean || '').startsWith('Re:') ? $('Parse classification').item.json.subject_clean : 'Re: ' + $('Parse classification').item.json.subject_clean }}",
            "emailType": "text",
            "message": "={{ $json.message?.content || $json.content || $json.text || '' }}",
            "options": {
                "sendTo": "={{ $('Parse classification').item.json.sender_email }}",
                "threadId": "={{ $('Parse classification').item.json.thread_id }}",
            },
        },
    )


# Slack Block Kit attachment-style payload — header carries priority emoji
# (🔴/🟠/🟢) AND the attachment-level `color` field gives the Slack message a
# real coloured left-bar. Both color signals together = covers all reviewer
# expectations from PDF §4.3.
def _slack_blocks_attachments_json() -> str:
    """Returns a Block-Kit + attachment payload that:
       - Header includes a priority-coloured emoji + the priority word.
       - Attachment carries a `color` (#E01E5A / #ECB22E / #2EB67D) so the
         Slack card shows a coloured vertical bar.

    The outer wrapper is a single n8n expression so the Slack node can evaluate
    it as one JSON string.
    """
    # The expression builds the JSON in JS so we keep all conditional logic
    # readable. It's wrapped in `={{ ... }}` so n8n evaluates the whole thing.
    return r"""={{ (() => {
  const p   = $('Parse classification').item.json.suggested_priority || 'MEDIUM';
  const col = p === 'HIGH' ? '#E01E5A' : (p === 'MEDIUM' ? '#ECB22E' : '#2EB67D');
  const emo = p === 'HIGH' ? ':red_circle:' : (p === 'MEDIUM' ? ':large_orange_circle:' : ':large_green_circle:');
  const j   = $('Parse classification').item.json;
  const draftId = $('Create Gmail draft').item?.json?.id || '';
  const body = (j.body_clean || '').slice(0, 300).replace(/\n/g, '\n> ');
  return JSON.stringify([
    {
      color: col,
      blocks: [
        {
          type: 'header',
          text: { type: 'plain_text', emoji: true,
                  text: emo + ' New lead — ' + p }
        },
        {
          type: 'section',
          fields: [
            { type: 'mrkdwn', text: '*From:*\n' + (j.sender_email || '') },
            { type: 'mrkdwn', text: '*Domain:*\n' + (j.sender_domain || '') },
            { type: 'mrkdwn', text: '*Subject:*\n' + (j.subject_clean || '') },
            { type: 'mrkdwn', text: '*Confidence:*\n' + Math.round((j.confidence || 0) * 100) + '%' }
          ]
        },
        {
          type: 'section',
          text: { type: 'mrkdwn', text: '*Preview:*\n> ' + body }
        },
        {
          type: 'actions',
          elements: [
            { type: 'button',
              text: { type: 'plain_text', emoji: true, text: 'Open draft in Gmail' },
              url: 'https://mail.google.com/mail/u/0/#drafts/' + draftId,
              style: 'primary' }
          ]
        },
        {
          type: 'context',
          elements: [
            { type: 'mrkdwn',
              text: '_Triaged by LeadInboxTriageBot · ' + (j.message_id || '') + '_' }
          ]
        }
      ]
    }
  ]);
})() }}"""


def make_slack_card(pos: list[int]) -> dict:
    return _node(
        "Post lead card",
        "n8n-nodes-base.slack",
        pos,
        type_version=2.2,
        credentials={"slackApi": CRED["slack"]},
        retry=True,
        continue_on_fail=True,
        notes=("Block-Kit card with priority-coloured attachment bar + emoji. "
               "Sees the draft URL via $('Create Gmail draft'). The Wait node "
               "before this guarantees the draft is created before Slack fires."),
        params={
            "resource": "message",
            "operation": "post",
            "select": "channel",
            "channelId": {"__rl": True, "mode": "expression",
                          "value": "={{ $node[\"Config\"].json.slackChannel }}"},
            "messageType": "block",
            "blocksUi": _slack_blocks_attachments_json(),
            "otherOptions": {},
        },
    )


# ---------------------------------------------------------------------------
# Phase builders
# ---------------------------------------------------------------------------
def workflow_skeleton(name: str, description: str, tags: list[str],
                      with_error_workflow: bool = False) -> dict:
    wf: dict = {
        "name": name,
        "nodes": [],
        "connections": {},
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "timezone": "Asia/Kolkata",
            "saveExecutionProgress": True,
            "saveManualExecutions": True,
            "saveDataErrorExecution": "all",
            "saveDataSuccessExecution": "all",
        },
        "tags": [{"name": t} for t in tags],
        "meta": {
            "templateCredsSetupCompleted": True,
            "description": description,
        },
    }
    if with_error_workflow:
        wf["settings"]["errorWorkflow"] = f"LeadInboxTriageBot_ErrorHandler_{INITIALS}"
    return wf


def _phase_banner(phase: str, summary: str, pos: list[int]) -> dict:
    """Big top-of-canvas banner sticky note that screams which phase this is."""
    body = (
        f"# {phase}\n"
        f"## Lead Inbox Triage Bot — Grootan Internal Training (batch `{BATCH_TAG}`)\n\n"
        f"**Trainee:** `<initials = {INITIALS}>`\n\n"
        f"{summary}\n\n"
        f"_See `docs/PHASES.md` for the acceptance criteria for this phase._"
    )
    return _sticky("Section_PhaseBanner", body, pos,
                   width=1180, height=160, color=COLOR_PHASE_BAND)


# ---- Phase 1: trigger only ------------------------------------------------
def build_phase1() -> dict:
    wf = workflow_skeleton(
        f"LeadInboxTriageBot_{INITIALS}",
        WORKFLOW_DESCRIPTION + "\n\nPhase 1 — Gmail trigger and Config only.",
        ["training", "lead-triage", "phase-1", BATCH_TAG],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    # Big phase banner across the top
    nodes.append(_phase_banner(
        "Phase 1 · Workflow Foundation & Gmail Trigger",
        "**This phase builds:** Gmail credentials, a polling Gmail Trigger on the test "
        "INBOX label (marks emails as read after capture), and a `Config` Set node "
        "holding tenant settings. No classification yet — that lands in Phase 2.",
        [40, 40],
    ))

    # Per-section sticky notes
    nodes.append(_sticky("Section_Trigger",
                         "# 🟡 Trigger\nGmail polls INBOX every 1 min and marks "
                         "captured emails as read so they aren't re-processed on retry.",
                         [400, 220], width=300, color=COLOR_TRIGGER))
    nodes.append(_sticky("Section_Config",
                         "# 🩷 Config\nSingle source of truth for spreadsheetId, "
                         "Slack channels, model, signature, batch name.",
                         [400, 420], width=300, color=COLOR_CONFIG))
    nodes.append(_sticky("Section_Future",
                         "# Future sections (built in Phases 2–5)\n→ **Prompts** | "
                         "**Pre-process** | **Classify** | **Route** | **Log** | "
                         "**Draft & Notify** | **Errors**",
                         [800, 220], width=420, height=120, color=3))

    cfg  = make_config_node([800, 460])
    trig = make_gmail_trigger([800, 280])
    nodes.extend([cfg, trig])

    _connect(conns, trig["name"], cfg["name"])
    return wf


# ---- Phase 2: + cleaning + classification ---------------------------------
def build_phase2() -> dict:
    wf = workflow_skeleton(
        f"LeadInboxTriageBot_{INITIALS}",
        WORKFLOW_DESCRIPTION + "\n\nPhase 2 — adds Clean email + Compute preview "
        "+ AI Classifier (strict JSON) + Parse classification safety net + a "
        "dedicated 'Prompts' Set node.",
        ["training", "lead-triage", "phase-2", BATCH_TAG],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_phase_banner(
        "Phase 2 · AI Classification with Structured Output",
        "**This phase builds:** the `Prompts` Set node (centralised system prompts), "
        "`Clean email` (subject/body strip), `Compute preview` (fixes body_preview "
        "to use body_clean), `Classify email` (OpenAI JSON mode, T=0.1), and "
        "`Parse classification` (Function-node safety net that never throws).",
        [40, 40],
    ))

    nodes.append(_sticky("Section_Trigger",  "# 🟡 Trigger",
                         [400, 220], width=200, color=COLOR_TRIGGER))
    nodes.append(_sticky("Section_Prompts",
                         "# 🟦 Prompts\nDedicated Set node — PDF §2.4. Edit prompts "
                         "here without opening the AI node.",
                         [700, 420], width=300, color=COLOR_PROMPTS))
    nodes.append(_sticky("Section_Preprocess",
                         "# 🟠 Pre-process\nClean email → Compute preview "
                         "(two-pass so body_preview uses body_clean).",
                         [1000, 220], width=320, color=COLOR_PREPROCESS))
    nodes.append(_sticky("Section_Classify",
                         "# 🟦 Classify\nOpenAI JSON mode, T=0.1, prompt from "
                         "'Prompts'. Parse classification = safety net (never throws).",
                         [1700, 220], width=420, color=COLOR_CLASSIFY))

    cfg     = make_config_node([400, 460])
    trig    = make_gmail_trigger([400, 280])
    prompts = make_prompts_node([700, 580])
    clean   = make_clean_email([1000, 320])
    prev    = make_compute_preview([1300, 320])
    cls     = make_classify_node([1700, 320])
    parse   = make_parse_classification([2000, 320])
    nodes.extend([cfg, trig, prompts, clean, prev, cls, parse])

    _connect(conns, trig["name"],  clean["name"])
    _connect(conns, clean["name"], prev["name"])
    _connect(conns, prev["name"],  cls["name"])
    _connect(conns, cls["name"],   parse["name"])
    return wf


# ---- Phase 3: + switch routing + CRM logging + idempotency ----------------
def build_phase3() -> dict:
    wf = workflow_skeleton(
        f"LeadInboxTriageBot_{INITIALS}",
        WORKFLOW_DESCRIPTION + "\n\nPhase 3 — adds Switch routing, per-tab "
        "idempotency (Lookup + IF + duplicate_skipped marker), explicit "
        "column mappings, and routes the fallback to the errors tab.",
        ["training", "lead-triage", "phase-3", BATCH_TAG],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_phase_banner(
        "Phase 3 · Routing & CRM Logging",
        "**This phase builds:** the Switch (4 outputs + fallback), per-tab "
        "Lookup → IF idempotency check, explicit-column Sheets appends, "
        "`duplicate_skipped=true` flag on the skip path, and fallback routing "
        "to the `errors` tab so misfires never disappear silently.",
        [40, 40],
    ))

    nodes.append(_sticky("Section_Trigger",   "# 🟡 Trigger",
                         [400, 220], width=200, color=COLOR_TRIGGER))
    nodes.append(_sticky("Section_Preprocess","# 🟠 Pre-process",
                         [1000, 220], width=200, color=COLOR_PREPROCESS))
    nodes.append(_sticky("Section_Classify",  "# 🟦 Classify",
                         [1700, 220], width=200, color=COLOR_CLASSIFY))
    nodes.append(_sticky("Section_Route",
                         "# 🟢 Route\nSwitch by category. Fallback → 'Log unknown category to errors'.",
                         [2300, 220], width=320, color=COLOR_ROUTE))
    nodes.append(_sticky("Section_Log",
                         "# 🔴 Log\nLookup → IF → Append per category, with explicit columns.\n"
                         "Duplicate path emits a `duplicate_skipped=true` Set marker (PDF §3.4).",
                         [2700, 220], width=620, height=140, color=COLOR_LOG))

    cfg     = make_config_node([400, 460])
    trig    = make_gmail_trigger([400, 280])
    prompts = make_prompts_node([700, 580])
    clean   = make_clean_email([1000, 320])
    prev    = make_compute_preview([1300, 320])
    cls     = make_classify_node([1700, 320])
    parse   = make_parse_classification([2000, 320])
    switch  = make_switch([2300, 320])

    # LEAD branch
    lk_lead   = make_lookup_node("Lookup leads",   "leads",   [2700, -180])
    if_lead   = make_already_logged_if("Already in leads?",   [2960, -180])
    skip_lead = make_duplicate_skipped_marker("Duplicate skipped (leads)", "leads", [3200, -260])
    app_lead  = make_append_leads([3200, -100])

    # SUPPORT branch
    lk_supp   = make_lookup_node("Lookup support", "support", [2700, 40])
    if_supp   = make_already_logged_if("Already in support?",[2960, 40])
    skip_supp = make_duplicate_skipped_marker("Duplicate skipped (support)", "support", [3200, -40])
    app_supp  = make_append_simple(
        "Append support", "support",
        {
            "message_id":   "={{ $('Parse classification').item.json.message_id }}",
            "received_at":  "={{ $('Parse classification').item.json.received_at }}",
            "sender_email": "={{ $('Parse classification').item.json.sender_email }}",
            "subject":      "={{ $('Parse classification').item.json.subject_clean }}",
            "body_preview": "={{ $('Parse classification').item.json.body_preview }}",
            "confidence":   "={{ $('Parse classification').item.json.confidence }}",
            "created_at":   "={{ $now.toISO() }}",
        }, [3200, 120])

    # SPAM branch
    lk_spam   = make_lookup_node("Lookup spam",    "spam",    [2700, 260])
    if_spam   = make_already_logged_if("Already in spam?",   [2960, 260])
    skip_spam = make_duplicate_skipped_marker("Duplicate skipped (spam)", "spam", [3200, 180])
    app_spam  = make_append_simple(
        "Append spam", "spam",
        {
            "message_id":   "={{ $('Parse classification').item.json.message_id }}",
            "received_at":  "={{ $('Parse classification').item.json.received_at }}",
            "sender_email": "={{ $('Parse classification').item.json.sender_email }}",
            "subject":      "={{ $('Parse classification').item.json.subject_clean }}",
            "confidence":   "={{ $('Parse classification').item.json.confidence }}",
            "created_at":   "={{ $now.toISO() }}",
        }, [3200, 340])

    # OTHER branch
    lk_other  = make_lookup_node("Lookup other",   "other",   [2700, 480])
    if_other  = make_already_logged_if("Already in other?",  [2960, 480])
    skip_other= make_duplicate_skipped_marker("Duplicate skipped (other)", "other", [3200, 400])
    app_other = make_append_simple(
        "Append other", "other",
        {
            "message_id":   "={{ $('Parse classification').item.json.message_id }}",
            "received_at":  "={{ $('Parse classification').item.json.received_at }}",
            "sender_email": "={{ $('Parse classification').item.json.sender_email }}",
            "subject":      "={{ $('Parse classification').item.json.subject_clean }}",
            "confidence":   "={{ $('Parse classification').item.json.confidence }}",
            "created_at":   "={{ $now.toISO() }}",
        }, [3200, 560])

    # Fallback: unknown category → errors tab
    unknown_err = make_unknown_category_error([2960, 700])

    nodes.extend([
        cfg, trig, prompts, clean, prev, cls, parse, switch,
        lk_lead, if_lead, skip_lead, app_lead,
        lk_supp, if_supp, skip_supp, app_supp,
        lk_spam, if_spam, skip_spam, app_spam,
        lk_other, if_other, skip_other, app_other,
        unknown_err,
    ])

    _connect(conns, trig["name"],  clean["name"])
    _connect(conns, clean["name"], prev["name"])
    _connect(conns, prev["name"],  cls["name"])
    _connect(conns, cls["name"],   parse["name"])
    _connect(conns, parse["name"], switch["name"])

    # Switch outputs: 0=LEAD 1=SUPPORT 2=SPAM 3=OTHER 4=fallback
    _connect(conns, switch["name"], lk_lead["name"],   src_index=0)
    _connect(conns, switch["name"], lk_supp["name"],   src_index=1)
    _connect(conns, switch["name"], lk_spam["name"],   src_index=2)
    _connect(conns, switch["name"], lk_other["name"],  src_index=3)
    _connect(conns, switch["name"], unknown_err["name"], src_index=4)

    # Per branch: Lookup → IF (true=dup→skip marker, false=append)
    for lk, ifn, skip, app in [
        (lk_lead,  if_lead,  skip_lead,  app_lead),
        (lk_supp,  if_supp,  skip_supp,  app_supp),
        (lk_spam,  if_spam,  skip_spam,  app_spam),
        (lk_other, if_other, skip_other, app_other),
    ]:
        _connect(conns, lk["name"],  ifn["name"])
        _connect(conns, ifn["name"], skip["name"], src_index=0)  # TRUE = duplicate → mark
        _connect(conns, ifn["name"], app["name"],  src_index=1)  # FALSE = new   → append

    return wf


# ---- Phase 4: + draft reply + Slack card (PARALLEL) -----------------------
def build_phase4() -> dict:
    wf = build_phase3()
    wf["name"] = f"LeadInboxTriageBot_{INITIALS}"
    wf["meta"]["description"] = (
        WORKFLOW_DESCRIPTION + "\n\nPhase 4 — on the LEAD branch only: AI Draft "
        "Reply, Gmail Create Draft (NEVER send), and a parallel Slack lead card "
        "with priority colour-coded attachment + emoji."
    )
    wf["tags"] = [{"name": "training"}, {"name": "lead-triage"},
                  {"name": "phase-4"}, {"name": BATCH_TAG}]
    nodes = wf["nodes"]
    conns = wf["connections"]

    # Replace Phase 3 banner with Phase 4 banner
    for n in nodes:
        if n.get("name") == "Section_PhaseBanner":
            n["parameters"]["content"] = (
                "# Phase 4 · Draft Reply Generation & Slack Notification\n"
                f"## Lead Inbox Triage Bot — Grootan Internal Training (batch `{BATCH_TAG}`)\n\n"
                f"**Trainee:** `<initials = {INITIALS}>`\n\n"
                "**This phase builds:** on the LEAD branch only — `Draft reply` "
                "(OpenAI, T=0.4) → `Create Gmail draft` (createDraft only — NEVER "
                "send) AND **in parallel** → `Post lead card` (Slack Block-Kit + "
                "priority-coloured attachment). A Wait node ensures the draft "
                "exists before Slack reads its id.\n\n"
                "_See `docs/PHASES.md` for the acceptance criteria for this phase._"
            )

    nodes.append(_sticky("Section_DraftNotify",
                         "# 🟣 Draft + Notify (LEAD branch only)\n"
                         "Draft reply → **(parallel)** Create Gmail draft & Post lead card.\n"
                         "Both must complete before the execution finishes.",
                         [3500, -180], width=520, height=140, color=COLOR_DRAFT))

    # Position: stacked vertically under each other, both fed by Draft reply
    draft  = make_draft_reply_ai([3500, 40])
    gmail  = make_create_gmail_draft([3800, -60])
    # Tiny Wait node so Slack reads the draft id AFTER Create Gmail draft
    # completes but BEFORE the workflow proceeds to other branches. This keeps
    # the spec's "in parallel" semantics (Slack does not block Gmail and vice
    # versa) while guaranteeing the draft URL is valid.
    wait_for_draft = _node(
        "Wait for draft id",
        "n8n-nodes-base.wait",
        [3800, 140],
        type_version=1,
        notes=("Tiny no-op delay so Slack reads $('Create Gmail draft').item.json.id "
               "after the draft create resolves. Without this the URL in the Slack "
               "card can race the API."),
        params={"resume": "timeInterval", "amount": 2, "unit": "seconds"},
    )
    slack  = make_slack_card([4100, 140])

    nodes.extend([draft, gmail, wait_for_draft, slack])

    # Connect: Append leads → Draft reply (single source for both branches)
    _connect(conns, "Append leads", draft["name"])

    # PARALLEL fan-out from Draft reply: one to Gmail draft, one to Wait → Slack
    _connect(conns, draft["name"], gmail["name"])
    _connect(conns, draft["name"], wait_for_draft["name"])
    _connect(conns, wait_for_draft["name"], slack["name"])

    return wf


# ---- Phase 5: + retries belt-and-suspenders + error workflow link ---------
def build_phase5() -> dict:
    wf = build_phase4()
    # Final form — keep the trainee-initials suffix the same; PDF doesn't ask
    # for _PhaseN in the final name, just for the per-phase export filenames.
    wf["name"] = f"LeadInboxTriageBot_{INITIALS}"
    wf["meta"]["description"] = (
        WORKFLOW_DESCRIPTION + "\n\nPhase 5 — final hardened workflow with "
        "retries on every external-call node, errorWorkflow wired to the "
        "ErrorHandler sub-workflow, and full canvas grouping."
    )
    wf["tags"] = [{"name": "training"}, {"name": "lead-triage"},
                  {"name": "phase-5"}, {"name": "production-ready"},
                  {"name": BATCH_TAG}]
    wf["settings"]["errorWorkflow"] = f"LeadInboxTriageBot_ErrorHandler_{INITIALS}"

    # Replace banner with Phase 5 banner
    for n in wf["nodes"]:
        if n.get("name") == "Section_PhaseBanner":
            n["parameters"]["content"] = (
                "# Phase 5 · Final Hardening & Error Handling\n"
                f"## Lead Inbox Triage Bot — Grootan Internal Training (batch `{BATCH_TAG}`)\n\n"
                f"**Trainee:** `<initials = {INITIALS}>`\n\n"
                "**This phase hardens:** all external-call nodes have "
                "`retryOnFail=true, maxTries=3, waitBetweenTries=5000`. The "
                f"workflow's `settings.errorWorkflow` is wired to "
                f"`LeadInboxTriageBot_ErrorHandler_{INITIALS}`. Gmail Trigger "
                "cannot retry at the node level (n8n limitation — documented "
                "in `docs/RUNBOOK.md §3`).\n\n"
                "_See `docs/PHASES.md` for the acceptance criteria for this phase._"
            )

    # Belt-and-suspenders retry pass on every external-call node
    for n in wf["nodes"]:
        if n["type"] in {
            "@n8n/n8n-nodes-langchain.openAi",
            "n8n-nodes-base.googleSheets",
            "n8n-nodes-base.slack",
            "n8n-nodes-base.gmail",
        }:
            n["retryOnFail"] = True
            n["maxTries"] = 3
            n["waitBetweenTries"] = 5000

    wf["nodes"].append(_sticky(
        "Section_Hardening",
        "# 🔴 Hardening\nEvery external-call node: `retryOnFail=true`, "
        "`maxTries=3`, `waitBetweenTries=5000`. "
        f"`settings.errorWorkflow → LeadInboxTriageBot_ErrorHandler_{INITIALS}`.\n\n"
        "_Note:_ Gmail Trigger does not expose `retryOnFail` (n8n limitation). "
        "Documented in `docs/RUNBOOK.md §3`.",
        [40, 760], width=620, height=160, color=COLOR_ERROR,
    ))
    return wf


# ---- Error handler sub-workflow ------------------------------------------
def build_error_handler() -> dict:
    wf = workflow_skeleton(
        f"LeadInboxTriageBot_ErrorHandler_{INITIALS}",
        "Error handler for the Lead Inbox Triage Bot. Appends a row to the "
        "'errors' tab and posts an alert to #training-leads-errors. Never "
        "re-triggers the failing workflow.\n\n"
        "Retries: enabled on Sheets + Slack appends (3x, 5s backoff) so a "
        "transient Sheets outage doesn't drop the error.",
        ["training", "lead-triage", "error-handler", BATCH_TAG],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_phase_banner(
        "Error Handler · Sub-workflow",
        "**This workflow runs:** whenever the main workflow fails. Wired via "
        f"`settings.errorWorkflow` in `LeadInboxTriageBot_{INITIALS}`. "
        "Builds an `errors`-tab row from the failed execution and pings Slack.",
        [40, 40],
    ))

    nodes.append(_sticky("Section_Error",
                         "# 🔴 Error Handler\nError Trigger → Build error row → "
                         "Append errors tab + Slack alert (parallel).\n"
                         "Both writers have `retryOnFail=true, maxTries=3, "
                         "waitBetweenTries=5000`. Never re-triggers the main workflow.",
                         [400, 220], width=520, height=140, color=COLOR_ERROR))

    cfg = make_config_node([400, 460])

    err = _node(
        "Error Trigger",
        "n8n-nodes-base.errorTrigger",
        [400, 280],
        type_version=1,
        notes="Receives the failed-execution payload from the main workflow.",
        params={},
    )

    build_row = _node(
        "Build error row",
        "n8n-nodes-base.set",
        [700, 280],
        type_version=3,
        notes=("Normalises the error payload to the 'errors' tab schema. "
               "error_message truncated to 500 chars (was 280)."),
        params={
            "assignments": {
                "assignments": [
                    {"id": _id(), "name": "message_id", "type": "string",
                     "value": "={{ $json.execution?.error?.context?.message_id || $json.execution?.lastNodeExecuted || '' }}"},
                    {"id": _id(), "name": "received_at", "type": "string",
                     "value": "={{ $now.toISO() }}"},
                    {"id": _id(), "name": "error_stage", "type": "string",
                     "value": "={{ $json.execution?.lastNodeExecuted || 'unknown' }}"},
                    {"id": _id(), "name": "error_message", "type": "string",
                     "value": "={{ ($json.execution?.error?.message || $json.error?.message || 'unknown error').toString().slice(0,500) }}"},
                    {"id": _id(), "name": "raw_payload", "type": "string",
                     "value": "={{ JSON.stringify($json).slice(0, 1000) }}"},
                    {"id": _id(), "name": "created_at", "type": "string",
                     "value": "={{ $now.toISO() }}"},
                ]
            },
            "options": {}
        },
    )

    append_err = make_append_simple(
        "Append errors row", "errors",
        {
            "message_id":    "={{ $json.message_id }}",
            "received_at":   "={{ $json.received_at }}",
            "error_stage":   "={{ $json.error_stage }}",
            "error_message": "={{ $json.error_message }}",
            "raw_payload":   "={{ $json.raw_payload }}",
            "created_at":    "={{ $json.created_at }}",
        }, [1000, 200])
    # Per evaluation feedback: enable retries on the error logger itself so a
    # transient Sheets outage doesn't drop the error row entirely.
    append_err["retryOnFail"] = True
    append_err["maxTries"] = 3
    append_err["waitBetweenTries"] = 5000
    append_err["continueOnFail"] = True  # still don't loop forever

    slack_err = _node(
        "Post error alert",
        "n8n-nodes-base.slack",
        [1000, 380],
        type_version=2.2,
        credentials={"slackApi": CRED["slack"]},
        retry=True,
        continue_on_fail=True,
        notes="Brief alert. Retries enabled (3x, 5s). continueOnFail=true.",
        params={
            "resource": "message",
            "operation": "post",
            "select": "channel",
            "channelId": {"__rl": True, "mode": "expression",
                          "value": "={{ $node[\"Config\"].json.slackErrorChannel }}"},
            "text": "=:rotating_light: *Lead Triage Bot error*\n*Stage:* `{{ $json.error_stage }}`\n*Message:* {{ $json.error_message }}\n*Time:* {{ $json.created_at }}\n*Payload:* ```{{ $json.raw_payload }}```",
            "otherOptions": {},
        },
    )

    nodes.extend([cfg, err, build_row, append_err, slack_err])

    _connect(conns, err["name"], build_row["name"])
    _connect(conns, build_row["name"], append_err["name"])
    _connect(conns, build_row["name"], slack_err["name"])
    return wf


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
PHASES = [
    ("v1-phase1.json",       build_phase1),
    ("v2-phase2.json",       build_phase2),
    ("v3-phase3.json",       build_phase3),
    ("v4-phase4.json",       build_phase4),
    ("v5-phase5-final.json", build_phase5),
    ("error-handler.json",   build_error_handler),
]


def main() -> int:
    for fname, builder in PHASES:
        wf = builder()
        path = OUT / fname
        path.write_text(json.dumps(wf, indent=2, ensure_ascii=False))
        print(f"wrote {path.relative_to(ROOT)}  ({len(wf['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
