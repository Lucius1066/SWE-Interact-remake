# 学院服务器运行指南（Linux / Slurm）

本文用于在学院 Linux 服务器上运行 User Simulator 冻结上下文信息泄露实验。
实验只重放已经保存的 Coding Agent 快照并调用 User Simulator，不会重新启动
Coding Agent，也不需要 Docker、E2B、GPU 或任务镜像。

## 最简一键运行

完成第 2–3 节的代码和 Python 准备后，直接执行：

```bash
chmod +x experiments/user_simulator_leakage/run_server_experiment.sh

./experiments/user_simulator_leakage/run_server_experiment.sh --samples 20
```

脚本默认使用 `deepseek/deepseek-flash`，会提示您输入 DeepSeek API Key；输入
过程不回显，Key 只保存在该脚本进程的环境变量中，不会写入文件或日志。随后脚本
依次完成 `uv sync`、Mock、专项测试、一次真实 API 冒烟、正式实验和结果完整性
验证。只验证服务器环境、不调用真实模型时执行：

```bash
./experiments/user_simulator_leakage/run_server_experiment.sh --mock-only
```

下文保留每一步的手工命令，便于排错、审计或编写学院集群的作业脚本。

## 1. 实验规模与资源

默认包含 3 个任务和 3 个条件：

- `blind`：User Simulator 不知道未来需求；
- `future_a`：知道真实 Session 中的后续需求；
- `future_b`：知道人工构造的反事实后续需求。

若 `--samples N`，总模型调用数为：

```text
3 个任务 × 3 个条件 × N = 9N 次调用
```

例如 `--samples 20` 会产生 180 次调用。当前实现按顺序调用模型，推荐资源：

- CPU：1–2 核；
- 内存：2–4 GB；
- GPU：不需要；
- 磁盘：代码约数百 MB，结果通常小于 100 MB；
- Python：严格使用 3.12，项目要求 `>=3.12,<3.13`；
- 网络：计算节点必须能访问所选模型服务商。

## 2. 克隆代码

登录服务器后执行：

```bash
git clone https://github.com/Lucius1066/SWE-Interact-remake.git
cd SWE-Interact-remake
git checkout main
```

如果已经克隆：

```bash
cd /path/to/SWE-Interact-remake
git pull --ff-only
```

以下命令均假定当前目录为仓库根目录。

## 3. 准备 Python 环境

如果服务器使用 Environment Modules，先加载 Python 3.12，例如：

```bash
module avail python
module load python/3.12
python3 --version
```

安装 `uv`。服务器已有 `uv` 时跳过此步；否则可使用学院允许的安装方式，例如：

```bash
python3 -m pip install --user uv
export PATH="$HOME/.local/bin:$PATH"
```

然后在仓库中安装锁定依赖：

```bash
uv sync --frozen
uv run python --version
```

第二条命令应显示 Python 3.12。`uv sync` 会安装本仓库附带的 Harbor，但本实验
不会创建 Harbor sandbox。

## 4. 安全配置 API Key

不要把 Key 写入 Git、作业脚本、命令行参数或 Slurm 日志。建议在仓库外创建：

```bash
mkdir -p "$HOME/.config/swe-leakage"
chmod 700 "$HOME/.config/swe-leakage"
```

直接在交互式终端运行一键脚本时，可以不创建密钥文件：脚本会隐藏输入
`DEEPSEEK_API_KEY`。Slurm 作业没有交互式终端，因此必须使用仓库外的密钥文件。

新建 `$HOME/.config/swe-leakage/deepseek.env`，内容为：

```bash
export DEEPSEEK_API_KEY='替换为真实Key'
```

设置仅本人可读：

```bash
chmod 600 "$HOME/.config/swe-leakage/deepseek.env"
source "$HOME/.config/swe-leakage/deepseek.env"
```

检查变量存在但不要打印 Key：

```bash
test -n "${DEEPSEEK_API_KEY:-}" && echo "DEEPSEEK_API_KEY is set"
```

DeepSeek 官方当前要求 Flash 使用模型名 `deepseek-flash`，Pro 使用
`deepseek-v4-pro`。本项目通过 LiteLLM 调用，因此分别写成
`deepseek/deepseek-flash` 和 `deepseek/deepseek-v4-pro`。旧别名
`deepseek-v4-flash` 虽然仍可调用，但其原模型已经下线，请求会转交当前 Flash
模型处理，因此本脚本不再使用该旧别名。可参阅
[DeepSeek API 文档](https://api-docs.deepseek.com/)。

DeepSeek 当前默认开启 Thinking，但 Thinking 模式不接受本项目 User
Simulator 使用的 `tool_choice="required"`。实验运行器会仅对 `deepseek/*`
模型显式发送 `thinking: {type: disabled}`；其他模型仍使用服务商默认值。
最终采用的模式会写入 `summary.json` 的 `thinking_mode` 字段。这个设置只解决
工具调用兼容性，不改变三种条件下的冻结上下文。

如果改用其他服务商，通过 `--model` 指定模型并导出对应变量，例如
`OPENROUTER_API_KEY`、`GEMINI_API_KEY`、`ANTHROPIC_API_KEY` 或
`OPENAI_API_KEY`。若学院要求代理，可在提交作业前按学院规范设置
`HTTPS_PROXY`；同时确认计算节点而不只是登录节点具有外网访问权限。

## 5. 先运行无 Key 的 Mock

这一步验证环境、三种条件、冻结哈希、规则分类和结果写入，不产生 API 费用：

```bash
mkdir -p server-results

uv run python -m experiments.user_simulator_leakage.run \
  --mock \
  --samples 3 \
  --output server-results/mock-smoke
```

再运行专项测试：

```bash
uv run python -m pytest -q tests/test_user_simulator_leakage.py
```

预期结果是 4 个测试通过，Mock 生成 27 条决策。不要直接运行裸的
`pytest`：仓库内 `external/harbor` 含有其他项目的模板测试，不属于本实验。

## 6. 用一次真实调用检查模型配置

先只运行一个任务、一个条件、一个样本：

```bash
uv run python -m experiments.user_simulator_leakage.run \
  --task openclaw-security-reviewer \
  --conditions blind \
  --samples 1 \
  --model deepseek/deepseek-flash \
  --output server-results/api-smoke
```

检查 `server-results/api-smoke/summary.json` 和
`server-results/api-smoke/decisions.jsonl`。这一步确认：

- Key、模型名和服务器网络可用；
- 模型支持 SWE-Together 使用的 tool calling；
- 输出不是由错误降级生成的 `no-op`。

User Simulator 会把 API 异常转换成 `no-op` 并在 `raw_response` 中保存错误，
所以“命令成功退出”不等于所有 API 调用都成功。必须执行：

```bash
jq -r '
  select(.decision.raw_response | startswith("error:")) |
  [.task_id, .condition, .sample_index, .decision.raw_response] | @tsv
' server-results/api-smoke/decisions.jsonl
```

没有输出才表示未记录到 API 错误。

如果看到 LiteLLM 无法通过 SOCKS 下载远程 model cost map、随后
`Falling back to local backup` 的警告，可以忽略：它只影响可选的远程价格表，
不会使调用失败。若紧接着出现
`Thinking mode does not support this tool_choice`，说明使用的是尚未包含上述修复
的旧代码；执行 `git pull` 后重新运行即可。失败的 smoke 目录应保留作诊断，
正式实验不会在 smoke 校验失败后继续执行。

## 7. 正式运行

为每次实验使用新的输出目录，不要覆盖旧结果：

```bash
RUN_TAG="deepseek-$(date +%Y%m%d-%H%M%S)"

uv run python -m experiments.user_simulator_leakage.run \
  --model deepseek/deepseek-flash \
  --temperature 0.8 \
  --samples 20 \
  --conditions blind,future_a,future_b \
  --output "server-results/$RUN_TAG"
```

建议先用 `N=5` 做 pilot，再决定正式实验使用 `N=20`、`N=30` 或更大样本。
不同模型和不同重复实验应使用独立目录，例如：

```text
server-results/
├── deepseek-r1/
├── deepseek-r2/
├── another-model-r1/
└── mock-smoke/
```

当前运行器在整次运行结束时才写出 `decisions.jsonl` 和 `summary.json`。因此正式
运行必须申请足够 walltime；作业中断时请用新目录重新运行，不要把不完整运行当作
有效重复实验。

## 8. 使用 Slurm 提交

先创建日志目录：

```bash
mkdir -p logs server-results
```

新建一个不包含 API Key 的作业脚本，例如 `run_leakage.slurm`：

```bash
#!/usr/bin/env bash
#SBATCH --job-name=swt-leakage
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=08:00:00
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail

# 按学院环境修改下面两行。
module load python/3.12
cd /absolute/path/to/SWE-Interact-remake

source "$HOME/.config/swe-leakage/deepseek.env"
export PYTHONUNBUFFERED=1

RUN_TAG="deepseek-${SLURM_JOB_ID}"

uv run python -m experiments.user_simulator_leakage.run \
  --model deepseek/deepseek-flash \
  --temperature 0.8 \
  --samples 20 \
  --conditions blind,future_a,future_b \
  --output "server-results/$RUN_TAG"
```

提交与观察：

```bash
sbatch run_leakage.slurm
squeue -u "$USER"
tail -f logs/swt-leakage-JOB_ID.out
```

如果服务器没有 Slurm，可在 `tmux` 中运行第 7 节的正式命令：

```bash
tmux new -s swt-leakage
# 在 tmux 内执行正式运行命令；Ctrl-b d 可退出而不终止任务。
tmux attach -t swt-leakage
```

## 9. 输出文件

每个输出目录包含：

### `summary.json`

保存每个任务、每种条件下的 `A`、`B`、`Other` 数量，例如：

```bash
jq '.distributions' server-results/RUN_TAG/summary.json
```

### `decisions.jsonl`

每行是一次独立采样，包含：

- `task_id`、`condition`、`sample_index`；
- 结构化 `action`、`content` 和完整 `raw_response`；
- A/B/Other 分类与命中的正则证据；
- 完整模型 prompt；
- `snapshot_sha256`、`prompt_sha256`；
- 当前条件可见未来需求的哈希。

快速查看决策：

```bash
jq -r '
  [.task_id, .condition, .sample_index,
   .decision.action, .classification.label, .decision.content] | @tsv
' server-results/RUN_TAG/decisions.jsonl | less -S
```

统计模型调用错误：

```bash
jq -s '[.[] | select(.decision.raw_response | startswith("error:"))] | length' \
  server-results/RUN_TAG/decisions.jsonl
```

结果必须为 `0`。否则该次运行包含 API 失败，不应直接用于比较分布。

## 10. 冻结与泄露检查

同一任务在三种条件下的 `snapshot_sha256` 必须完全相同：

```bash
jq -r '[.task_id, .condition, .snapshot_sha256] | @tsv' \
  server-results/RUN_TAG/decisions.jsonl | sort -u
```

每个任务应该只有一个 snapshot 哈希，只是同一哈希分别出现在三个条件中。

检查各条件 prompt 是否按预期包含未来知识：

```bash
jq -r '
  select(.sample_index == 0) |
  [.task_id, .condition, .prompt_sha256] | @tsv
' server-results/RUN_TAG/decisions.jsonl
```

三种条件的 prompt 哈希应不同，而同一条件内重复样本的 prompt 哈希应相同。模型
输出可能不同，但输入被冻结。任务加载器和 Mock 还会验证 Blind 不包含 A/B、
Future A 不包含 B、Future B 不包含 A。

## 11. 推荐的正式实验纪律

为了让结果可比较：

1. 同一批比较使用完全相同的模型名、temperature 和代码 commit；
2. Blind、Future A、Future B 使用相同样本数；
3. 保存 `git rev-parse HEAD`、模型名、运行日期和服务器环境；
4. 不要手工修改 `decisions.jsonl`；分析时保留原始输出；
5. 将 `Other` 保留为独立类别，不要事后强行归入 A 或 B；
6. 发现 API 错误、限流或不支持 tool calling 时，整次重复应单独标记并重跑；
7. Mock 分布是管线测试数据，不是研究结论。

记录当前代码版本：

```bash
git rev-parse HEAD > "server-results/$RUN_TAG/commit.txt"
uv run python --version > "server-results/$RUN_TAG/python-version.txt"
```

## 12. 常见问题

### `No module named ...`

确认使用的是 `uv run python`，并重新执行：

```bash
uv sync --frozen
```

### `requires-python` 或 Python 版本错误

该仓库要求 Python 3.12，不支持 3.11、3.13 或 3.14。重新加载正确模块后删除
并重建 `.venv`：

```bash
rm -rf .venv
uv sync --frozen
```

执行删除前请确认当前目录确实是本仓库根目录。

### 认证失败或 401/403

确认作业节点中已 `source` 密钥文件，并检查变量是否非空。不要在日志中打印 Key。

### 429 或速率限制

降低样本数，等待额度恢复后使用新的输出目录重跑。当前运行器是顺序调用，不是并发
洪泛；持续出现 429 通常是账号额度或服务商限制。

### 模型总是得到 `no-op`

先检查 `raw_response` 是否以 `error:`、`fallback_noop:` 或 `noop_guard:` 开头。
如果没有错误，再检查原始 prompt 和模型是否稳定支持 required tool calling。

### 计算节点没有外网

Mock 仍可运行，但真实实验不能运行。需要按学院规定申请可访问模型 API 的队列、
代理或出口节点；本实验不应通过登录节点长期运行来绕过集群策略。
