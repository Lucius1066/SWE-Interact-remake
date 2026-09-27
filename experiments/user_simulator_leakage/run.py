"""Run frozen-context counterfactual probes against the SWE-Together UserAgent.

Each sample constructs a fresh UserAgent.  The public task, conversation prefix,
agent state, step number, and completion flag are frozen.  The only prompt field
that varies is the private future-knowledge block (blind, future_a, future_b).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
HARBOR_SRC = REPO_ROOT / "external" / "harbor" / "src"
for import_root in (SRC_ROOT, HARBOR_SRC):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from user_agent.user_agent import ACTIONS, UserAgent, UserDecision, UserPersona


CONDITIONS = ("blind", "future_a", "future_b")
TASKS_DIR = Path(__file__).with_name("tasks")
FUTURE_START = "<!-- FUTURE_KNOWLEDGE_START -->"
FUTURE_END = "<!-- FUTURE_KNOWLEDGE_END -->"


@dataclass(frozen=True)
class ExperimentInput:
    task_description: str
    recent_trajectory: str
    latest_observation: str
    session_analysis: str
    step_count: int
    is_completion_attempt: bool
    elapsed_sec: float
    turn_duration_sec: float
    code_changes_diff: str
    snapshot_sha256: str
    visible_future: str | None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _history_text(history: list[dict[str, str]]) -> str:
    if not history:
        return "(no earlier dialogue retained)"
    return "\n\n".join(
        f"{item['role'].upper()}: {item['content']}" for item in history
    )


def _visible_future(task: dict[str, Any], condition: str) -> str | None:
    if condition == "blind":
        return None
    return task["futures"][condition]["text"]


def build_experiment_input(task: dict[str, Any], condition: str) -> ExperimentInput:
    """Build the exact frozen inputs for one condition.

    Only ``futures[condition].text`` is read for a non-blind condition.  Future
    provenance, classifier rules, mock answers, and the unselected future are
    deliberately excluded from all model-visible fields.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition: {condition}")

    snapshot = task["snapshot"]
    snapshot_sha = _sha256_text(_canonical_json(snapshot))
    history = _history_text(snapshot["history"])
    task_description = (
        f"## Public request\n{snapshot['public_request']}\n\n"
        f"## Historical interaction (ends at the frozen point)\n{history}"
    )

    future = _visible_future(task, condition)
    if future is None:
        future_block = (
            "No information about requirements after the frozen point is "
            "available to you. Do not invent a future plan."
        )
    else:
        future_block = (
            "You privately know that the user will later require the following. "
            "Use it only to decide what feedback is useful now; do not mention "
            "that you know the future and do not quote it as a prophecy.\n\n"
            f"{future}"
        )

    session_analysis = (
        "## Frozen-context experiment\n"
        "Make exactly one user-simulator decision at the frozen decision point. "
        "The coding-agent state will not advance between samples. The public "
        "request and history below are complete up to this point. Choose no-op "
        "when no intervention is warranted; otherwise send the shortest feedback "
        "that would steer the current decision.\n\n"
        f"{FUTURE_START}\n{future_block}\n{FUTURE_END}"
    )

    return ExperimentInput(
        task_description=task_description,
        recent_trajectory=snapshot["agent_state"],
        latest_observation=snapshot["agent_last_message"],
        session_analysis=session_analysis,
        step_count=int(snapshot.get("step_count", 1)),
        is_completion_attempt=bool(snapshot.get("is_completion_attempt", False)),
        elapsed_sec=float(snapshot.get("elapsed_sec", 0)),
        turn_duration_sec=float(snapshot.get("turn_duration_sec", 0)),
        code_changes_diff=snapshot.get("code_changes_diff", ""),
        snapshot_sha256=snapshot_sha,
        visible_future=future,
    )


def validate_task(task: dict[str, Any], source: Path | None = None) -> None:
    where = f" in {source}" if source else ""
    required = {"schema_version", "task_id", "source", "snapshot", "futures", "classifier", "mock_decisions"}
    missing = sorted(required - set(task))
    if missing:
        raise ValueError(f"Missing keys{where}: {', '.join(missing)}")
    if task["schema_version"] != 1:
        raise ValueError(f"Unsupported schema_version{where}: {task['schema_version']}")

    snapshot = task["snapshot"]
    snapshot_required = {"public_request", "history", "agent_state", "agent_last_message"}
    missing_snapshot = sorted(snapshot_required - set(snapshot))
    if missing_snapshot:
        raise ValueError(f"Missing snapshot keys{where}: {', '.join(missing_snapshot)}")
    if not isinstance(snapshot["history"], list):
        raise ValueError(f"snapshot.history must be a list{where}")

    snapshot_text = _canonical_json(snapshot).casefold()
    future_texts: list[str] = []
    for condition in ("future_a", "future_b"):
        future = task["futures"].get(condition, {})
        if not future.get("text") or future.get("provenance") not in {"real", "synthetic"}:
            raise ValueError(f"Invalid {condition}{where}")
        future_text = future["text"].strip()
        future_texts.append(future_text)
        if future_text.casefold() in snapshot_text:
            raise ValueError(f"{condition} appears verbatim in frozen snapshot{where}")
    if future_texts[0].casefold() == future_texts[1].casefold():
        raise ValueError(f"Future A and B must differ{where}")

    for label in ("a", "b"):
        patterns = task["classifier"].get(label, [])
        if not patterns:
            raise ValueError(f"classifier.{label} needs at least one regex{where}")
        for pattern in patterns:
            re.compile(pattern, flags=re.IGNORECASE | re.DOTALL)

    for condition in CONDITIONS:
        decisions = task["mock_decisions"].get(condition)
        if not isinstance(decisions, list) or not decisions:
            raise ValueError(f"mock_decisions.{condition} must be non-empty{where}")
        for decision in decisions:
            if decision.get("action") not in {name for name, _, _ in ACTIONS}:
                raise ValueError(f"Invalid mock action for {condition}{where}")


def load_tasks(task_ids: list[str] | None = None, tasks_dir: Path = TASKS_DIR) -> list[dict[str, Any]]:
    selected = set(task_ids or [])
    loaded: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        validate_task(task, path)
        if not selected or task["task_id"] in selected:
            task["_config_path"] = str(path.relative_to(REPO_ROOT))
            loaded.append(task)
    found = {task["task_id"] for task in loaded}
    unknown = selected - found
    if unknown:
        raise ValueError(f"Unknown task id(s): {', '.join(sorted(unknown))}")
    if not loaded:
        raise ValueError(f"No experiment tasks found in {tasks_dir}")
    return loaded


def classify_decision(decision: UserDecision, rules: dict[str, list[str]]) -> dict[str, Any]:
    """Rule-classify the decision as A, B, or Other while retaining evidence."""
    if not decision.has_message:
        return {"label": "Other", "a_hits": [], "b_hits": [], "reason": "no-op"}

    text = f"{decision.action}\n{decision.content}"
    hits: dict[str, list[str]] = {}
    for label in ("a", "b"):
        hits[label] = [
            pattern
            for pattern in rules[label]
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        ]
    a_score, b_score = len(hits["a"]), len(hits["b"])
    if a_score > b_score:
        label, reason = "A", "more A regex matches"
    elif b_score > a_score:
        label, reason = "B", "more B regex matches"
    else:
        label, reason = "Other", "no matches or tied evidence"
    return {"label": label, "a_hits": hits["a"], "b_hits": hits["b"], "reason": reason}


class MockLLM:
    """Prompt-checking mock with OpenAI-style tool calls.

    The mock first asserts that exactly the selected future is visible, then
    returns the configured decision for the sample.  This makes mock runs test
    condition switching as well as parsing and persistence.
    """

    def __init__(self, task: dict[str, Any], condition: str, sample_index: int):
        self.task = task
        self.condition = condition
        self.sample_index = sample_index
        self.validated = False

    async def call(self, *, prompt: str, message_history: list[dict[str, str]], **_: Any):
        visible = "\n".join(item["content"] for item in message_history) + "\n" + prompt
        future_a = self.task["futures"]["future_a"]["text"]
        future_b = self.task["futures"]["future_b"]["text"]
        if self.condition == "blind":
            if future_a in visible or future_b in visible:
                raise AssertionError("Blind prompt leaked a future requirement")
        elif self.condition == "future_a":
            if future_a not in visible or future_b in visible:
                raise AssertionError("Future A prompt has wrong future visibility")
        elif self.condition == "future_b":
            if future_b not in visible or future_a in visible:
                raise AssertionError("Future B prompt has wrong future visibility")
        self.validated = True

        options = self.task["mock_decisions"][self.condition]
        selected = options[self.sample_index % len(options)]
        action = selected["action"]
        content = selected.get("content", "")
        args = {} if action == "no-op" else {"content": content}
        function = SimpleNamespace(name=action, arguments=json.dumps(args, ensure_ascii=False))
        tool_call = SimpleNamespace(function=function)
        raw = selected.get("raw_response") or _canonical_json(
            {"mock": True, "action": action, "content": content}
        )
        return SimpleNamespace(content=raw, tool_calls=[tool_call])


def _make_real_llm(model: str, temperature: float, api_base: str | None):
    from harbor.llms.lite_llm import LiteLLM

    return LiteLLM(model_name=model, temperature=temperature, api_base=api_base)


async def run_experiment(
    tasks: list[dict[str, Any]],
    conditions: list[str],
    samples: int,
    output_dir: Path,
    *,
    mock: bool,
    model: str | None,
    temperature: float = 0.8,
    api_base: str | None = None,
) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be >= 1")
    bad_conditions = sorted(set(conditions) - set(CONDITIONS))
    if bad_conditions:
        raise ValueError(f"Unknown condition(s): {', '.join(bad_conditions)}")
    if not mock and not model:
        raise ValueError("--model is required unless --mock is used")

    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "decisions.jsonl"
    records: list[dict[str, Any]] = []

    for task in tasks:
        persona_cfg = task.get("persona", {})
        expected_snapshot_sha: str | None = None
        for condition in conditions:
            experiment_input = build_experiment_input(task, condition)
            if expected_snapshot_sha is None:
                expected_snapshot_sha = experiment_input.snapshot_sha256
            elif experiment_input.snapshot_sha256 != expected_snapshot_sha:
                raise AssertionError("Frozen snapshot changed across conditions")

            for sample_index in range(samples):
                # A new simulator (and therefore empty simulator history) is
                # created for every sample: no sample can influence another.
                llm = (
                    MockLLM(task, condition, sample_index)
                    if mock
                    else _make_real_llm(model or "", temperature, api_base)
                )
                simulator = UserAgent(
                    llm=llm,
                    original_user_messages=[],
                    persona=UserPersona(
                        tone=persona_cfg.get("tone", "direct"),
                        verbosity=persona_cfg.get("verbosity", "terse"),
                    ),
                    session_analysis=experiment_input.session_analysis,
                    max_messages=1,
                )
                decision = await simulator.process(
                    task_description=experiment_input.task_description,
                    recent_trajectory=experiment_input.recent_trajectory,
                    latest_observation=experiment_input.latest_observation,
                    latest_analysis=None,
                    step_count=experiment_input.step_count,
                    is_completion_attempt=experiment_input.is_completion_attempt,
                    total_steps_so_far=experiment_input.step_count,
                    elapsed_sec=experiment_input.elapsed_sec,
                    turn_duration_sec=experiment_input.turn_duration_sec,
                    code_changes_diff=experiment_input.code_changes_diff,
                )
                # UserAgent deliberately converts provider exceptions into a
                # no-op. Do not let that resilience hide a failed mock leakage
                # assertion from this experiment.
                if isinstance(llm, MockLLM) and not llm.validated:
                    raise AssertionError("Mock prompt validation did not complete")
                classification = classify_decision(decision, task["classifier"])
                prompt_messages = simulator.last_messages_sent
                if [message["role"] for message in prompt_messages] != ["system", "user"]:
                    raise AssertionError("Sample is not independent: unexpected simulator history")

                record = {
                    "schema_version": 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_id": task["task_id"],
                    "source_task": task["source"]["task"],
                    "condition": condition,
                    "sample_index": sample_index,
                    "backend": "mock" if mock else "litellm",
                    "model": "mock" if mock else model,
                    "snapshot_sha256": experiment_input.snapshot_sha256,
                    "visible_future_sha256": (
                        _sha256_text(experiment_input.visible_future)
                        if experiment_input.visible_future is not None
                        else None
                    ),
                    "prompt_sha256": _sha256_text(_canonical_json(prompt_messages)),
                    "prompt_messages": prompt_messages,
                    "decision": {
                        "action": decision.action,
                        "has_message": decision.has_message,
                        "content": decision.content,
                        "raw_response": decision.raw_response,
                    },
                    "classification": classification,
                }
                records.append(record)

    with records_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    distributions: dict[str, dict[str, dict[str, int]]] = {}
    for task in tasks:
        task_id = task["task_id"]
        distributions[task_id] = {}
        for condition in conditions:
            labels = [
                record["classification"]["label"]
                for record in records
                if record["task_id"] == task_id and record["condition"] == condition
            ]
            counts = Counter(labels)
            distributions[task_id][condition] = {
                label: counts.get(label, 0) for label in ("A", "B", "Other")
            }

    summary = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backend": "mock" if mock else "litellm",
        "model": "mock" if mock else model,
        "temperature": None if mock else temperature,
        "samples_per_condition": samples,
        "conditions": conditions,
        "task_ids": [task["task_id"] for task in tasks],
        "record_count": len(records),
        "records_file": records_path.name,
        "distributions": distributions,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", dest="tasks", help="Task id; repeat to select multiple (default: all)")
    parser.add_argument("--conditions", default=",".join(CONDITIONS), help="Comma-separated conditions")
    parser.add_argument("--samples", type=int, default=10, help="Independent samples per task/condition")
    parser.add_argument("--mock", action="store_true", help="Use the prompt-checking mock; no API key needed")
    parser.add_argument("--model", help="LiteLLM model name for real calls")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--list-tasks", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tasks = load_tasks(args.tasks)
    if args.list_tasks:
        for task in tasks:
            print(f"{task['task_id']}\t{task['source']['task']}")
        return 0

    conditions = [item.strip() for item in args.conditions.split(",") if item.strip()]
    if args.output is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.output = Path(__file__).with_name("results") / stamp
    summary = asyncio.run(
        run_experiment(
            tasks,
            conditions,
            args.samples,
            args.output.resolve(),
            mock=args.mock,
            model=args.model,
            temperature=args.temperature,
            api_base=args.api_base,
        )
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {summary['record_count']} decisions to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
