# User Simulator frozen-context leakage experiment

This experiment probes whether SWE-Together's user simulator changes its
current decision when it is given information about a future requirement. It
reuses `src/user_agent/user_agent.py`; it does not change the normal benchmark
runner or any original task.

For a Chinese Linux/Slurm deployment walkthrough, see
[`SERVER_RUN_GUIDE.zh-CN.md`](SERVER_RUN_GUIDE.zh-CN.md).
The accompanying [`run_server_experiment.sh`](run_server_experiment.sh) performs
dependency setup, Mock/tests, a real API smoke call, the formal run, and result
validation in one command.

For every sample, the public request, conversation prefix, coding-agent state,
last response, diff, turn number, and timing are frozen. A **fresh** `UserAgent`
is created, so samples share no simulator history. The only model-visible
change is one private block:

- `blind`: no future information;
- `future_a`: the real later requirement from the source session;
- `future_b`: a plausible synthetic counterfactual, explicitly marked as such
  in the task file.

## The nine decision nodes

All nine tasks come from real SWE-Together sessions under `tasks/<name>/`.
`message` is the exact `original_session.json` index of the decision node;
Future A is the verbatim real user message at that node. Per-task provenance,
including what is real and what is synthetic, is recorded inside each task file
under `source` and `futures`, and summarized in
[`TASK_SELECTION.zh-CN.md`](TASK_SELECTION.zh-CN.md).

| Experiment task | Source task | Decision node | Current decision | Future A (real) | Future B (synthetic) |
|---|---|---|---|---|---|
| `openclaw-security-reviewer` | `openclaw-security-review-flow` | message 75 | Static prompt-injection gate | Add an LLM reviewer with multi-turn context | Stay deterministic/offline with fixed rules |
| `pi-mono-extension-to-core` | `pi-mono-auto-cbb62cbe` | message 89 | Where to place an iterated UI easter egg | Move it into core and wire model selection | Keep it as a reloadable opt-in extension |
| `entire-protected-dirs` | `cli-task-2a55af` | messages 219/223 | How protected config directories are owned | Let each Agent declare protected paths | Keep the Claude/Gemini allowlist local and static |
| `lumina-newbie-merge-into-existing-classes` | `comfyui-newbie-lumina-refactor` | message 71 | Where the NewBie architecture lives | Fold the NewBie classes into `NextDiT`/`Lumina2` | Keep NewBie as a separate architecture module |
| `skills-loader-reuse-ignore-discovery` | `pi-mono-auto-0467b78e` | message 16 | How skill discovery handles ignore rules | Make `skills.ts` work like the package-manager resolver | Keep the loaders separate and share only a rule helper |
| `regularization-balance-single-pass` | `sd-scripts-reg-image-dedup` | message 33 | When reg images are registered | Register reg images once after filtering | Move the filtering before the initial registration |
| `multisession-prompt-uuids` | `cli-task-b3d2dd` | message 66 | Whether to reject non-UUID session ids | (real user rejects the UUID filter) | Fix the stray session at its source instead |
| `anonymizer-regex-compile-vs-lazy` | `dataclaw-anonymizer-tests` | message 31 | How to speed up the anonymizer | Compile the regexes | Make anonymization lazy and incremental |
| `shadow-prompt-keep-on-carryover` | `cli-task-2f5833` | message 163 | Whether the metadata prompt survives condensation | Keep `prompt.txt` when carry-over files exist | Delete the metadata prompt unconditionally |

Two tasks use context that is not blind to the topic, and both say so in
`source.decision_context_status`: `multisession-prompt-uuids` freezes a context
in which the agent has already restated the repository's plan (which includes
the filtering idea) and warns that the real user then contradicts it, and
`shadow-prompt-keep-on-carryover` freezes a context in which the plan already
declares `prompt.txt` the source of truth without deciding the carry-over case.
Reports should stratify these two rather than pooling them with the other seven.

## Run without an API key

From the repository root:

```bash
uv run python -m experiments.user_simulator_leakage.run \
  --mock --samples 3 \
  --output experiments/user_simulator_leakage/results/mock
```

The prompt-checking mock verifies that Blind sees neither future, Future A sees
only A, and Future B sees only B. It then exercises the existing tool-call
parser, the raw tool-call capture, the rule classifier, independent sampling,
JSONL recording, and the aggregate counts. Nine tasks × three conditions ×
three samples = 81 records.

Run the focused tests with:

```bash
uv run python -m pytest -q tests/test_user_simulator_leakage.py
```

If the pytest temp root is not writable (restricted sandbox), point the tests at
a writable scratch directory and skip pytest's temp machinery:

```bash
LEAKAGE_TEST_OUTPUT_DIR=/path/to/scratch \
  uv run python -m pytest -q -p no:tmpdir tests/test_user_simulator_leakage.py
```

## Run real samples later

Install the normal SWE-Together environment and set the provider credential
used by LiteLLM, then omit `--mock`:

```bash
uv run python -m experiments.user_simulator_leakage.run \
  --model openrouter/google/gemini-3.1-pro-preview \
  --samples 20 \
  --output experiments/user_simulator_leakage/results/gemini
```

Use `--task TASK_ID` repeatedly to select tasks and `--conditions
blind,future_a,future_b` to select conditions. No coding-agent container is
needed: the coding-agent snapshot is already stored in each experiment task.

For `deepseek/*` models the runner explicitly sends
`thinking: {type: disabled}`. DeepSeek enables thinking by default, while its
thinking mode does not accept the `tool_choice="required"` call used by the
existing SWE-Together User Simulator. The applied mode is recorded in
`summary.json` as `thinking_mode`. Other providers retain their defaults.

## Outputs, schema, and leakage controls

`decisions.jsonl` (`schema_version: 2`) keeps, per sample:

- `decision.action`, `decision.content`, `decision.has_message`, and the legacy
  `decision.raw_response`;
- `raw_model_output`: `content`, each tool call's `id` and `type`, the
  `function.name`, and `function.arguments` exactly as the provider sent it
  (never `json.loads`-ed), plus `raw_response_fallback`;
- `classification`: `label` (legacy `A`/`B`/`Other`), `message_status`
  (`speak`/`no_op`), `direction` (`A`/`B`/`Neutral`/`Ambiguous`/`NoOp`), the
  matched regexes with `span`/`match`/`may_be_contrastive`, the action name as
  separate evidence, and a human-readable `reason`;
- the exact `prompt_messages`, `prompt_sha256`, `snapshot_sha256`, and
  `visible_future_sha256`.

Pilot (`9998c59`) records stored `decision.raw_response: ""` for every sample
because DeepSeek answers with a tool call and the assistant text is empty.
`decision.raw_response` keeps its old meaning when the provider returns text and
now falls back to the raw, unparsed arguments string when it returns only a tool
call; `raw_model_output` is the field new analysis should read.

`summary.json` (`schema_version: 2`) reports, per task and condition:
`labels` (legacy A/B/Other), `message_status`, `direction`, `action`, and
`sample_count`. Keeping `labels` preserves compatibility with the pilot
analysis, while `message_status` + `direction` stop the old conflation of
"did the simulator speak" with "which way did it point".

### Why the classifier splits status from direction

The pilot folded every `no-op` into `Other`, so `entire-protected-dirs` Future B
(which really is "keep the current static implementation") produced 20/20
no-ops that could not be distinguished from Blind. The current schema therefore
reports both dimensions and never assigns a no-op to a direction.

The classifier is regex-based and auditable:

- only the message text is scored; matching the action name (`redirect`,
  `question`, ...) is not treated as evidence about direction;
- equal counts are labelled `Ambiguous`, no match at all is `Neutral`;
- each hit keeps its pattern, span, and matched text;
- a contrastive connector (`instead of`, `rather than`, `don't need`, ...)
  within 80 characters *before* a hit is recorded as `may_be_contrastive` with
  the cue that triggered it. It is evidence for the reviewer, not an automatic
  label flip, because a negated phrase is as often the correct direction
  ("don't add an LLM reviewer") as the wrong one.

Old runs can be re-labelled offline without touching them:

```bash
uv run python -m experiments.user_simulator_leakage.run \
  --reclassify server-results/deepseek-deepseek-flash-20260927-150112/decisions.jsonl
```

Recommended final analysis is a hidden-condition manual blind label of a
sample, cross-checked against the rule output.

Task loading rejects a future that appears verbatim in its frozen snapshot,
requires Future A to be `real` and Future B to be `synthetic`, requires each
future to record its `source_message`, and requires every task to record
`source.session_id`, `source.decision_node`, `source.decision_message_index`,
and `source.decision_context_status`. Prompt construction reads only the
snapshot and the selected future text; it never passes provenance notes,
classifier patterns, mock answers, or the unselected future to the model.
Identical `snapshot_sha256` values across all conditions provide a run-time
freeze check.

## Limits

- These are regex-audited labels, not human ground truth. Treat the direction
  counts as a triage signal, and keep `Ambiguous`/`Neutral` visible.
- Two of the nine tasks freeze contexts that are not blind to the topic; see the
  table note above and `TASK_SELECTION.zh-CN.md`.
- Patterns are hand-written per task before any real call, which is auditable
  but cannot cover paraphrases; unmatched-but-directional text lands in
  `Neutral`. Patterns are plain regexes without word boundaries, so a short
  pattern can match inside an unrelated word (`core` inside `scores`); use `\b`
  in new rules where that matters.
- `blind` context can legitimately point toward A or B in principle; the mock
  keeps Blind samples neutral so that harness failures stay visible, which is
  why the mock expectations allow one tolerant sample per condition.
- The mock verifies the harness, not model behaviour. Mock distributions are
  pipeline fixtures, never research findings.
