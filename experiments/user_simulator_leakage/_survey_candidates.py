"""Volunteer helper: dump real user turns per task so decision nodes can be surveyed.

This is a scratch tool for task construction, not part of the experiment runner.
Usage: uv run python experiments/user_simulator_leakage/_survey_candidates.py [--grep PATTERN]
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

SKIP_PATTERNS = re.compile(
    r"^(yes|yeah|yep|yup|ok|okay|sure|continue|go ahead|do it|proceed|"
    r"what'?s next\??|any update\??|status\??|thanks?|ty|great|perfect|lgtm|"
    r"nice|cool|good|hmm+)[.!]?$",
    re.IGNORECASE,
)


def load_messages(task_dir: Path) -> tuple[str, list[dict]]:
    payload = json.loads((task_dir / "original_session.json").read_text(encoding="utf-8"))
    return payload.get("session_id", ""), payload.get("messages", [])


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grep", default=None)
    parser.add_argument("--task", default=None)
    parser.add_argument("--min-chars", type=int, default=60)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--format", choices=("text", "tsv"), default="text")
    args = parser.parse_args()

    for task_dir in sorted(p for p in TASKS_DIR.iterdir() if p.is_dir()):
        if args.task and task_dir.name != args.task:
            continue
        session_path = task_dir / "original_session.json"
        if not session_path.exists():
            continue
        session_id, messages = load_messages(task_dir)
        if args.format == "text":
            print(f"\n===== {task_dir.name}  session={session_id}  msgs={len(messages)}")
        for index, message in enumerate(messages):
            if message.get("role") != "user":
                continue
            content = text_of(message.get("content")).strip()
            if "<local-command" in content or "<command-name>" in content:
                continue
            if "system-reminder" in content:
                content = re.sub(
                    r"<system-reminder>.*?</system-reminder>", "", content, flags=re.DOTALL
                ).strip()
            if not content or SKIP_PATTERNS.match(content):
                continue
            if not (args.min_chars <= len(content) <= args.max_chars):
                continue
            if args.grep and not re.search(args.grep, content, re.IGNORECASE):
                continue
            flat = re.sub(r"\s+", " ", content)
            if args.format == "tsv":
                print(f"{task_dir.name}\t{index}\t{flat}")
            else:
                print(f"  [{index}] {flat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
