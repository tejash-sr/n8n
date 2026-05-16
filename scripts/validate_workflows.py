#!/usr/bin/env python3
"""Validate every workflow JSON in ./workflows/.

Checks (all must pass):
  1. File parses as JSON.
  2. Top-level keys include "name", "nodes", "connections".
  3. Every node has a non-default "name" (e.g. not "OpenAI", "Set1", "If1").
  4. At least one sticky note exists (Phase 1 onwards — section labelling).
  5. No raw OpenAI key / Slack bot token / Google client secret is committed
     (heuristic: regex for sk-, xoxb-, GOCSPX-).
  6. Every credentials reference is by {id, name} only — never includes a
     "value" or "data" field that could be a leaked secret.

Exit code 0 = all good. Non-zero = some workflow failed; prints details.

Usage:
    python3 scripts/validate_workflows.py
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "workflows"

DEFAULT_NAME_PATTERN = re.compile(
    r"^(OpenAI|Set|If|Switch|Function|Gmail|Slack|Google Sheets|"
    r"Merge|Wait|HTTP Request|Webhook|Cron|Schedule)\s*\d*$"
)

SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),         # OpenAI / Anthropic
    re.compile(r"\bxoxb-[0-9A-Za-z-]{20,}"),       # Slack bot token
    re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}"),    # Google OAuth client secret
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),        # Google API key
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
]


def check_workflow(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text()

    # 5. Secret scan on the raw text (before JSON parse)
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            errors.append(f"{path.name}: looks like a committed secret matching {pat.pattern!r}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: invalid JSON — {exc}")
        return errors

    # 2. Required keys
    for key in ("name", "nodes", "connections"):
        if key not in data:
            errors.append(f"{path.name}: missing top-level key '{key}'")

    nodes = data.get("nodes", [])
    if not nodes:
        errors.append(f"{path.name}: zero nodes")
        return errors

    # 3. Descriptive names
    for n in nodes:
        if n.get("type") == "n8n-nodes-base.stickyNote":
            continue
        name = n.get("name", "")
        if not name:
            errors.append(f"{path.name}: node has empty name (id={n.get('id')})")
        elif DEFAULT_NAME_PATTERN.match(name):
            errors.append(
                f"{path.name}: node '{name}' looks like the default n8n name — "
                "rename to human-readable form (verb + object)."
            )

    # 4. At least one sticky note section label
    sticky = [n for n in nodes if n.get("type") == "n8n-nodes-base.stickyNote"]
    if not sticky:
        errors.append(
            f"{path.name}: no sticky notes found — add section labels per "
            "docs/ARCHITECTURE.md §2."
        )

    # 6. Credentials reference must be id+name only, no values
    for n in nodes:
        creds = n.get("credentials", {})
        for cred_type, body in creds.items():
            if not isinstance(body, dict):
                errors.append(f"{path.name}: credential {cred_type} is not an object")
                continue
            extra_keys = set(body) - {"id", "name"}
            if extra_keys:
                errors.append(
                    f"{path.name}: credential {cred_type} has unexpected keys "
                    f"{extra_keys} (only id+name allowed; do NOT commit values)."
                )

    return errors


def main() -> int:
    if not WORKFLOW_DIR.exists():
        print(f"!! workflows/ directory not found at {WORKFLOW_DIR}", file=sys.stderr)
        return 2

    json_files = sorted(WORKFLOW_DIR.glob("*.json"))
    if not json_files:
        print("!! no .json files in workflows/", file=sys.stderr)
        return 2

    total_errors: list[str] = []
    for jf in json_files:
        errs = check_workflow(jf)
        if errs:
            total_errors.extend(errs)
            print(f"FAIL {jf.name}")
            for e in errs:
                print(f"   - {e}")
        else:
            print(f"PASS {jf.name}")

    if total_errors:
        print(f"\n{len(total_errors)} problem(s). See above.", file=sys.stderr)
        return 1
    print("\nOK: all workflow JSONs valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
