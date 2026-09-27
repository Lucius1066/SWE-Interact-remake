"""Volunteer helper: print a window of a real session around a message index.

Usage: uv run python experiments/user_simulator_leakage/_peek_node.py TASK INDEX [--before 6] [--after 2]
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
                    parts.append(f"<tool_use {block.get('name')}> {json.dumps(block.get('input'))[:300]}")
                elif block.get("type") == "tool_result":
                    parts.append(f"<tool_result> {text_of(block.get('content'))[:300]}")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("index", type=int)
    parser.add_argument("--before", type=int, default=8)
    parser.add_argument("--after", type=int, default=3)
    parser.add_argument("--chars", type=int, default=1800)
    args = parser.parse_args()

    payload = json.loads((TASKS_DIR / args.task / "original_session.json").read_text(encoding="utf-8"))
    messages = payload["messages"]
    print(f"# {args.task} session={payload.get('session_id')} total={len(messages)}")
    for index in range(max(0, args.index - args.before), min(len(messages), args.index + args.after + 1)):
        message = messages[index]
        role = message.get("role")
        body = re.sub(r"\s+", " ", text_of(message.get("content"))).strip()
        marker = ">>>" if index == args.index else "   "
        print(f"{marker} [{index}] {role}: {body[:args.chars]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
