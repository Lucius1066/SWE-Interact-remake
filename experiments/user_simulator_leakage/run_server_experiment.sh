#!/usr/bin/env bash
# One-command Linux/Slurm runner for the frozen-context leakage experiment.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

# DeepSeek's current official low-cost agent model. The provider prefix is the
# LiteLLM routing prefix; the upstream model id is deepseek-v4-flash.
MODEL="deepseek/deepseek-v4-flash"
SAMPLES=20
TEMPERATURE=0.8
CONDITIONS="blind,future_a,future_b"
OUTPUT_ROOT="server-results"
ENV_FILE=""
RUN_TAG=""
MOCK_ONLY=0
SKIP_SYNC=0
SKIP_TESTS=0
SKIP_MOCK=0
SKIP_API_SMOKE=0
TASKS=()

usage() {
  cat <<'EOF'
Usage:
  ./experiments/user_simulator_leakage/run_server_experiment.sh [options]

Interactive DeepSeek run (the key is read silently and is not saved):
  ./experiments/user_simulator_leakage/run_server_experiment.sh --samples 20

Non-interactive DeepSeek/Slurm run:
  ./experiments/user_simulator_leakage/run_server_experiment.sh \
    --env-file "$HOME/.config/swe-leakage/deepseek.env" \
    --samples 20

Mock-only example (no API key):
  ./experiments/user_simulator_leakage/run_server_experiment.sh --mock-only

Options:
  --model MODEL          LiteLLM model name (default: deepseek/deepseek-v4-flash).
  --samples N            Independent samples per task/condition (default: 20).
  --temperature FLOAT    User Simulator temperature (default: 0.8).
  --conditions LIST      Comma-separated conditions (default: all three).
  --task TASK_ID         Select a task; repeat this flag to select several.
  --output-root DIR      Parent directory for all outputs (default: server-results).
  --run-tag NAME         Formal result directory name (default: model + timestamp).
  --env-file PATH        Source API credentials from a file outside the repository.
  --mock-only            Run dependency setup, Mock, and tests; make no real API call.
  --skip-sync            Do not run `uv sync --frozen`.
  --skip-tests           Skip the focused pytest suite.
  --skip-mock            Skip the Mock smoke run.
  --skip-api-smoke       Skip the one-call real API smoke test.
  -h, --help             Show this help.

The script never prints API keys. It writes one result directory per run and
validates API errors, record counts, prompt freezing, and snapshot hashes.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

on_error() {
  local exit_code=$?
  printf 'ERROR: command failed at line %s (exit %s).\n' "${BASH_LINENO[0]}" "$exit_code" >&2
  exit "$exit_code"
}
trap on_error ERR

while (($#)); do
  case "$1" in
    --model)
      (($# >= 2)) || die "--model requires a value"
      MODEL="$2"
      shift 2
      ;;
    --samples)
      (($# >= 2)) || die "--samples requires a value"
      SAMPLES="$2"
      shift 2
      ;;
    --temperature)
      (($# >= 2)) || die "--temperature requires a value"
      TEMPERATURE="$2"
      shift 2
      ;;
    --conditions)
      (($# >= 2)) || die "--conditions requires a value"
      CONDITIONS="$2"
      shift 2
      ;;
    --task)
      (($# >= 2)) || die "--task requires a value"
      TASKS+=("$2")
      shift 2
      ;;
    --output-root)
      (($# >= 2)) || die "--output-root requires a value"
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --run-tag)
      (($# >= 2)) || die "--run-tag requires a value"
      RUN_TAG="$2"
      shift 2
      ;;
    --env-file)
      (($# >= 2)) || die "--env-file requires a value"
      ENV_FILE="$2"
      shift 2
      ;;
    --mock-only)
      MOCK_ONLY=1
      shift
      ;;
    --skip-sync)
      SKIP_SYNC=1
      shift
      ;;
    --skip-tests)
      SKIP_TESTS=1
      shift
      ;;
    --skip-mock)
      SKIP_MOCK=1
      shift
      ;;
    --skip-api-smoke)
      SKIP_API_SMOKE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1 (use --help)"
      ;;
  esac
done

[[ "$SAMPLES" =~ ^[1-9][0-9]*$ ]] || die "--samples must be a positive integer"
[[ -n "$CONDITIONS" ]] || die "--conditions cannot be empty"

cd "$REPO_ROOT"

if [[ -n "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" ]] || die "credential file not found: $ENV_FILE"
  # The credential file is user-owned shell syntax (KEY=value or export KEY=value).
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# For an interactive login shell, make the common DeepSeek path genuinely
# one-command: read the secret without echo and keep it only in this process
# environment. Batch jobs have no terminal and must use --env-file or export.
if ((MOCK_ONLY == 0)) && [[ "$MODEL" == deepseek/* ]] && [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  if [[ -t 0 ]]; then
    printf '请输入 DeepSeek API Key（输入不会显示，也不会保存）：' >&2
    IFS= read -r -s DEEPSEEK_API_KEY
    printf '\n' >&2
    [[ -n "$DEEPSEEK_API_KEY" ]] || die "DeepSeek API Key cannot be empty"
    export DEEPSEEK_API_KEY
  else
    die "DEEPSEEK_API_KEY is not set; use --env-file for a non-interactive job"
  fi
fi

command -v uv >/dev/null 2>&1 || die "uv is not installed or not on PATH"
mkdir -p "$OUTPUT_ROOT"

printf '\n== SWE-Together User Simulator leakage experiment ==\n'
printf 'Repository : %s\n' "$REPO_ROOT"
printf 'Commit     : %s\n' "$(git rev-parse --short HEAD)"
printf 'Output root: %s\n' "$OUTPUT_ROOT"

if ((SKIP_SYNC == 0)); then
  printf '\n[1/5] Installing locked dependencies...\n'
  uv sync --frozen
else
  printf '\n[1/5] Dependency sync skipped.\n'
fi

PYTHON_VERSION="$(uv run python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$PYTHON_VERSION" == "3.12" ]] || die "project requires Python 3.12; uv selected $PYTHON_VERSION"

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"

if ((SKIP_MOCK == 0)); then
  MOCK_DIR="$OUTPUT_ROOT/mock-$TIMESTAMP"
  printf '\n[2/5] Running Mock smoke test -> %s\n' "$MOCK_DIR"
  uv run python -m experiments.user_simulator_leakage.run \
    --mock \
    --samples 3 \
    --output "$MOCK_DIR"
else
  printf '\n[2/5] Mock smoke test skipped.\n'
fi

if ((SKIP_TESTS == 0)); then
  printf '\n[3/5] Running focused tests...\n'
  uv run python -m pytest -q tests/test_user_simulator_leakage.py
else
  printf '\n[3/5] Focused tests skipped.\n'
fi

if ((MOCK_ONLY == 1)); then
  printf '\nMock-only workflow completed successfully.\n'
  exit 0
fi

case "$MODEL" in
  deepseek/*)
    [[ -n "${DEEPSEEK_API_KEY:-}" ]] || die "DEEPSEEK_API_KEY is not set"
    ;;
  openrouter/*)
    [[ -n "${OPENROUTER_API_KEY:-}" ]] || die "OPENROUTER_API_KEY is not set"
    ;;
  gemini/*)
    [[ -n "${GEMINI_API_KEY:-}" ]] || die "GEMINI_API_KEY is not set"
    ;;
  anthropic/*)
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] || die "ANTHROPIC_API_KEY is not set"
    ;;
  openai/*)
    [[ -n "${OPENAI_API_KEY:-}" ]] || die "OPENAI_API_KEY is not set"
    ;;
  bedrock/*)
    [[ -n "${AWS_PROFILE:-}${AWS_ACCESS_KEY_ID:-}" ]] || die "AWS credentials are not configured"
    ;;
  *)
    printf 'WARNING: no credential preflight is defined for model prefix: %s\n' "$MODEL" >&2
    ;;
esac

validate_results() {
  local result_dir="$1"
  local expected_samples="$2"
  uv run python - "$result_dir" "$expected_samples" <<'PY'
import json
import sys
from collections import defaultdict
from pathlib import Path

result_dir = Path(sys.argv[1])
expected_samples = int(sys.argv[2])
records_path = result_dir / "decisions.jsonl"
summary_path = result_dir / "summary.json"

if not records_path.is_file() or not summary_path.is_file():
    raise SystemExit("missing decisions.jsonl or summary.json")

records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
summary = json.loads(summary_path.read_text(encoding="utf-8"))
errors = []
warnings = []

if len(records) != summary.get("record_count"):
    errors.append(f"record count mismatch: JSONL={len(records)}, summary={summary.get('record_count')}")

groups = defaultdict(list)
snapshot_hashes = defaultdict(set)
prompt_hashes = defaultdict(set)
for record in records:
    key = (record["task_id"], record["condition"])
    groups[key].append(record)
    snapshot_hashes[record["task_id"]].add(record["snapshot_sha256"])
    prompt_hashes[key].add(record["prompt_sha256"])
    raw = record["decision"].get("raw_response", "")
    if raw.startswith(("error:", "fallback_noop:")):
        errors.append(
            f"invalid model response: {record['task_id']}/{record['condition']}/"
            f"{record['sample_index']}: {raw[:200]}"
        )
    elif raw.startswith("noop_guard:"):
        warnings.append(
            f"noop guard: {record['task_id']}/{record['condition']}/{record['sample_index']}"
        )
    roles = [message.get("role") for message in record.get("prompt_messages", [])]
    if roles != ["system", "user"]:
        errors.append(f"non-independent prompt history at {record['task_id']}/{record['condition']}")
    if record["condition"] == "blind" and record.get("visible_future_sha256") is not None:
        errors.append(f"blind future hash is non-null for {record['task_id']}")
    if record["condition"] != "blind" and record.get("visible_future_sha256") is None:
        errors.append(f"future hash is null for {record['task_id']}/{record['condition']}")

for key, items in groups.items():
    if len(items) != expected_samples:
        errors.append(f"{key[0]}/{key[1]} has {len(items)} samples, expected {expected_samples}")
    if len(prompt_hashes[key]) != 1:
        errors.append(f"prompt changed within frozen condition {key[0]}/{key[1]}")

for task_id, hashes in snapshot_hashes.items():
    if len(hashes) != 1:
        errors.append(f"snapshot changed across conditions for {task_id}: {sorted(hashes)}")

validation = {
    "valid": not errors,
    "records": len(records),
    "tasks": sorted(snapshot_hashes),
    "conditions": summary.get("conditions", []),
    "errors": errors,
    "warnings": warnings,
}
(result_dir / "validation.json").write_text(
    json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print(json.dumps(validation, ensure_ascii=False, indent=2))
if errors:
    raise SystemExit(1)
PY
}

if ((SKIP_API_SMOKE == 0)); then
  API_SMOKE_DIR="$OUTPUT_ROOT/api-smoke-$TIMESTAMP"
  printf '\n[4/5] Running one-call API smoke test -> %s\n' "$API_SMOKE_DIR"
  uv run python -m experiments.user_simulator_leakage.run \
    --task openclaw-security-reviewer \
    --conditions blind \
    --samples 1 \
    --model "$MODEL" \
    --temperature "$TEMPERATURE" \
    --output "$API_SMOKE_DIR"
  validate_results "$API_SMOKE_DIR" 1
else
  printf '\n[4/5] Real API smoke test skipped.\n'
fi

if [[ -z "$RUN_TAG" ]]; then
  MODEL_TAG="$(printf '%s' "$MODEL" | tr '/: ' '-' | tr -cd '[:alnum:]_.-')"
  RUN_TAG="$MODEL_TAG-$TIMESTAMP"
fi
RUN_DIR="$OUTPUT_ROOT/$RUN_TAG"
[[ ! -e "$RUN_DIR" ]] || die "result directory already exists: $RUN_DIR"
mkdir -p "$RUN_DIR"

{
  printf 'utc_started=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'git_commit=%s\n' "$(git rev-parse HEAD)"
  printf 'git_dirty=%s\n' "$(test -n "$(git status --porcelain)" && echo true || echo false)"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'python=%s\n' "$(uv run python --version 2>&1)"
  printf 'uv=%s\n' "$(uv --version)"
  printf 'model=%s\n' "$MODEL"
  printf 'temperature=%s\n' "$TEMPERATURE"
  printf 'samples_per_condition=%s\n' "$SAMPLES"
  printf 'conditions=%s\n' "$CONDITIONS"
  printf 'tasks=%s\n' "${TASKS[*]:-all}"
} > "$RUN_DIR/run_metadata.txt"

RUN_COMMAND=(
  uv run python -m experiments.user_simulator_leakage.run
  --model "$MODEL"
  --temperature "$TEMPERATURE"
  --samples "$SAMPLES"
  --conditions "$CONDITIONS"
  --output "$RUN_DIR"
)
for task in "${TASKS[@]}"; do
  RUN_COMMAND+=(--task "$task")
done

printf '\n[5/5] Running formal experiment -> %s\n' "$RUN_DIR"
printf 'Model: %s | samples/condition: %s | conditions: %s\n' "$MODEL" "$SAMPLES" "$CONDITIONS"
"${RUN_COMMAND[@]}"

validate_results "$RUN_DIR" "$SAMPLES"
printf 'utc_completed=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_DIR/run_metadata.txt"

printf '\nExperiment completed and validated.\n'
printf 'Summary   : %s/summary.json\n' "$RUN_DIR"
printf 'Decisions : %s/decisions.jsonl\n' "$RUN_DIR"
printf 'Validation: %s/validation.json\n' "$RUN_DIR"
printf 'Metadata  : %s/run_metadata.txt\n' "$RUN_DIR"
