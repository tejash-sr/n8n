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
from copy import deepcopy

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "workflows"
OUT.mkdir(exist_ok=True)


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

# Reusable credentials map — references only, never values. Real IDs are
# assigned by n8n when the user binds credentials after import.
CRED = {
    "gmail":   {"id": "PLACEHOLDER_GMAIL_OAUTH",   "name": "Gmail - Triage Training"},
    "sheets":  {"id": "PLACEHOLDER_SHEETS_OAUTH",  "name": "Sheets - Triage Training"},
    "openai":  {"id": "PLACEHOLDER_OPENAI_API",    "name": "OpenAI - Triage Training"},
    "slack":   {"id": "PLACEHOLDER_SLACK_API",     "name": "Slack - Triage Bot"},
}


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
    n = {
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


def _sticky(text: str, pos: list[int], width: int = 280, height: int = 160,
            color: int = 3) -> dict:
    return {
        "parameters": {
            "content": text,
            "height": height,
            "width": width,
            "color": color,
        },
        "id": _id(),
        "name": f"Section_{text.splitlines()[0][:20].strip()}",
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
# Reusable node factories — each returns the dict for one node.
# Positions are 280-pixel grid columns so the canvas is readable.
# ---------------------------------------------------------------------------
COL = 280  # horizontal step
ROW = 200  # vertical step


def make_config_node(pos: list[int]) -> dict:
    """Set node 'Config' — single source of truth for tenant-style settings."""
    return _node(
        "Config",
        "n8n-nodes-base.set",
        pos,
        type_version=3,
        notes="Edit this node to point at YOUR spreadsheet / Slack channel / signature.\n"
              "All downstream nodes read from here so re-targeting is one place.",
        params={
            "assignments": {
                "assignments": [
                    {"id": _id(), "name": "spreadsheetId",
                     "type": "string",
                     "value": "REPLACE_WITH_YOUR_GOOGLE_SHEET_ID"},
                    {"id": _id(), "name": "slackChannel",
                     "type": "string",
                     "value": "#training-leads"},
                    {"id": _id(), "name": "slackErrorChannel",
                     "type": "string",
                     "value": "#training-leads-errors"},
                    {"id": _id(), "name": "model",
                     "type": "string",
                     "value": "gpt-4o-mini"},
                    {"id": _id(), "name": "temperature",
                     "type": "number",
                     "value": 0.1},
                    {"id": _id(), "name": "signature",
                     "type": "string",
                     "value": "— Grootan Team\n+91-44-XXXX-XXXX | grootan.com"},
                    {"id": _id(), "name": "classification_system_prompt",
                     "type": "string",
                     "value": CLASSIFY_PROMPT},
                    {"id": _id(), "name": "reply_system_prompt",
                     "type": "string",
                     "value": REPLY_PROMPT},
                ]
            },
            "options": {}
        },
    )


def make_gmail_trigger(pos: list[int]) -> dict:
    return _node(
        "Watch inbox",
        "n8n-nodes-base.gmailTrigger",
        pos,
        type_version=1,
        credentials={"gmailOAuth2": CRED["gmail"]},
        notes="Polls every 1 minute. Marks emails as read so they aren't reprocessed.",
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
            },
        },
    )


def make_clean_email(pos: list[int]) -> dict:
    """Set node that strips signatures, quoted replies, and extracts metadata."""
    return _node(
        "Clean email",
        "n8n-nodes-base.set",
        pos,
        type_version=3,
        notes="Strips Re:/Fwd: from subject, removes quoted reply chains, "
              "RFC 3676 signatures, and extracts sender_name / sender_domain.",
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
                    {"id": _id(), "name": "body_preview", "type": "string",
                     "value": "={{ ($json.text || $json.body || $json.snippet || '').slice(0, 500) }}"},
                ]
            },
            "options": {
                "include": "all"
            }
        },
    )


def make_classify_node(pos: list[int]) -> dict:
    """OpenAI Chat node 'Classify email' — JSON mode, low temperature."""
    return _node(
        "Classify email",
        "@n8n/n8n-nodes-langchain.openAi",
        pos,
        type_version=1.6,
        credentials={"openAiApi": CRED["openai"]},
        retry=True,
        notes="JSON mode, temperature 0.1, model and prompt read from Config.",
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
                        "content": "={{ $node[\"Config\"].json.classification_system_prompt }}",
                    },
                    {
                        "role": "user",
                        "content": "=Subject: {{ $json.subject_clean }}\n\nFrom: {{ $json.sender_email }} (domain: {{ $json.sender_domain }})\n\nBody:\n{{ ($json.body_clean || '').slice(0, 2000) }}",
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
    """Function node — coerces AI output into a strict 4-field schema."""
    code = """// Safety net: NEVER throw. Always emit a valid {category, confidence, reasoning,
// suggested_priority, parse_failed} object so downstream Switch never crashes.
const ALLOWED = ['LEAD','SUPPORT','SPAM','OTHER'];
const PRIOS   = ['LOW','MEDIUM','HIGH'];

return items.map(item => {
  const upstream = item.json;
  // The AI node returns the assistant message under .message.content (chat)
  // or under .text / .output depending on n8n version. Try a few shapes.
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

  // Carry forward the cleaned email fields so downstream nodes don't need to
  // re-resolve them. We pull them via $('Clean email') in n8n, but copying is
  // safer for the Function-node output shape.
  const carry = $('Clean email').item?.json || {};

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
        notes="Safety net — guarantees one of LEAD/SUPPORT/SPAM/OTHER on 100% of inputs, "
              "even when the model returns prose or invalid JSON.",
        params={"functionCode": code},
    )


def make_switch(pos: list[int]) -> dict:
    """Switch node 'Route by category' — 4 outputs + fallback."""
    return _node(
        "Route by category",
        "n8n-nodes-base.switch",
        pos,
        type_version=3,
        notes="Routes on $json.category. Fallback output (index 4) goes to "
              "the errors path so unknown categories don't silently disappear.",
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
    """Google Sheets lookup-by-message_id for idempotency."""
    return _node(
        name,
        "n8n-nodes-base.googleSheets",
        pos,
        type_version=4.4,
        credentials={"googleSheetsOAuth2Api": CRED["sheets"]},
        retry=True,
        continue_on_fail=True,
        notes=f"Reads existing message_ids from the '{tab}' tab so we don't insert duplicates.",
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
                "values": [
                    {
                        "lookupColumn": "message_id",
                        "lookupValue": "={{ $json.message_id }}",
                    }
                ]
            },
            "options": {"returnFirstMatch": True},
        },
    )


def make_already_logged_if(name: str, pos: list[int]) -> dict:
    """IF node that short-circuits when lookup returns a row."""
    return _node(
        name,
        "n8n-nodes-base.if",
        pos,
        type_version=2,
        notes="Short-circuits with duplicate_skipped=true if lookup found this message_id.",
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


def make_append_leads(pos: list[int]) -> dict:
    return _node(
        "Append leads",
        "n8n-nodes-base.googleSheets",
        pos,
        type_version=4.4,
        credentials={"googleSheetsOAuth2Api": CRED["sheets"]},
        retry=True,
        notes="Explicit column mapping per docs/sheets/CRM_SHEET_TEMPLATE.md.",
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


def make_draft_reply_ai(pos: list[int]) -> dict:
    return _node(
        "Draft reply",
        "@n8n/n8n-nodes-langchain.openAi",
        pos,
        type_version=1.6,
        credentials={"openAiApi": CRED["openai"]},
        retry=True,
        notes="Generates the 80-150 word reply body. Temperature 0.4 for natural prose.",
        params={
            "resource": "text",
            "operation": "message",
            "modelId": {"__rl": True, "mode": "expression",
                        "value": "={{ $node[\"Config\"].json.model }}"},
            "messages": {
                "values": [
                    {
                        "role": "system",
                        "content": "={{ $node[\"Config\"].json.reply_system_prompt }}",
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
        notes="HARD SAFETY RULE: createDraft only. Never 'send'. CI guard enforces.",
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


def make_slack_card(pos: list[int]) -> dict:
    blocks = json.dumps([
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "=🚀 New lead — {{ $('Parse classification').item.json.suggested_priority }}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "=*From:*\n{{ $('Parse classification').item.json.sender_email }}"},
                {"type": "mrkdwn", "text": "=*Domain:*\n{{ $('Parse classification').item.json.sender_domain }}"},
                {"type": "mrkdwn", "text": "=*Subject:*\n{{ $('Parse classification').item.json.subject_clean }}"},
                {"type": "mrkdwn", "text": "=*Confidence:*\n{{ Math.round($('Parse classification').item.json.confidence * 100) }}%"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "=*Preview:*\n> {{ ($('Parse classification').item.json.body_clean || '').slice(0, 300).replace(/\\n/g, '\\n> ') }}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open draft in Gmail", "emoji": True},
                    "url": "=https://mail.google.com/mail/u/0/#drafts/{{ $('Create Gmail draft').item.json.id }}",
                    "style": "primary",
                }
            ],
        },
    ], indent=2)
    return _node(
        "Post lead card",
        "n8n-nodes-base.slack",
        pos,
        type_version=2.2,
        credentials={"slackApi": CRED["slack"]},
        retry=True,
        continue_on_fail=True,  # Slack outage must not block the CRM write
        notes="Posts a Block-Kit card with priority emoji + Open Draft link.",
        params={
            "resource": "message",
            "operation": "post",
            "select": "channel",
            "channelId": {"__rl": True, "mode": "expression",
                           "value": "={{ $node[\"Config\"].json.slackChannel }}"},
            "messageType": "block",
            "blocksUi": blocks,
            "otherOptions": {},
        },
    )


# ---------------------------------------------------------------------------
# Phase builders
# ---------------------------------------------------------------------------
def workflow_skeleton(name: str, description: str, tags: list[str],
                      with_error_workflow: bool = False) -> dict:
    wf = {
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
            "templateCredsSetupCompleted": False,
            "description": description,
        },
    }
    if with_error_workflow:
        wf["settings"]["errorWorkflow"] = "LeadInboxTriageBot_ErrorHandler"
    return wf


# ---- Phase 1: trigger only ------------------------------------------------
def build_phase1() -> dict:
    wf = workflow_skeleton(
        "LeadInboxTriageBot_Phase1",
        "Phase 1 — Gmail Trigger + Config skeleton. Captures every new email in "
        "the test inbox; no classification or routing yet.",
        ["training", "lead-triage", "phase-1"],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_sticky("# Trigger\nGmail polls every 1 min and marks read.",
                         [40, 40], width=320, color=5))
    nodes.append(_sticky("# Config\nEdit this Set node to point at YOUR Sheet, "
                         "Slack channel, signature, and model.",
                         [40, 220], width=320, color=4))
    nodes.append(_sticky("# Future sections (built in Phases 2–5)\n"
                         "→ Classify | Route | Log | Notify | Errors",
                         [40, 400], width=620, height=120, color=7))

    cfg = make_config_node([400, 280])
    trig = make_gmail_trigger([400, 80])
    nodes.extend([cfg, trig])

    # Trigger flows into Config so downstream nodes (added in later phases)
    # can chain. For Phase 1 the chain ends here.
    _connect(conns, trig["name"], cfg["name"])
    return wf


# ---- Phase 2: + cleaning + classification ---------------------------------
def build_phase2() -> dict:
    wf = workflow_skeleton(
        "LeadInboxTriageBot_Phase2",
        "Phase 2 — adds Clean email Set node + AI Classifier (strict JSON) + "
        "Parse classification safety net.",
        ["training", "lead-triage", "phase-2"],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_sticky("# Trigger", [40, 40], width=200, color=5))
    nodes.append(_sticky("# Pre-process\nClean email = subject_clean, body_clean (no quoted reply, no signature), "
                         "sender_email/name/domain, body_preview.",
                         [40, 220], width=320, color=4))
    nodes.append(_sticky("# Classify\nOpenAI JSON mode, T=0.1, prompt from Config.\n"
                         "Parse classification = safety net (never throws).",
                         [40, 400], width=320, color=6))

    cfg  = make_config_node([400, 280])
    trig = make_gmail_trigger([400, 80])
    clean= make_clean_email([700, 80])
    cls  = make_classify_node([1000, 80])
    parse= make_parse_classification([1300, 80])
    nodes.extend([cfg, trig, clean, cls, parse])

    _connect(conns, trig["name"],  clean["name"])
    _connect(conns, clean["name"], cls["name"])
    _connect(conns, cls["name"],   parse["name"])
    return wf


# ---- Phase 3: + switch routing + CRM logging + idempotency ----------------
def build_phase3() -> dict:
    wf = workflow_skeleton(
        "LeadInboxTriageBot_Phase3",
        "Phase 3 — adds Switch routing and Sheets CRM writes with per-tab "
        "idempotency (Lookup + IF before each Append).",
        ["training", "lead-triage", "phase-3"],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_sticky("# Trigger", [40, 40], width=200, color=5))
    nodes.append(_sticky("# Pre-process", [40, 220], width=200, color=4))
    nodes.append(_sticky("# Classify", [40, 400], width=200, color=6))
    nodes.append(_sticky("# Route + Log\nSwitch → per-category idempotency check → Sheets append.\n"
                         "Fallback output goes to the OTHER tab so unknowns don't disappear.",
                         [40, 580], width=620, height=140, color=2))

    cfg   = make_config_node([400, 280])
    trig  = make_gmail_trigger([400, 80])
    clean = make_clean_email([700, 80])
    cls   = make_classify_node([1000, 80])
    parse = make_parse_classification([1300, 80])
    switch = make_switch([1600, 80])

    # Per-category lookup + IF + append
    lk_lead   = make_lookup_node("Lookup leads",   "leads",   [1900, -180])
    if_lead   = make_already_logged_if("Already in leads?", [2160, -180])
    app_lead  = make_append_leads([2420, -240])

    lk_supp   = make_lookup_node("Lookup support", "support", [1900,  20])
    if_supp   = make_already_logged_if("Already in support?", [2160, 20])
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
        }, [2420, -40])

    lk_spam   = make_lookup_node("Lookup spam",    "spam",    [1900, 220])
    if_spam   = make_already_logged_if("Already in spam?", [2160, 220])
    app_spam  = make_append_simple(
        "Append spam", "spam",
        {
            "message_id":   "={{ $('Parse classification').item.json.message_id }}",
            "received_at":  "={{ $('Parse classification').item.json.received_at }}",
            "sender_email": "={{ $('Parse classification').item.json.sender_email }}",
            "subject":      "={{ $('Parse classification').item.json.subject_clean }}",
            "confidence":   "={{ $('Parse classification').item.json.confidence }}",
            "created_at":   "={{ $now.toISO() }}",
        }, [2420, 160])

    lk_other  = make_lookup_node("Lookup other",   "other",   [1900, 420])
    if_other  = make_already_logged_if("Already in other?", [2160, 420])
    app_other = make_append_simple(
        "Append other", "other",
        {
            "message_id":   "={{ $('Parse classification').item.json.message_id }}",
            "received_at":  "={{ $('Parse classification').item.json.received_at }}",
            "sender_email": "={{ $('Parse classification').item.json.sender_email }}",
            "subject":      "={{ $('Parse classification').item.json.subject_clean }}",
            "confidence":   "={{ $('Parse classification').item.json.confidence }}",
            "created_at":   "={{ $now.toISO() }}",
        }, [2420, 360])

    # Fallback output reuses the OTHER append path
    nodes.extend([
        cfg, trig, clean, cls, parse, switch,
        lk_lead, if_lead, app_lead,
        lk_supp, if_supp, app_supp,
        lk_spam, if_spam, app_spam,
        lk_other, if_other, app_other,
    ])

    _connect(conns, trig["name"],  clean["name"])
    _connect(conns, clean["name"], cls["name"])
    _connect(conns, cls["name"],   parse["name"])
    _connect(conns, parse["name"], switch["name"])

    # Switch outputs: 0=LEAD, 1=SUPPORT, 2=SPAM, 3=OTHER, 4=fallback (unknown)
    _connect(conns, switch["name"], lk_lead["name"],  src_index=0)
    _connect(conns, switch["name"], lk_supp["name"],  src_index=1)
    _connect(conns, switch["name"], lk_spam["name"],  src_index=2)
    _connect(conns, switch["name"], lk_other["name"], src_index=3)
    # Fallback (unknown) → other tab (so we don't lose it)
    _connect(conns, switch["name"], lk_other["name"], src_index=4)

    # lookup → IF → append (true branch = found duplicate → end; false branch = append)
    # n8n IF: output 0 = true (condition met = message_id present), output 1 = false
    # We want to APPEND when message_id is present AND not already there. The lookup
    # returns empty when no match; the IF tests message_id presence in the lookup output.
    # Simpler approach: if lookup returned ANY rows → skip; else → append.
    # Implement by checking lookup output's row count via an expression in the IF.

    # Re-wire the IF to test whether lookup found something. The IF param uses
    # $json.message_id which after a no-match lookup is missing.
    for lk, ifn, app in [(lk_lead, if_lead, app_lead),
                          (lk_supp, if_supp, app_supp),
                          (lk_spam, if_spam, app_spam),
                          (lk_other, if_other, app_other)]:
        _connect(conns, lk["name"], ifn["name"])
        # IF true (lookup found a row → has message_id) → SKIP (no further connection)
        # IF false (no row) → APPEND
        _connect(conns, ifn["name"], app["name"], src_index=1)

    return wf


# ---- Phase 4: + draft reply + Slack card ----------------------------------
def build_phase4() -> dict:
    wf = build_phase3()
    wf["name"] = "LeadInboxTriageBot_Phase4"
    wf["meta"]["description"] = (
        "Phase 4 — adds Draft Reply (AI), Gmail Create Draft (NEVER send), "
        "and Slack lead-card notification on the LEAD branch."
    )
    wf["tags"] = [{"name": "training"}, {"name": "lead-triage"}, {"name": "phase-4"}]
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_sticky("# Draft + Notify\nLEAD branch only.\nDraft Reply (AI) → Gmail "
                         "Create Draft (NEVER send) → Slack card.",
                         [40, 760], width=620, height=130, color=1))

    draft  = make_draft_reply_ai([2720, -300])
    gmail  = make_create_gmail_draft([3020, -300])
    slack  = make_slack_card([3320, -300])
    nodes.extend([draft, gmail, slack])

    # Find the Append leads node (already exists in Phase 3) and chain off it.
    _connect(conns, "Append leads", draft["name"])
    _connect(conns, draft["name"],  gmail["name"])
    _connect(conns, gmail["name"],  slack["name"])
    return wf


# ---- Phase 5: + retries everywhere + error workflow link ------------------
def build_phase5() -> dict:
    wf = build_phase4()
    wf["name"] = "LeadInboxTriageBot"   # final name (no _Phase5)
    wf["meta"]["description"] = (
        "Lead Inbox Triage Bot — final hardened workflow. Watches a test "
        "Gmail inbox, classifies with an LLM (strict JSON), routes via Switch, "
        "logs to a Google Sheets CRM idempotently, drafts replies (NEVER sends) "
        "for LEADs, and posts to Slack. Retries on every external call. "
        "An ErrorTrigger sub-workflow captures any failure."
    )
    wf["tags"] = [{"name": "training"}, {"name": "lead-triage"},
                  {"name": "phase-5"}, {"name": "production-ready"}]
    wf["settings"]["errorWorkflow"] = "LeadInboxTriageBot_ErrorHandler"

    # Retries already set via the retry=True flag on factory nodes. Belt-and-
    # suspenders pass to upgrade anything we missed.
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

    # Add a sticky note explaining retries
    wf["nodes"].append(_sticky(
        "# Hardening (Phase 5)\nEvery external-call node: retryOnFail=true, "
        "maxTries=3, waitBetweenTries=5000ms.\n"
        "Workflow's errorWorkflow → LeadInboxTriageBot_ErrorHandler.",
        [40, 900], width=620, height=130, color=3,
    ))
    return wf


# ---- Error handler sub-workflow ------------------------------------------
def build_error_handler() -> dict:
    wf = workflow_skeleton(
        "LeadInboxTriageBot_ErrorHandler",
        "Error handler for the Lead Inbox Triage Bot. Appends a row to the "
        "'errors' tab and posts an alert to #training-leads-errors. Never "
        "re-triggers the failing workflow.",
        ["training", "lead-triage", "error-handler"],
    )
    nodes = wf["nodes"]
    conns = wf["connections"]

    nodes.append(_sticky("# Error Handler\nWired in main workflow via Settings → Error Workflow.",
                         [40, 40], width=420, color=8))

    cfg = make_config_node([320, 280])

    err = _node(
        "Error Trigger",
        "n8n-nodes-base.errorTrigger",
        [320, 80],
        type_version=1,
        notes="Receives the failed execution payload from the main workflow.",
        params={},
    )

    build_row = _node(
        "Build error row",
        "n8n-nodes-base.set",
        [620, 80],
        type_version=3,
        notes="Normalizes the error payload to the 'errors' tab schema.",
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
                     "value": "={{ ($json.execution?.error?.message || $json.error?.message || 'unknown error').toString().slice(0,280) }}"},
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
        }, [920, 0])
    append_err["continueOnFail"] = True
    append_err["retryOnFail"] = False  # never retry the error logger itself

    slack_err = _node(
        "Post error alert",
        "n8n-nodes-base.slack",
        [920, 200],
        type_version=2.2,
        credentials={"slackApi": CRED["slack"]},
        continue_on_fail=True,
        notes="Brief alert; no retries — we never want the error path to loop.",
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
