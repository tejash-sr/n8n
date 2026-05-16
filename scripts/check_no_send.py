#!/usr/bin/env python3
"""Hard guard — fail CI if any Gmail node has the 'send' operation.

The PDF's hard safety rule (§Phase 4) forbids the workflow from ever sending
email automatically. This script crawls every workflow JSON and asserts:

  - No `n8n-nodes-base.gmail` node has `parameters.operation == "send"`
  - No `n8n-nodes-base.gmail` node has `parameters.operation == "sendAndWait"`
  - No `n8n-nodes-base.emailSend` (SMTP) node exists anywhere
  - No `n8n-nodes-base.gmailTool` node has a send operation either
  - No `n8n-nodes-base.microsoftOutlook` node has a send operation

Run before every commit. Exit code 0 = safe; non-zero = unsafe.

    python3 scripts/check_no_send.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "workflows"

FORBIDDEN_OPS = {"send", "sendAndWait", "sendMessage", "reply"}
FORBIDDEN_TYPES = {
    "n8n-nodes-base.emailSend",       # generic SMTP send
    "n8n-nodes-base.microsoftOutlook",  # block all Outlook (it has send operations)
}


def check(path: Path) -> list[str]:
    out: list[str] = []
    data = json.loads(path.read_text())
    for n in data.get("nodes", []):
        ntype = n.get("type", "")
        params = n.get("parameters", {}) or {}
        op = params.get("operation", "")

        if ntype in FORBIDDEN_TYPES:
            out.append(
                f"{path.name}: forbidden node type '{ntype}' (name='{n.get('name')}') — "
                "the workflow must never send outbound email."
            )
            continue

        # Gmail (regular + tool variant used by AI Agent nodes)
        if ntype.startswith("n8n-nodes-base.gmail"):
            if op in FORBIDDEN_OPS:
                out.append(
                    f"{path.name}: Gmail node '{n.get('name')}' has forbidden "
                    f"operation '{op}'. Only 'createDraft' is allowed."
                )

    return out


def main() -> int:
    if not WORKFLOW_DIR.exists():
        # No workflows yet (Phase 0). Pretend OK so commits aren't blocked
        # before Phase 1 lands.
        print("OK: no workflows directory yet (Phase 0). Nothing to check.")
        return 0

    files = sorted(WORKFLOW_DIR.glob("*.json"))
    if not files:
        print("OK: no workflow files yet. Nothing to check.")
        return 0

    total: list[str] = []
    for f in files:
        problems = check(f)
        if problems:
            total.extend(problems)
            print(f"UNSAFE {f.name}")
            for p in problems:
                print(f"   - {p}")
        else:
            print(f"OK     {f.name}")

    if total:
        print(
            f"\n{len(total)} forbidden send operation(s) found. "
            "This is a HARD STOP per the exercise PDF §Phase 4.",
            file=sys.stderr,
        )
        return 1

    print("\nOK: no send operations found. Workflow is draft-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
