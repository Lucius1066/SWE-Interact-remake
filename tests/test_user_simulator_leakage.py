"""Focused tests for the frozen-context future-leakage experiment.

The tests cover the harness, not model behaviour: nine real-session tasks, the
only-the-future-block-changes freeze, raw tool-call capture, the speak/no-op vs
direction split, negation/tie evidence, and independent sampling.
"""
import asyncio
import json
import re
from pathlib import Path

from experiments.user_simulator_leakage.run import (
    CONDITIONS,
    DIRECTIONS,
    FUTURE_END,
    FUTURE_START,
    MESSAGE_STATUSES,
    build_experiment_input,
    capture_raw_model_output,
    classify_decision,
    classify_message,
    legacy_raw_response,
    load_tasks,
    reclassify_records,
    _real_llm_kwargs,
    run_experiment,
)
from user_agent.user_agent import UserDecision

EXPECTED_TASK_COUNT = 9
SAMPLES = 3


def _without_future_block(text: str) -> str:
    return re.sub(
        re.escape(FUTURE_START) + r".*?" + re.escape(FUTURE_END),
        "<future-block>",
        text,
        flags=re.DOTALL,
    )


def _mock_decision(mock: dict) -> UserDecision:
    return UserDecision(action=mock["action"], content=mock.get("content", ""))


def test_task_set_is_real_session_backed_and_marks_real_vs_synthetic_futures():
    repo_root = Path(__file__).resolve().parents[1]
    tasks = load_tasks()
    assert len(tasks) == EXPECTED_TASK_COUNT
    assert len({task["task_id"] for task in tasks}) == EXPECTED_TASK_COUNT

    for task in tasks:
        assert task["futures"]["future_a"]["provenance"] == "real"
        assert task["futures"]["future_b"]["provenance"] == "synthetic"
        # Provenance must be auditable: exact source task, session, node and index.
        assert task["source"]["session_id"]
        assert "original_session.json message" in task["source"]["decision_node"]
        assert task["source"]["decision_message_index"] >= 0
        assert task["source"]["decision_context_status"].strip()
        assert "verbatim" in task["futures"]["future_a"]["source_message"]
        assert "Synthetic" in task["futures"]["future_b"]["source_message"]
        # The recorded source task must be a real session directory in this repo.
        source_dir = repo_root / "tasks" / task["source"]["task"]
        assert (source_dir / "original_session.json").exists(), task["task_id"]
        # Future B must be flagged as synthetic on its own text, and the two
        # futures must stay distinguishable.
        assert task["futures"]["future_a"]["text"] != task["futures"]["future_b"]["text"]

    deepseek_kwargs = _real_llm_kwargs("deepseek/deepseek-flash", 0.8, None)
    assert deepseek_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "extra_body" not in _real_llm_kwargs("openai/gpt-5", 0.8, None)


def test_future_a_texts_match_the_recorded_real_message():
    """Future A must be the verbatim text at the recorded message index.

    ``entire-protected-dirs`` records two consecutive real user messages (219
    and 223) as one Future A, so each sentence is checked against the user
    messages starting at the recorded index.
    """
    repo_root = Path(__file__).resolve().parents[1]
    for task in load_tasks():
        session_path = (
            repo_root / "tasks" / task["source"]["task"] / "original_session.json"
        )
        assert session_path.exists(), task["task_id"]
        messages = json.loads(session_path.read_text(encoding="utf-8"))["messages"]
        node = task["source"]["decision_message_index"]
        recorded_turns: list[str] = []
        for message in messages[node:]:
            if message.get("role") != "user":
                continue
            raw = message.get("content")
            if isinstance(raw, list):
                text = "\n".join(
                    block.get("text", "")
                    for block in raw
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            else:
                text = raw or ""
            if text.strip():
                recorded_turns.append(text)
            if len(recorded_turns) >= 3:
                break
        future_a = task["futures"]["future_a"]["text"]
        # Each recorded sentence must appear verbatim in a recorded user message.
        for sentence in future_a.split("\n\n"):
            assert any(sentence.strip() in turn for turn in recorded_turns), (
                task["task_id"],
                sentence[:60],
            )


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
        # Classifier rules, mock answers and provenance never reach the model.
        for field in (task["classifier"]["a"] + task["classifier"]["b"]):
            assert field not in inputs["blind"].session_analysis


def test_raw_tool_call_capture_keeps_provider_bytes():
    class Function:
        name = "question"
        arguments = '{"content": "why did you pick the static list?"}'

    class ToolCall:
        id = "call_abc123"
        type = "function"
        function = Function()

    class Response:
        content = ""
        tool_calls = [ToolCall()]

    captured = capture_raw_model_output(Response())
    assert captured["content"] == ""
    assert captured["tool_calls"] == [
        {
            "id": "call_abc123",
            "type": "function",
            "function": {
                "name": "question",
                # Untouched, unparsed provider string.
                "arguments": '{"content": "why did you pick the static list?"}',
            },
        }
    ]
    assert captured["raw_response_fallback"] == Function.arguments
    assert legacy_raw_response(captured) == Function.arguments

    # Dict-shaped responses (litellm returns both shapes) are supported too.
    dict_response = {
        "content": "plain text",
        "tool_calls": [
            {
                "id": "call_dict",
                "type": "function",
                "function": {"name": "no-op", "arguments": "{}"},
            }
        ],
    }
    dict_captured = capture_raw_model_output(dict_response)
    assert dict_captured["tool_calls"][0]["id"] == "call_dict"
    assert dict_captured["tool_calls"][0]["function"]["arguments"] == "{}"
    assert legacy_raw_response(dict_captured) == "plain text"

    assert capture_raw_model_output(None) == {"content": "", "tool_calls": []}
    assert legacy_raw_response({"content": "", "tool_calls": []}) == ""


def test_classifier_splits_speak_status_from_direction():
    rules = {"a": ["core"], "b": ["extension"]}

    spoken = classify_decision(UserDecision("new_requirement", "move to core"), rules)
    assert spoken["message_status"] == "speak"
    assert spoken["direction"] == "A"
    assert spoken["label"] == "A"

    silent = classify_decision(UserDecision("no-op"), rules)
    assert silent["message_status"] == "no_op"
    assert silent["direction"] == "NoOp"
    assert silent["label"] == "Other"

    # A message-tool call whose content is a declared no-op marker stays a no-op.
    marker = classify_decision(
        UserDecision("redirect", "i stayed silent and let the agent keep working."), rules
    )
    assert marker["direction"] == "NoOp"
    assert marker["message_status"] == "no_op"

    assert MESSAGE_STATUSES == ("speak", "no_op")
    assert "Ambiguous" in DIRECTIONS and "Neutral" in DIRECTIONS


def test_rule_classifier_marks_ties_ambiguous_and_reports_negation_evidence():
    rules = {"a": ["core"], "b": ["extension"]}

    tie = classify_decision(UserDecision("question", "core or extension?"), rules)
    assert tie["label"] == "Other"
    assert tie["direction"] == "Ambiguous"
    assert tie["reason"] == "A and B regex evidence tied"

    neither = classify_decision(UserDecision("question", "what is the diff looking like?"), rules)
    assert neither["direction"] == "Neutral"

    # A contrastive connector is recorded next to the hit it may negate. Here
    # "instead of" precedes the B hit ("static patterns") but not the A hit.
    contrastive = classify_message(
        "use a second LLM as reviewer instead of static patterns", "redirect",
        {"a": ["LLM.{0,40}review"], "b": ["static patterns"]},
    )
    a_hit = contrastive["a_hits"][0]
    b_hit = contrastive["b_hits"][0]
    assert a_hit.get("may_be_contrastive") is None
    assert b_hit["may_be_contrastive"] is True
    assert "instead of" in b_hit["contrastive_cue"]
    assert a_hit["span"][0] < len("use a second LLM as reviewer ")
    assert a_hit["match"]
    # The tie is explicit rather than silently folded into a direction.
    assert contrastive["direction"] == "Ambiguous"
    assert contrastive["label"] == "Other"

    # A plain mention keeps the hit without a negation flag.
    plain = classify_message("keep the static patterns", "redirect", {"a": ["core"], "b": ["static patterns"]})
    assert plain["b_hits"][0].get("may_be_contrastive") is None

    # The action label is never scored as direction evidence.
    action_only = classify_message("what are the scores now?", "redirect", {"a": ["redirect"], "b": []})
    assert action_only["a_score"] == 0
    assert action_only["action_name"] == "redirect"


def test_mock_classification_is_reviewable_per_task():
    """Mock answers must exercise both directions and a neutral blind baseline."""
    for task in load_tasks():
        by_condition = {}
        for condition in CONDITIONS:
            by_condition[condition] = [
                classify_decision(_mock_decision(mock), task["classifier"])
                for mock in task["mock_decisions"][condition]
            ]

        blind = by_condition["blind"]
        future_a = by_condition["future_a"]
        future_b = by_condition["future_b"]
        assert all(item["label"] == "A" for item in future_a), task["task_id"]
        # Tolerance: a Future B mock may legitimately be a no-op (the pilot's
        # "B equals keeping the status quo" failure mode) or resolve to a tie,
        # because a negated A-phrase is still an A-regex hit. At most one such
        # sample, and the B direction must still dominate.
        assert sum(item["label"] != "B" for item in future_b) <= 1, task["task_id"]
        assert sum(item["label"] == "B" for item in future_b) >= len(future_b) - 1, task["task_id"]
        assert sum(item["label"] == "Other" for item in blind) >= len(blind) - 1, task["task_id"]
        for item in blind:
            assert item["label"] == "Other", task["task_id"]


def test_mock_run_validates_switching_independence_and_recording(output_dir):
    tasks = load_tasks()
    summary = asyncio.run(
        run_experiment(
            tasks,
            list(CONDITIONS),
            SAMPLES,
            output_dir,
            mock=True,
            model=None,
        )
    )

    expected_records = EXPECTED_TASK_COUNT * len(CONDITIONS) * SAMPLES
    assert expected_records == 81
    assert summary["record_count"] == expected_records
    records = [
        json.loads(line)
        for line in (output_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == expected_records
    assert (output_dir / "summary.json").exists()
    assert summary["schema_version"] == 2

    for record in records:
        assert [message["role"] for message in record["prompt_messages"]] == ["system", "user"]
        assert record["decision"]["raw_response"]
        assert len(record["snapshot_sha256"]) == 64
        assert len(record["prompt_sha256"]) == 64
        # Raw provider output is persisted with id/type/name/unparsed arguments.
        raw = record["raw_model_output"]
        assert raw["content"] == ""
        assert len(raw["tool_calls"]) == 1
        tool_call = raw["tool_calls"][0]
        assert tool_call["id"]
        assert tool_call["type"] == "function"
        assert tool_call["function"]["name"] == record["decision"]["action"]
        assert isinstance(tool_call["function"]["arguments"], str)
        assert tool_call["function"]["arguments"]
        if record["decision"]["action"] != "no-op":
            assert json.loads(tool_call["function"]["arguments"])["content"] == record["decision"]["content"]
        # Every sample is a fresh simulator: one system + one user message only.
        assert record["classification"]["message_status"] in MESSAGE_STATUSES
        assert record["classification"]["direction"] in DIRECTIONS
        assert record["classification"]["label"] in {"A", "B", "Other"}

    for task in tasks:
        task_records = [record for record in records if record["task_id"] == task["task_id"]]
        assert len(task_records) == len(CONDITIONS) * SAMPLES
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
        assert len({record["prompt_sha256"] for record in task_records}) > 1

        a_records = [r for r in task_records if r["condition"] == "future_a"]
        b_records = [r for r in task_records if r["condition"] == "future_b"]
        assert {record["classification"]["label"] for record in a_records} == {"A"}
        b_labels = [record["classification"]["label"] for record in b_records]
        # Future B must dominate, but one sample may resolve to a tie or a
        # no-op because a negated A-phrase is still an A-regex hit.
        assert b_labels.count("B") >= len(b_labels) - 1
        assert b_labels.count("A") <= 1

    distributions = summary["distributions"]
    assert set(distributions) == {task["task_id"] for task in tasks}
    for task_id, by_condition in distributions.items():
        for condition, buckets in by_condition.items():
            assert set(buckets) == {"labels", "message_status", "direction", "action", "samples"}
            assert set(buckets["labels"]) == {"A", "B", "Other"}
            assert set(buckets["message_status"]) == set(MESSAGE_STATUSES)
            assert set(buckets["direction"]) == set(DIRECTIONS)
            assert buckets["samples"] == SAMPLES
            assert sum(buckets["labels"].values()) == SAMPLES
            assert sum(buckets["message_status"].values()) == SAMPLES
            assert sum(buckets["direction"].values()) == SAMPLES
            assert sum(buckets["action"].values()) == SAMPLES


def test_offline_reclassification_matches_stored_labels_without_rewriting(output_dir):
    tasks = load_tasks()
    asyncio.run(run_experiment(tasks, list(CONDITIONS), SAMPLES, output_dir, mock=True, model=None))
    decisions_path = output_dir / "decisions.jsonl"
    original_text = decisions_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in original_text.splitlines()]

    report = reclassify_records(records, {task["task_id"]: task for task in tasks})
    assert report["records"] == 81
    assert report["changed"] == 0
    assert set(report["tasks"]) == {task["task_id"] for task in tasks}
    # The audit is read-only.
    assert decisions_path.read_text(encoding="utf-8") == original_text
