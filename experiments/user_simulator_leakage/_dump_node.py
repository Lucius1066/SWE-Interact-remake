"""Volunteer helper: dump real session text needed to curate a task snapshot.

Prints the frozen prefix, the decision-node user message, the next user message
(the real Future A), and every later user message, so a task file can be written
with exact message indices and verbatim text.

Usage: uv run python experiments/user_simulator_leakage/_dump_node.py TASK INDEX
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "tasks"


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"<tool_use {block.get('name')}>")
                elif block.get("type") == "tool_result":
                    parts.append("<tool_result>")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def clean(content) -> str:
    raw = text_of(content)
    raw = re.sub(r"<system-reminder>.*?</system-reminder>", "", raw, flags=re.DOTALL)
    return re.sub(r"\s+", " ", raw).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("index", type=int)
    parser.add_argument("--budget", type=int, default=20000)
    args = parser.parse_args()

    payload = json.loads((TASKS_DIR / args.task / "original_session.json").read_text(encoding="utf-8"))
    messages = payload["messages"]
    print(f"TASK {args.task} session={payload.get('session_id')} total={len(messages)}")

    print("\n### DECISION NODE user message")
    print(json.dumps(clean(messages[args.index].get("content")), ensure_ascii=False))

    print("\n### LATER USER MESSAGES")
    for index in range(args.index + 1, len(messages)):
        if messages[index].get("role") != "user":
            continue
        body = clean(messages[index].get("content"))
        if not body or body.startswith("<tool_result"):
            continue
        print(f"[{index}] {body[:900]}")
        if index > args.index + 40:
            break

    print("\n### FROZEN PREFIX ASSISTANT MESSAGES (labels only)")
    for index in range(args.index):
        if messages[index].get("role") != "assistant":
            continue
        body = clean(messages[index].get("content"))
        if body:
            print(f"[{index}] {body[:900]}")

    print("\n### ORACLE TURNS")
    oracle = TASKS_DIR / args.task / "oracle_session.jsonl"
    if oracle.exists():
        for line in oracle.read_text(encoding="utf-8").splitlines()[1:]:
            turn = json.loads(line)
            lo, hi = turn["msg_range"]
            flag = " <== node" if lo <= args.index < hi else ""
            print(
                f"turn={turn['turn']} range=[{lo},{hi}) completion={turn['completion_signaled']} "
                f"files={turn.get('files_edited') or turn.get('files_written')}{flag}"
            )
            print(f"   user: {clean(turn.get('user_message'))[:400]}")
            print(f"   agent: {clean(turn.get('agent_text'))[:600]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
