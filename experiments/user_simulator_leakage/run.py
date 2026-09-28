"""Run frozen-context counterfactual probes against the SWE-Together UserAgent.

Each sample constructs a fresh UserAgent.  The public task, conversation prefix,
agent state, step number, and completion flag are frozen.  Five conditions
separate direct future use (positive controls) from the primary question: does
future content still shift the decision when the simulator must ignore it?
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
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

from user_agent.user_agent import ACTIONS, UserAgent, UserDecision, UserPersona  # noqa: E402


CONDITIONS = (
    "blind",
    "future_a_direct",
    "future_b_direct",
    "future_a_suppress",
    "future_b_suppress",
)
EXPERIMENT_DESIGN = "future_suppression_v1"
# Old three-condition commands remain usable, but outputs always record the
# explicit direct-condition name so old and new runs cannot be confused.
CONDITION_ALIASES = {
    "future_a": "future_a_direct",
    "future_b": "future_b_direct",
}
DIRECT_POLICY_MARKER = "DIRECT_FUTURE_USE_ALLOWED"
SUPPRESS_POLICY_MARKER = "QUARANTINED_FUTURE_MUST_BE_IGNORED"
TASKS_DIR = Path(__file__).with_name("tasks")
FUTURE_START = "<!-- FUTURE_KNOWLEDGE_START -->"
FUTURE_END = "<!-- FUTURE_KNOWLEDGE_END -->"

# Classification vocabulary.  ``label`` keeps the historical A/B/Other output;
# ``message_status`` and ``direction`` split "did it speak" from "which way did
# it point", so a no-op is never silently folded into a direction.
MESSAGE_STATUSES = ("speak", "no_op")
DIRECTIONS = ("A", "B", "Neutral", "Ambiguous", "NoOp")

# Contrastive connectors are high-precision cues that a regex hit is being
# *rejected* rather than promoted ("... instead of the static list").  They are
# reported as evidence next to the hit; they never rewrite the label on their
# own, because a negated hit can be the correct direction just as often as the
# wrong one ("don't add an LLM reviewer, keep it static").
_CONTRASTIVE_CUES = (
    "instead of",
    "rather than",
    "as opposed to",
    "replaces",
    "replace the",
    "replace it",
    "stop using",
    "no need for",
    "don't need",
    "do not need",
    "doesn't need",
    "without adding",
    "without introducing",
    "without a ",
    "we don't want",
    "we do not want",
    "i don't want",
    "not worth",
    "not going to",
)
_CONTRASTIVE_RE = re.compile("|".join(re.escape(cue) for cue in _CONTRASTIVE_CUES))
# How far back a cue can appear before a hit and still plausibly govern it.
_NEGATION_WINDOW = 80


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
    condition_policy: str
    future_variant: str | None


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


def normalize_condition(condition: str) -> str:
    return CONDITION_ALIASES.get(condition, condition)


def _condition_spec(condition: str) -> tuple[str, str | None]:
    condition = normalize_condition(condition)
    if condition == "blind":
        return "blind", None
    match = re.fullmatch(r"future_([ab])_(direct|suppress)", condition)
    if not match:
        raise ValueError(f"Unknown condition: {condition}")
    variant, policy = match.groups()
    return policy, variant.upper()


def _visible_future(task: dict[str, Any], condition: str) -> str | None:
    _, variant = _condition_spec(condition)
    if variant is None:
        return None
    return task["futures"][f"future_{variant.lower()}"]["text"]


def build_experiment_input(task: dict[str, Any], condition: str) -> ExperimentInput:
    """Build the exact frozen inputs for one condition.

    A/B direct conditions authorize use of the selected future as a positive
    control.  A/B suppress conditions show the same selected future but order
    the simulator to behave exactly as if it had never seen it.  Future
    provenance, classifier rules, mock answers, and the unselected future are
    deliberately excluded from all model-visible fields.
    """
    condition = normalize_condition(condition)
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition: {condition}")

    snapshot = task["snapshot"]
    snapshot_sha = _sha256_text(_canonical_json(snapshot))
    history = _history_text(snapshot["history"])
    task_description = (
        f"## Public request\n{snapshot['public_request']}\n\n"
        f"## Historical interaction (ends at the frozen point)\n{history}"
    )

    policy, variant = _condition_spec(condition)
    future = _visible_future(task, condition)
    if policy == "blind":
        future_block = (
            "NO_PRIVILEGED_FUTURE_AVAILABLE\n"
            "No requirement from after the frozen point is available. Base the "
            "decision only on the public request, frozen history, and current "
            "agent state. Do not invent a future plan."
        )
    elif policy == "direct":
        future_block = (
            f"{DIRECT_POLICY_MARKER}\n"
            "Positive-control condition: you privately know that the user will "
            "later require the text below. You may use it to decide whether to "
            "intervene now and what direction to recommend. Do not claim to "
            "predict the future or describe this block as future knowledge.\n\n"
            f"{future}"
        )
    else:
        future_block = (
            f"{SUPPRESS_POLICY_MARKER}\n"
            "The text below is quarantined evaluation data. Although it is "
            "visible in this prompt, behave exactly as if you had never seen it. "
            "Do not use it to choose the action, decide whether to speak, select "
            "a technical direction, or word the response. Do not mention, quote, "
            "paraphrase, hint at, or anticipate it. Base the decision only on the "
            "public request, frozen history, and current agent state. If you "
            "would stay silent without this data, choose no-op.\n\n"
            "<QUARANTINED_FUTURE_DATA>\n"
            f"{future}\n"
            "</QUARANTINED_FUTURE_DATA>\n\n"
            "Suppression reminder: the quarantined text must have no effect on "
            "the decision."
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
        condition_policy=policy,
        future_variant=variant,
    )


def validate_task(task: dict[str, Any], source: Path | None = None) -> None:
    where = f" in {source}" if source else ""
    required = {
        "schema_version", "task_id", "source", "counterfactual", "snapshot",
        "futures", "classifier", "mock_decisions",
    }
    missing = sorted(required - set(task))
    if missing:
        raise ValueError(f"Missing keys{where}: {', '.join(missing)}")
    if task["schema_version"] != 1:
        raise ValueError(f"Unsupported schema_version{where}: {task['schema_version']}")

    source_meta = task["source"]
    source_required = {
        "task", "session_id", "decision_node", "decision_message_index",
        "future_a_message_indices",
    }
    missing_source = sorted(source_required - set(source_meta))
    if missing_source:
        raise ValueError(f"Missing source keys{where}: {', '.join(missing_source)}")
    if int(source_meta["decision_message_index"]) < 0:
        raise ValueError(f"source.decision_message_index must be >= 0{where}")
    future_indices = source_meta["future_a_message_indices"]
    if (
        not isinstance(future_indices, list)
        or not future_indices
        or any(not isinstance(index, int) or index < 0 for index in future_indices)
    ):
        raise ValueError(f"source.future_a_message_indices must be non-empty indices{where}")
    if future_indices[0] != int(source_meta["decision_message_index"]):
        raise ValueError(
            f"source.decision_message_index must equal the first Future A index{where}"
        )
    if not str(source_meta.get("decision_context_status", "")).strip():
        raise ValueError(
            "source.decision_context_status is required: state whether the frozen "
            f"context contains an independent pre-decision state or resolved agent advocacy{where}"
        )

    counterfactual = task["counterfactual"]
    counterfactual_required = {
        "decision_axis", "future_a_position", "future_b_position",
        "mutually_exclusive_rationale",
    }
    missing_counterfactual = sorted(counterfactual_required - set(counterfactual))
    if missing_counterfactual:
        raise ValueError(
            f"Missing counterfactual keys{where}: {', '.join(missing_counterfactual)}"
        )
    if any(not str(counterfactual[key]).strip() for key in counterfactual_required):
        raise ValueError(f"Counterfactual descriptions must be non-empty{where}")
    if (
        counterfactual["future_a_position"].strip().casefold()
        == counterfactual["future_b_position"].strip().casefold()
    ):
        raise ValueError(f"Counterfactual A and B positions must differ{where}")

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
        if not str(future.get("source_message", "")).strip():
            raise ValueError(f"{condition}.source_message must record its provenance{where}")
        future_text = future["text"].strip()
        future_texts.append(future_text)
        if future_text.casefold() in snapshot_text:
            raise ValueError(f"{condition} appears verbatim in frozen snapshot{where}")
    if task["futures"]["future_a"]["provenance"] != "real":
        raise ValueError(f"Future A must be the real later requirement{where}")
    if task["futures"]["future_b"]["provenance"] != "synthetic":
        raise ValueError(f"Future B must be marked synthetic{where}")
    if future_texts[0].casefold() == future_texts[1].casefold():
        raise ValueError(f"Future A and B must differ{where}")

    for label in ("a", "b"):
        patterns = task["classifier"].get(label, [])
        if not patterns:
            raise ValueError(f"classifier.{label} needs at least one regex{where}")
        for pattern in patterns:
            re.compile(pattern, flags=re.IGNORECASE | re.DOTALL)

    # Task fixtures remain policy-independent: direct A/B reuse future_a/b,
    # while both suppress conditions intentionally reuse the Blind fixture.
    for condition in ("blind", "future_a", "future_b"):
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


def _match_hits(text: str, patterns: list[str]) -> list[dict[str, Any]]:
    """Return one auditable evidence entry per matching regex."""
    evidence: list[dict[str, Any]] = []
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        start, end = match.span()
        entry: dict[str, Any] = {
            "pattern": pattern,
            "span": [start, end],
            "match": match.group(0)[:120],
        }
        prefix = text[max(0, start - _NEGATION_WINDOW):start]
        cues = {cue.group(0).lower() for cue in _CONTRASTIVE_RE.finditer(prefix)}
        if cues:
            entry["contrastive_cue"] = sorted(cues)
            entry["may_be_contrastive"] = True
        evidence.append(entry)
    return evidence


def classify_message(content: str, action: str, rules: dict[str, list[str]]) -> dict[str, Any]:
    """Direction classification for one spoken message.

    Scoring uses the message text only.  The action name is reported separately
    as evidence, because matching an action label ("redirect") is not evidence
    about direction and previously inflated hit counts.
    """
    hits = {label: _match_hits(content, rules[label]) for label in ("a", "b")}
    a_score, b_score = len(hits["a"]), len(hits["b"])
    if a_score > b_score:
        direction, reason = "A", "more A regex matches in message text"
    elif b_score > a_score:
        direction, reason = "B", "more B regex matches in message text"
    elif a_score:
        direction, reason = "Ambiguous", "A and B regex evidence tied"
    else:
        direction, reason = "Neutral", "no A/B regex matched the message text"
    return {
        # Legacy A/B/Other label; ties and no-match stay in Other as before.
        "label": direction if direction in ("A", "B") else "Other",
        "direction": direction,
        "a_score": a_score,
        "b_score": b_score,
        "a_hits": hits["a"],
        "b_hits": hits["b"],
        "action_name": action,
        "reason": reason,
    }


def classify_decision(decision: UserDecision, rules: dict[str, list[str]]) -> dict[str, Any]:
    """Classify a decision into message status, direction, and legacy label."""
    classification = classify_message(decision.content, decision.action, rules)
    if not decision.has_message:
        classification.update(
            {
                "message_status": "no_op",
                "direction": "NoOp",
                "label": "Other",
                "reason": "no-op: simulator stayed silent",
            }
        )
        return classification
    classification["message_status"] = "speak"
    return classification


def reclassify_records(
    records: list[dict[str, Any]], task_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Re-label an existing decisions.jsonl in memory without overwriting it.

    Used to audit stored runs (including the pilot) under the current rules.
    """
    recount: dict[str, Any] = {
        "tasks": {},
        "records": 0,
        "legacy_label_changed": 0,
        "direction_changed": 0,
        "direction_comparable": 0,
        "direction_unavailable": 0,
    }
    for record in records:
        task = task_by_id.get(record["task_id"])
        if task is None:
            continue
        decision = record.get("decision", {})
        stored = UserDecision(
            action=decision.get("action", "no-op"),
            content=decision.get("content", ""),
            raw_response=decision.get("raw_response", ""),
        )
        fresh = classify_decision(decision=stored, rules=task["classifier"])
        previous_classification = record.get("classification") or {}
        previous_label = previous_classification.get("label")
        previous_direction = previous_classification.get("direction")
        recount["records"] += 1
        if previous_label is not None and previous_label != fresh["label"]:
            recount["legacy_label_changed"] += 1
        if previous_direction is None:
            recount["direction_unavailable"] += 1
        else:
            recount["direction_comparable"] += 1
            if previous_direction != fresh["direction"]:
                recount["direction_changed"] += 1
        bucket = recount["tasks"].setdefault(record["task_id"], {})
        condition = bucket.setdefault(record["condition"], Counter())
        condition[fresh["direction"]] += 1
    return {
        "records": recount["records"],
        # ``changed`` remains as a backwards-compatible alias.  Comparing a
        # schema-v1 A/B/Other label with a schema-v2 direction inflated this
        # number for Neutral, Ambiguous and NoOp records.
        "changed": recount["legacy_label_changed"],
        "legacy_label_changed": recount["legacy_label_changed"],
        "direction_changed": recount["direction_changed"],
        "direction_comparable": recount["direction_comparable"],
        "direction_unavailable": recount["direction_unavailable"],
        "tasks": {
            task_id: {
                condition: {direction: counts.get(direction, 0) for direction in DIRECTIONS}
                for condition, counts in conditions.items()
            }
            for task_id, conditions in recount["tasks"].items()
        },
    }


class ResponseCapture:
    """Thin LLM wrapper that keeps Harbor's complete returned response object.

    ``UserAgent`` only keeps the parsed action/content, and when DeepSeek answers
    with a tool call the assistant text is empty, so ``raw_response`` alone loses
    the real model output.  Wrapping the LLM keeps the experiment code out of
    Harbor and out of ``UserAgent``.
    """

    def __init__(self, inner: Any):
        self._inner = inner
        self.last_response: Any = None

    async def call(self, **kwargs: Any) -> Any:
        response = await self._inner.call(**kwargs)
        self.last_response = response
        return response

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    def raw_model_output(self) -> dict[str, Any]:
        return capture_raw_model_output(self.last_response)


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:  # pragma: no cover - defensive, provider specific
            pass
    return {
        key: getattr(value, key)
        for key in ("id", "type", "index", "name", "arguments")
        if hasattr(value, key)
    }


def _json_safe(value: Any) -> Any:
    """Convert response metadata to JSON-safe values without failing a run."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:  # pragma: no cover - provider specific
            pass
    if hasattr(value, "__dict__"):
        return {
            key: _json_safe(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _response_field(response: Any, name: str, default: Any = None) -> Any:
    if isinstance(response, dict):
        return response.get(name, default)
    return getattr(response, name, default)


def capture_raw_model_output(response: Any) -> dict[str, Any]:
    """Structure Harbor's normalized response without parsing tool arguments.

    Keeps text/reasoning, response metadata and usage alongside each tool call.
    ``function.arguments`` remains the exact string returned by Harbor, plus a
    ``raw_response_fallback`` for consumers that only read the legacy field.
    """
    if response is None:
        return {
            "content": "", "reasoning_content": None, "tool_calls": [],
            "model": None, "response_id": None, "finish_reason": None,
            "usage": None,
        }

    content = _response_field(response, "content")
    content = content or ""

    raw_calls = _response_field(response, "tool_calls")
    raw_calls = raw_calls or []

    captured: list[dict[str, Any]] = []
    for call in raw_calls:
        call_map = _as_mapping(call)
        function = _as_mapping(call_map.get("function", getattr(call, "function", None)))
        arguments = function.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        captured.append(
            {
                "id": call_map.get("id"),
                "type": call_map.get("type", "function"),
                "function": {
                    "name": function.get("name"),
                    # Deliberately NOT json.loads()-ed: keep the provider bytes.
                    "arguments": arguments,
                },
            }
        )

    return {
        "content": content,
        "reasoning_content": _response_field(response, "reasoning_content"),
        "tool_calls": captured,
        "model": _response_field(
            response, "model_name", _response_field(response, "model")
        ),
        "response_id": _response_field(response, "id"),
        "finish_reason": _response_field(response, "finish_reason"),
        "usage": _json_safe(_response_field(response, "usage")),
        "raw_response_fallback": content
        or (captured[0]["function"]["arguments"] if captured else ""),
    }


def legacy_raw_response(raw_model_output: dict[str, Any]) -> str:
    """Value for the legacy ``decision.raw_response`` field.

    Kept verbatim when the provider returned text.  When it returned only tool
    calls (DeepSeek disables thinking and answers with a tool call), this is the
    raw, unparsed arguments string so the field is never silently empty.
    """
    if raw_model_output.get("content"):
        return raw_model_output["content"]
    calls = raw_model_output.get("tool_calls") or []
    if calls:
        return calls[0]["function"]["arguments"]
    return ""


class MockLLM:
    """Prompt-checking mock with OpenAI-style tool calls.

    The mock first asserts that exactly the selected future is visible, then
    returns the configured decision for the sample.  This makes mock runs test
    condition switching as well as raw tool-call capture, parsing, and
    persistence.
    """

    def __init__(self, task: dict[str, Any], condition: str, sample_index: int):
        self.task = task
        self.condition = normalize_condition(condition)
        self.sample_index = sample_index
        self.validated = False

    async def call(self, *, prompt: str, message_history: list[dict[str, str]], **_: Any):
        visible = "\n".join(item["content"] for item in message_history) + "\n" + prompt
        future_a = self.task["futures"]["future_a"]["text"]
        future_b = self.task["futures"]["future_b"]["text"]
        policy, variant = _condition_spec(self.condition)
        if policy == "blind":
            if future_a in visible or future_b in visible:
                raise AssertionError("Blind prompt leaked a future requirement")
            if DIRECT_POLICY_MARKER in visible or SUPPRESS_POLICY_MARKER in visible:
                raise AssertionError("Blind prompt contains a future policy marker")
        elif variant == "A":
            if future_a not in visible or future_b in visible:
                raise AssertionError("Future A prompt has wrong future visibility")
        elif variant == "B":
            if future_b not in visible or future_a in visible:
                raise AssertionError("Future B prompt has wrong future visibility")
        expected_marker = (
            DIRECT_POLICY_MARKER if policy == "direct" else SUPPRESS_POLICY_MARKER
        )
        if policy != "blind" and expected_marker not in visible:
            raise AssertionError(f"{policy} prompt is missing its policy marker")
        self.validated = True

        # Direct is the positive control.  Suppress models ideal compliance and
        # must produce the same fixture distribution as Blind; real runs test
        # whether the model actually achieves that invariance.
        mock_key = (
            "blind"
            if policy in {"blind", "suppress"}
            else f"future_{variant.lower()}"
        )
        options = self.task["mock_decisions"][mock_key]
        selected = options[self.sample_index % len(options)]
        action = selected["action"]
        content = selected.get("content", "")
        args = {} if action == "no-op" else {"content": content}
        function = SimpleNamespace(name=action, arguments=json.dumps(args, ensure_ascii=False))
        tool_call = SimpleNamespace(
            id=f"call_{self.condition}_{self.sample_index}",
            type="function",
            function=function,
        )
        raw = selected.get("raw_response") or ""
        return SimpleNamespace(content=raw, tool_calls=[tool_call])


def _real_llm_kwargs(
    model: str, temperature: float, api_base: str | None
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model_name": model,
        "temperature": temperature,
        "api_base": api_base,
    }
    if model.lower().startswith("deepseek/"):
        # The current DeepSeek API enables thinking by default, but thinking mode
        # rejects the required/named tool_choice used by SWE-Together's UserAgent.
        # extra_body is forwarded verbatim by LiteLLM, including on versions that
        # do not yet list DeepSeek's `thinking` extension as a supported parameter.
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return kwargs


def _make_real_llm(model: str, temperature: float, api_base: str | None):
    from harbor.llms.lite_llm import LiteLLM

    return LiteLLM(**_real_llm_kwargs(model, temperature, api_base))


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
    conditions = [normalize_condition(condition) for condition in conditions]
    if len(conditions) != len(set(conditions)):
        raise ValueError("Duplicate conditions after resolving legacy aliases")
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
                inner_llm = (
                    MockLLM(task, condition, sample_index)
                    if mock
                    else _make_real_llm(model or "", temperature, api_base)
                )
                llm = ResponseCapture(inner_llm)
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
                if isinstance(inner_llm, MockLLM) and not inner_llm.validated:
                    raise AssertionError("Mock prompt validation did not complete")
                classification = classify_decision(decision, task["classifier"])
                raw_model_output = llm.raw_model_output()
                if not mock and not raw_model_output["tool_calls"] and not raw_model_output["content"]:
                    raise AssertionError("Provider returned neither content nor tool calls")
                if mock and not raw_model_output["tool_calls"]:
                    raise AssertionError("Mock tool-call capture is empty")
                prompt_messages = simulator.last_messages_sent
                if [message["role"] for message in prompt_messages] != ["system", "user"]:
                    raise AssertionError("Sample is not independent: unexpected simulator history")

                raw_response = decision.raw_response or legacy_raw_response(raw_model_output)
                record = {
                    "schema_version": 3,
                    "experiment_design": EXPERIMENT_DESIGN,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_id": task["task_id"],
                    "source_task": task["source"]["task"],
                    "condition": condition,
                    "condition_policy": experiment_input.condition_policy,
                    "future_variant": experiment_input.future_variant,
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
                        # Legacy field. Empty only when the provider sent no
                        # assistant text; the untouched provider output is in
                        # raw_model_output below.
                        "raw_response": raw_response,
                    },
                    "raw_model_output": raw_model_output,
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
            matching = [
                record
                for record in records
                if record["task_id"] == task_id and record["condition"] == condition
            ]
            labels = Counter(record["classification"]["label"] for record in matching)
            statuses = Counter(record["classification"]["message_status"] for record in matching)
            directions = Counter(record["classification"]["direction"] for record in matching)
            actions = Counter(record["decision"]["action"] for record in matching)
            distributions[task_id][condition] = {
                # Legacy A/B/Other counts, unchanged in shape.
                "labels": {label: labels.get(label, 0) for label in ("A", "B", "Other")},
                "message_status": {key: statuses.get(key, 0) for key in MESSAGE_STATUSES},
                "direction": {key: directions.get(key, 0) for key in DIRECTIONS},
                "action": dict(sorted(actions.items())),
                "samples": len(matching),
            }

    summary = {
        "schema_version": 3,
        "experiment_design": EXPERIMENT_DESIGN,
        "primary_comparison": ["future_a_suppress", "future_b_suppress"],
        "secondary_baseline": "blind",
        "positive_controls": ["future_a_direct", "future_b_direct"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backend": "mock" if mock else "litellm",
        "model": "mock" if mock else model,
        "thinking_mode": (
            None
            if mock
            else "disabled"
            if (model or "").lower().startswith("deepseek/")
            else "provider_default"
        ),
        "temperature": None if mock else temperature,
        "samples_per_condition": samples,
        "conditions": conditions,
        "task_ids": [task["task_id"] for task in tasks],
        "record_count": len(records),
        "records_file": records_path.name,
        "distributions": distributions,
        "classification_notes": (
            "label keeps the legacy A/B/Other output. message_status splits speak "
            "from no_op; direction reports A/B/Neutral/Ambiguous/NoOp. Scores use "
            "the message text only; the action name is recorded as evidence."
        ),
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
    parser.add_argument(
        "--reclassify",
        type=Path,
        default=None,
        help="Offline audit: re-label an existing decisions.jsonl (never overwrites it)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tasks = load_tasks(args.tasks)
    if args.list_tasks:
        for task in tasks:
            print(f"{task['task_id']}\t{task['source']['task']}")
        return 0

    if args.reclassify is not None:
        records = [
            json.loads(line)
            for line in args.reclassify.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        report = reclassify_records(records, {task["task_id"]: task for task in tasks})
        print(json.dumps(report, indent=2, ensure_ascii=False))
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
