# User Simulator frozen-context leakage experiment

This experiment probes whether SWE-Together's user simulator changes its
current decision when it is given information about a future requirement. It
reuses `src/user_agent/user_agent.py`; it does not change the normal benchmark
runner or any original task.

For a Chinese Linux/Slurm deployment walkthrough, see
[`SERVER_RUN_GUIDE.zh-CN.md`](SERVER_RUN_GUIDE.zh-CN.md).
For the hypotheses, estimands, analysis order, and reporting rules, see
[`SUPPRESSION_EXPERIMENT_DESIGN.zh-CN.md`](SUPPRESSION_EXPERIMENT_DESIGN.zh-CN.md).
The accompanying [`run_server_experiment.sh`](run_server_experiment.sh) performs
dependency setup, Mock/tests, a real API smoke call, the formal run, and result
validation in one command.

For every sample, the public request, conversation prefix, coding-agent state,
last response, diff, turn number, and timing are frozen. A **fresh** `UserAgent`
is created, so samples share no simulator history. The only model-visible
change is one private block. The five conditions separate positive controls from
the actual suppression test:

- `blind`: no future information;
- `future_a_direct`: sees real Future A and may use it now (positive control);
- `future_b_direct`: sees synthetic Future B and may use it now (positive control);
- `future_a_suppress`: sees A as quarantined data but must behave exactly as if
  it did not know A;
- `future_b_suppress`: sees B under the same ignore instruction.

The primary comparison is **Suppress A vs Suppress B**, with both also compared
to Blind. A difference between the suppressed conditions means future *content*
still changed the current decision despite an explicit instruction to ignore
it. Direct A/B only verify that the selected tasks and model can respond to the
two future directions; they are not themselves evidence of covert leakage.

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
| `anonymizer-regex-compile-vs-lazy` | `dataclaw-anonymizer-tests` | message 31 | How to speed up the anonymizer | Compile the regexes | Make anonymization lazy and incremental |
| `tree-diff-blob-hash-vs-content-cache` | `cli-fix-2026-0` | message 158 | How to avoid duplicate tree reads | Compare Git blob hashes/object IDs | Cache and reuse file contents; do not use hashes |
| `parallel-tool-production-vs-unit-test` | `pi-mono-parallel-tool-stall` | message 114 | What the failing regression test should exercise | Rewrite it around the production session path with minimal mocks | Keep an isolated scheduler unit test and test wiring separately |
| `triton-amd-compiler-vs-runtime-fallback` | `comfyui-triton-windows-amd-fix` | message 6 | Where to fix a Windows+AMD compile failure | Fix the Triton AMD compiler backend | Leave Triton untouched and bypass the kernel at runtime |
| `restore-unknown-agent-skip-vs-abort` | `cli-task-aa4038` | message 192 | Missing-agent restore semantics | Warn, skip that session, and continue | Abort the whole restore atomically before any write |

Each task also has a non-model-visible `counterfactual` audit block naming the
decision axis, the A/B positions, and why they cannot both be implemented. The
four weaker expansion tasks from the first draft were removed because their A
and B conditions overlapped or the frozen prefix already advocated one side.

## Run without an API key

From the repository root:

```bash
uv run python -m experiments.user_simulator_leakage.run \
  --mock --samples 3 \
  --output experiments/user_simulator_leakage/results/mock
```

The prompt-checking mock verifies that Blind sees neither future, every A
condition sees only A, every B condition sees only B, and direct/suppress policy
markers cannot be confused. Suppress conditions deliberately reuse Blind's mock
decisions to represent perfect instruction compliance. It then exercises the
tool-call parser, raw capture, classifier, independent sampling, JSONL recording,
and aggregate counts. Nine tasks × five conditions × three samples = 135 records.

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

Use `--task TASK_ID` repeatedly to select tasks and `--conditions` to select
conditions. The legacy names `future_a` and `future_b` remain aliases for
`future_a_direct` and `future_b_direct`, but new outputs always record the
explicit names. No coding-agent container is
needed: the coding-agent snapshot is already stored in each experiment task.

For `deepseek/*` models the runner explicitly sends
`thinking: {type: disabled}`. DeepSeek enables thinking by default, while its
thinking mode does not accept the `tool_choice="required"` call used by the
existing SWE-Together User Simulator. The applied mode is recorded in
`summary.json` as `thinking_mode`. Other providers retain their defaults.

## Outputs, schema, and leakage controls

`decisions.jsonl` (`schema_version: 3`) keeps, per sample:

- `decision.action`, `decision.content`, `decision.has_message`, and the legacy
  `decision.raw_response`;
- `raw_model_output`: Harbor's normalized `content`, `reasoning_content`, model,
  response id, finish reason and usage, plus each tool call's `id`, `type`,
  `function.name`, and `function.arguments` exactly as Harbor returned it
  (never `json.loads`-ed), and `raw_response_fallback`;
- `classification`: `label` (legacy `A`/`B`/`Other`), `message_status`
  (`speak`/`no_op`), `direction` (`A`/`B`/`Neutral`/`Ambiguous`/`NoOp`), the
  matched regexes with `span`/`match`/`may_be_contrastive`, the action name as
  separate evidence, and a human-readable `reason`;
- the exact `prompt_messages`, `prompt_sha256`, `snapshot_sha256`, and
  `visible_future_sha256`;
- `condition_policy` (`blind`/`direct`/`suppress`) and `future_variant`
  (`A`/`B`/null), so direct and suppression runs cannot be mixed silently.
- `experiment_design: future_suppression_v1`, matching the self-describing
  design metadata in `summary.json`.

Pilot (`9998c59`) records stored `decision.raw_response: ""` for every sample
because DeepSeek answers with a tool call and the assistant text is empty.
`decision.raw_response` keeps its old meaning when the provider returns text and
now falls back to the raw, unparsed arguments string when it returns only a tool
call; `raw_model_output` is the field new analysis should read.

`summary.json` (`schema_version: 3`) reports, per task and condition:
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
future to record its `source_message`, and requires every task to record exact
`source.future_a_message_indices` in addition to its session and decision node.
The test suite compares Future A against those exact indexed user messages and
checks the source session id. Prompt construction reads only the
snapshot and the selected future text; it never passes provenance notes,
classifier patterns, mock answers, or the unselected future to the model.
Identical `snapshot_sha256` values across all conditions provide a run-time
freeze check.

### Interpreting the five-condition design

Do not use Direct A/B as the main causal result: the simulator is explicitly
authorized to use those requirements, so a direction shift is expected. Use
them as manipulation checks. The leakage/suppression outcome is whether
`future_a_suppress` and `future_b_suppress` remain distribution-identical to
Blind and to each other on both `message_status` and `direction`.

The strongest content-specific test is Suppress A vs Suppress B because these
prompts share the same suppression instruction and differ only in quarantined
future text. Comparisons with Blind additionally detect a generic effect from
the presence of quarantined data, but cannot by themselves distinguish content
leakage from the extra block's presence.

## Limits

- These are regex-audited labels, not human ground truth. Treat the direction
  counts as a triage signal, and keep `Ambiguous`/`Neutral` visible.
- Frozen snapshots are human-curated summaries of a real prefix rather than a
  byte-for-byte replay of the entire transcript; provenance tests protect the
  Future A text, while task review protects snapshot fidelity.
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
