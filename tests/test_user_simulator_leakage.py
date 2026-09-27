import asyncio
import json
import re

from experiments.user_simulator_leakage.run import (
    CONDITIONS,
    FUTURE_END,
    FUTURE_START,
    build_experiment_input,
    classify_decision,
    load_tasks,
    run_experiment,
)
from user_agent.user_agent import UserDecision


def _without_future_block(text: str) -> str:
    return re.sub(
        re.escape(FUTURE_START) + r".*?" + re.escape(FUTURE_END),
        "<future-block>",
        text,
        flags=re.DOTALL,
    )


def test_three_tasks_load_and_mark_real_vs_synthetic_futures():
    tasks = load_tasks()
    assert {task["task_id"] for task in tasks} == {
        "openclaw-security-reviewer",
        "pi-mono-extension-to-core",
        "entire-protected-dirs",
    }
    for task in tasks:
        assert task["futures"]["future_a"]["provenance"] == "real"
        assert task["futures"]["future_b"]["provenance"] == "synthetic"


def test_only_future_block_changes_across_conditions():
    for task in load_tasks():
        inputs = {condition: build_experiment_input(task, condition) for condition in CONDITIONS}
        assert len({item.snapshot_sha256 for item in inputs.values()}) == 1
        assert len({item.task_description for item in inputs.values()}) == 1
        assert len({item.recent_trajectory for item in inputs.values()}) == 1
        assert len({item.latest_observation for item in inputs.values()}) == 1
        assert len({_without_future_block(item.session_analysis) for item in inputs.values()}) == 1

        blind = inputs["blind"].session_analysis
        future_a = task["futures"]["future_a"]["text"]
        future_b = task["futures"]["future_b"]["text"]
        assert future_a not in blind
        assert future_b not in blind
        assert future_a in inputs["future_a"].session_analysis
        assert future_b not in inputs["future_a"].session_analysis
        assert future_b in inputs["future_b"].session_analysis
        assert future_a not in inputs["future_b"].session_analysis


def test_rule_classifier_keeps_ambiguous_output_as_other():
    rules = {"a": ["core"], "b": ["extension"]}
    assert classify_decision(UserDecision("new_requirement", "move to core"), rules)["label"] == "A"
    assert classify_decision(UserDecision("redirect", "keep the extension"), rules)["label"] == "B"
    assert classify_decision(UserDecision("question", "core or extension?"), rules)["label"] == "Other"
    assert classify_decision(UserDecision("no-op"), rules)["label"] == "Other"


def test_mock_run_validates_switching_independence_and_recording(tmp_path):
    tasks = load_tasks()
    summary = asyncio.run(
        run_experiment(
            tasks,
            list(CONDITIONS),
            3,
            tmp_path,
            mock=True,
            model=None,
        )
    )

    assert summary["record_count"] == 27
    records = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 27
    assert (tmp_path / "summary.json").exists()

    for record in records:
        assert [message["role"] for message in record["prompt_messages"]] == ["system", "user"]
        assert record["decision"]["raw_response"]
        assert len(record["snapshot_sha256"]) == 64
        assert len(record["prompt_sha256"]) == 64

    for task in tasks:
        task_records = [record for record in records if record["task_id"] == task["task_id"]]
        assert len({record["snapshot_sha256"] for record in task_records}) == 1
        first_by_condition = {
            condition: next(
                record
                for record in task_records
                if record["condition"] == condition and record["sample_index"] == 0
            )
            for condition in CONDITIONS
        }
        assert len(
            {
                _without_future_block(record["prompt_messages"][0]["content"])
                for record in first_by_condition.values()
            }
        ) == 1
        assert len(
            {record["prompt_messages"][1]["content"] for record in first_by_condition.values()}
        ) == 1
        a_labels = {
            record["classification"]["label"]
            for record in task_records
            if record["condition"] == "future_a"
        }
        b_labels = {
            record["classification"]["label"]
            for record in task_records
            if record["condition"] == "future_b"
        }
        assert a_labels == {"A"}
        assert "A" not in b_labels
        assert "B" in b_labels
