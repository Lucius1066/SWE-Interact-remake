"""Volunteer helper: dry-run the rule classifier over every task's mock decisions.

Mirrors the tolerance the focused test uses: the mock is a harness check, not a
model-quality check, so Future B may legitimately contain a no-op sample and
Blind may contain an occasional directional sample.

Usage: uv run python experiments/user_simulator_leakage/_check_mock_labels.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run import CONDITIONS, classify_decision, load_tasks  # noqa: E402
from user_agent.user_agent import UserDecision  # noqa: E402


def main() -> int:
    problems = 0
    for task in load_tasks():
        print(f"\n=== {task['task_id']}")
        per_condition: dict[str, Counter] = {}
        for condition in CONDITIONS:
            labels: Counter = Counter()
            for index, mock in enumerate(task["mock_decisions"][condition], start=1):
                decision = UserDecision(
                    action=mock["action"], content=mock.get("content", "")
                )
                report = classify_decision(decision, task["classifier"])
                labels[report["label"]] += 1
                print(
                    f"  {condition}#{index} action={mock['action']} -> "
                    f"{report['label']}/{report['direction']} "
                    f"a={report['a_score']} b={report['b_score']}"
                    + ("" if report["message_status"] == "speak" else " (no-op)")
                )
            per_condition[condition] = labels

        blind = per_condition["blind"]
        future_a = per_condition["future_a"]
        future_b = per_condition["future_b"]
        checks = [
            ("future_a all A", future_a.get("A", 0) == sum(future_a.values()) and future_a),
            ("future_b mostly B", future_b.get("B", 0) >= sum(future_b.values()) - 1),
            ("blind mostly Other", blind.get("Other", 0) >= sum(blind.values()) - 1),
        ]
        for name, ok in checks:
            if not ok:
                problems += 1
                print(f"  FAIL: {name} -> blind={dict(blind)} a={dict(future_a)} b={dict(future_b)}")
    print(f"\nproblems={problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
