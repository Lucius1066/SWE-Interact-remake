"""Volunteer helper: print the exact user-message text at session indices as JSON.

Usage: uv run python experiments/user_simulator_leakage/_node_text.py TASK INDEX [INDEX ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def main() -> int:
    task = sys.argv[1]
    messages = json.loads(
        (REPO_ROOT / "tasks" / task / "original_session.json").read_text(encoding="utf-8")
    )["messages"]
    for raw_index in sys.argv[2:]:
        index = int(raw_index)
        print(f"[{index}] role={messages[index].get('role')}")
        print(json.dumps(text_of(messages[index].get("content")), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
