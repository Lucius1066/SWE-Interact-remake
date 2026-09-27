# User Simulator frozen-context leakage experiment

This experiment probes whether SWE-Together's user simulator changes its
current decision when it is given information about a future requirement. It
reuses `src/user_agent/user_agent.py`; it does not change the normal benchmark
runner or any original task.

For a Chinese Linux/Slurm deployment walkthrough, see
[`SERVER_RUN_GUIDE.zh-CN.md`](SERVER_RUN_GUIDE.zh-CN.md).

For every sample, the public request, conversation prefix, coding-agent state,
last response, diff, turn number, and timing are frozen. A **fresh** `UserAgent`
is created, so samples share no simulator history. The only model-visible
change is one private block:

- `blind`: no future information;
- `future_a`: the real later requirement from the source session;
- `future_b`: a plausible synthetic counterfactual, explicitly marked as such
  in the task file.

The three selected decision nodes are:

| Experiment task | Source SWE-Together task | Current decision | Future A (real) | Future B (synthetic) |
|---|---|---|---|---|
| `openclaw-security-reviewer` | `openclaw-security-review-flow` | Static prompt-injection gate | Add an LLM reviewer with multi-turn context | Remain deterministic/offline with fixed rules |
| `pi-mono-extension-to-core` | `pi-mono-auto-cbb62cbe` | Where to place an iterated UI easter egg | Move it into core and wire model selection | Keep it as a reloadable opt-in extension |
| `entire-protected-dirs` | `cli-task-2a55af` | How protected config directories are owned | Let each Agent declare protected paths | Keep the Claude/Gemini allowlist local and static |

## Run without an API key

From the repository root:

```bash
python -m experiments.user_simulator_leakage.run \
  --mock --samples 3 \
  --output experiments/user_simulator_leakage/results/mock
```

The prompt-checking mock verifies that Blind sees neither future, Future A sees
only A, and Future B sees only B. It then exercises the existing tool-call
parser, rule classifier, independent sampling, JSONL recording, and aggregate
counts.

Run the focused tests with:

```bash
python -m pytest -q tests/test_user_simulator_leakage.py
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

## Outputs and leakage controls

`decisions.jsonl` keeps the structured action, full raw model response, exact
prompt messages, prompt hash, and frozen-snapshot hash for every sample.
`summary.json` reports A/B/Other counts by task and condition. Classification is
regex-based and auditable; ambiguous/tied decisions remain `Other`.

Task loading rejects a future that appears verbatim in its frozen snapshot.
Prompt construction reads only the snapshot and the selected future text; it
never passes provenance notes, classifier patterns, mock answers, or the
unselected future to the model. Identical `snapshot_sha256` values across all
conditions provide a run-time freeze check.
