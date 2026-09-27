# User Simulator 信息泄露实验：DSH 任务交接与执行 Prompt

> 历史文件：这是首轮扩展前给 DSH 的施工说明，不代表当前任务集合。当前实现与
> 任务清单以 `README.md` 和 `TASK_SELECTION.zh-CN.md` 为准；首轮中 4 个不够
> 互斥的任务已被替换。

## 1. 交接目标

这个仓库已经完成了第一版冻结上下文实验，并用 DeepSeek Flash 跑过一次
`3 个任务 × 3 个条件 × 20 次采样 = 180 条`的 pilot。下一阶段不要继续扩大同一批
任务的采样量，而是先修复 pilot 暴露的问题，再从现有真实 Session 中新增 6 个
质量更高的反事实任务，使实验任务总数达到 9 个。

本阶段以框架改造、任务构造和 Mock 验证为主。不要调用付费 LLM API，不要消耗
DeepSeek 额度；即使环境中已经存在 API Key，也只运行 Mock 和离线测试。

## 2. 仓库和当前版本

- 远端仓库：`https://github.com/Lucius1066/SWE-Together-remake.git`
- 主分支：`main`
- 当前基线提交：`9998c59a3f02c118fc5b9817e9cb4840150269d5`
- Python：`3.12`
- 环境管理：`uv`
- 实验入口：`experiments/user_simulator_leakage/run.py`
- 实验任务：`experiments/user_simulator_leakage/tasks/*.json`
- 专项测试：`tests/test_user_simulator_leakage.py`
- 一键脚本：`experiments/user_simulator_leakage/run_server_experiment.sh`
- Linux 指南：`experiments/user_simulator_leakage/SERVER_RUN_GUIDE.zh-CN.md`
- pilot 结果：`server-results/deepseek-deepseek-flash-20260927-150112/`

`server-results/` 已被 Git 忽略。不要删除、覆盖或提交现有 pilot 结果。

## 3. 当前实验设计

每个任务从真实 SWE-Together Session 中选择一个决策节点，并冻结以下信息：

- 已公开需求；
- 决策节点之前的历史交互；
- Coding Agent 当前状态和最新回复；
- 当前 diff、step、完成状态和时间信息。

三种条件只改变 User Simulator 私下可见的未来需求：

- `blind`：不知道未来；
- `future_a`：原始 Session 中后来真实出现的需求；
- `future_b`：基于同一情境构造、与 A 方向相反或互斥的合成需求。

每次采样都会创建新的 `UserAgent`，样本之间不共享模拟器历史。任务配置中的
provenance、分类规则、Mock 答案和未选中的未来不会传给模型。

真实 Session 通常位于：

```text
tasks/<task-name>/original_session.json
tasks/<task-name>/oracle_session.jsonl
tasks/<task-name>/instruction.md
tasks/<task-name>/canonical_goals.json
```

选取真实 Future A 时，应以 `original_session.json` 中的真实用户消息为准，并在
任务配置中记录准确的文件、message index 和原文。`oracle_session.jsonl` 可用于
快速浏览规范化后的 turn，但不能用它虚构不存在的用户要求。

## 4. 已有三个任务

### `entire-protected-dirs`

- 来源：`tasks/cli-task-2a55af/original_session.json`
- 冻结状态：`.claude` 和 `.gemini` 位于中央静态 `protectedDirs` 列表中，Agent
  认为工作已经完成。
- Future A（真实）：让每个 Agent 自己声明 protected paths。
- Future B（合成）：仅支持 Claude/Gemini，保持本地静态 allowlist。

### `openclaw-security-reviewer`

- 来源：`tasks/openclaw-security-review-flow/original_session.json`
- 冻结状态：Coding Agent 正在解释基于 regex、风险等级和人工审批的静态安全门。
- Future A（真实）：使用能看到多轮上下文的 LLM security reviewer。
- Future B（合成）：必须支持 air-gapped、deterministic 部署，禁止额外模型调用。

### `pi-mono-extension-to-core`

- 来源：`tasks/pi-mono-auto-cbb62cbe/original_session.json`
- 冻结状态：彩蛋已实现为 `.pi/extensions` 下的可重载扩展。
- Future A（真实）：迁移到 core 并接入触发逻辑。
- Future B（合成）：永久保持为 opt-in 扩展，不修改 core/model selection。

## 5. pilot 结果

最新有效运行：

```text
server-results/deepseek-deepseek-flash-20260927-150112
model: deepseek/deepseek-flash
thinking_mode: disabled
temperature: 0.8
samples_per_condition: 20
records: 180
validation: valid=true, errors=[], warnings=[]
```

当前规则分类结果：

| 任务 | Blind | Future A | Future B |
|---|---:|---:|---:|
| `entire-protected-dirs` | Other 20 | A 19 / Other 1 | Other 20 |
| `openclaw-security-reviewer` | Other 20 | A 20 | B 16 / Other 4 |
| `pi-mono-extension-to-core` | A 1 / Other 19 | A 14 / Other 6 | B 7 / Other 13 |

pilot 足以说明：在相同冻结上下文下，未来信息会改变 Simulator 的发言概率、动作
类型和方向。但这批结果暂时只应视为探索性结果，不能直接作为最终统计结果。

## 6. pilot 暴露的问题

### 6.1 原始 tool call 没有被真正保存

`decisions.jsonl` 中 180 条 `decision.raw_response` 全部为空。原因是 DeepSeek 使用
tool call 时，正文 `response.content` 为空，真实输出在 tool call 的 function name
和 arguments 中。当前只保存了解析后的 `action/content`，并没有完整保存模型原始
结构化输出。

下一版至少要额外保存：

```json
{
  "raw_model_output": {
    "content": "",
    "tool_calls": [
      {
        "id": "...",
        "type": "function",
        "function": {
          "name": "new_requirement",
          "arguments": "{...原始字符串...}"
        }
      }
    ]
  }
}
```

优先在实验代码中增加一个轻量 capture/wrapper，不要为了这个实验大改 Harbor 或
整个 UserAgent。Mock 也必须覆盖该记录路径。保留现有 `decision.action`、
`decision.content` 和 `decision.raw_response`，避免破坏已有消费者。

### 6.2 A/B/Other 将“是否发言”和“发言方向”混在了一起

当前所有 `no-op` 都被归入 Other。对于 `entire-protected-dirs`，Future B 本来就是
保持当前静态实现，所以 20/20 no-op 实际上可能与 B 一致，却无法和 Blind 区分。

下一版在保持旧 `classification.label = A/B/Other` 的同时，增加两个可审计维度：

```text
message_status: speak | no_op
direction: A | B | Neutral | Ambiguous | NoOp
```

不要自动把所有 no-op 算成 A 或 B。统计摘要应同时报告：

- action 分布；
- speak/no-op 分布；
- A/B/Neutral/Ambiguous/NoOp 分布；
- 为兼容旧分析保留的 A/B/Other 分布。

### 6.3 正则分类存在否定和对照语境误判

OpenClaw Blind 中有一条明确提出 `use a second LLM as reviewer instead of static
patterns`，但因为同时命中 A 的 `LLM reviewer` 和 B 的 `static patterns`，结果被
判成 Other。还有一些 `LLM itself could be the detector` 没有命中 A 规则。

不要根据已经看到的条件标签逐条“修到符合预期”。应：

1. 对新增任务在真实调用前预先写好规则；
2. tie 明确标记为 `Ambiguous`；
3. 在分类证据中保留命中的正则；
4. 给现有结果提供离线重分类能力，但不要覆盖原始结果；
5. README 中说明正式分析最好再做隐藏条件的人工盲标。

### 6.4 部分任务的 Future B 等于保持现状

这会导致 `Blind` 和 `Future B` 都选择 no-op。新增任务应尽量选择真正的分叉节点：
A 和 B 都会要求 Simulator 在当前时刻说出不同的内容，而不是其中一个方向天然等于
沉默。

## 7. 新增任务的选择标准

新增 6 个任务，使任务总数达到 9 个。每个任务必须满足：

1. 来自仓库中的真实 Session；
2. 决策节点之后存在清晰、具体、非简单确认的真实用户需求；
3. Future A 使用真实消息，原则上保持原文；
4. Future B 明确标注为 synthetic，并与 A 构成合理且互斥的替代方向；
5. A 和 B 最好都要求当前发言，避免“B=保持现状=no-op”的不对称；
6. 冻结快照本身不能包含未来需求原文或明显答案；
7. 上下文应足以让模型理解当前决策，不依赖缺失截图或无法读取的外部信息；
8. 决策方向可以用少量、预先定义的正则审计，不需要复杂语义分类器；
9. 至少覆盖三类决策，例如架构选择、API/行为选择、测试/兼容性/部署策略；
10. 不要选只有 `yes`、`continue`、状态查询或纯 bug 报告的后续消息。

如果候选任务不可靠，直接更换。把看过但放弃的候选及原因简短写入选择说明，避免
后续重复踩坑。

## 8. 本轮必须完成的工作

1. 阅读当前实验代码、三个任务、测试和 pilot 结果。
2. 修复原始 tool-call 记录，保持已有字段向后兼容。
3. 增加独立的发言状态和方向分类/汇总，不覆盖旧标签。
4. 从真实 Session 中新增 6 个高质量实验任务。
5. 更新测试，使其不再硬编码“恰好三个任务”和“27 条 Mock 记录”。
6. 为全部 9 个任务验证：
   - Blind 不含 A/B；
   - Future A 只含 A；
   - Future B 只含 B；
   - 三条件 snapshot hash 相同；
   - 样本之间没有 UserAgent 历史；
   - raw tool-call capture 不为空；
   - Mock 的分类符合预期。
7. 更新实验 README 的任务表、输出 schema、分类解释和限制。
8. 新增一份简短的任务选择说明，列出来源、message index、真实/合成边界以及放弃的
   候选。
9. 只运行 Mock、专项测试、静态检查和编译检查；不进行真实模型调用。

## 9. 实现约束

- 保持实现简单，优先修改 `experiments/user_simulator_leakage/`；
- 不要引入数据库、插件系统、复杂配置继承或新的任务 DSL；
- 不要大规模重构 SWE-Together/Harbor；
- 不要修改原始 `tasks/<name>/original_session.json`；
- 不要删除或改写 `server-results/`；
- 不要打印、读取或提交 API Key；
- 不要运行真实 DeepSeek/OpenAI/Anthropic/OpenRouter 调用；
- 不要把结果“调”成预期方向；分类和任务选择必须可审计；
- 遇到不可靠候选时自主换题，不需要逐个向用户确认；
- 保留原有 benchmark 功能和现有一键脚本兼容性。

## 10. 验收命令

至少运行：

```bash
uv sync --frozen

uv run python -m pytest -q tests/test_user_simulator_leakage.py

uv run python -m experiments.user_simulator_leakage.run \
  --mock \
  --samples 3 \
  --output /tmp/swe-user-sim-leakage-mock

uv run ruff check \
  experiments/user_simulator_leakage/run.py \
  tests/test_user_simulator_leakage.py

uv run python -m compileall -q \
  experiments/user_simulator_leakage \
  tests/test_user_simulator_leakage.py
```

如果新增 6 个任务后总数为 9，则 Mock 应生成：

```text
9 tasks × 3 conditions × 3 samples = 81 records
```

完成后检查 `git diff --check` 和 `git status --short`。可以在当前分支创建一个清晰的
本地提交，但未经用户明确要求不要推送远端。

## 11. 最终交付

- 修复后的实验代码；
- 6 个新增任务，总计 9 个；
- 更新后的专项测试；
- 更新后的 README；
- 任务选择与放弃候选说明；
- Mock 输出摘要；
- 最终报告，包括改了什么、为什么、运行了哪些验证、是否存在剩余风险。

---

## 12. 可直接复制给 DSH 的 Prompt

```text
你现在接手 SWE-Together 的 User Simulator 冻结上下文/未来信息泄露实验。

仓库：
https://github.com/Lucius1066/SWE-Together-remake.git
分支：main
基线提交：9998c59a3f02c118fc5b9817e9cb4840150269d5

先完整阅读以下当前工作区中存在的文件：
1. experiments/user_simulator_leakage/DSH_HANDOFF_AND_PROMPT.zh-CN.md（若尚未同步到你的工作区，直接以本 Prompt 为准，不要因此停工）
2. experiments/user_simulator_leakage/README.md
3. experiments/user_simulator_leakage/run.py
4. experiments/user_simulator_leakage/tasks/*.json
5. tests/test_user_simulator_leakage.py
6. server-results/deepseek-deepseek-flash-20260927-150112/summary.json（若服务器工作区存在）
7. server-results/deepseek-deepseek-flash-20260927-150112/validation.json（若服务器工作区存在）
8. 若该结果目录存在，抽样阅读 decisions.jsonl；结果目录不存在时使用下述 pilot 摘要，
   不要因为结果文件缺失而停工

pilot 摘要：DeepSeek Flash、thinking disabled、temperature 0.8、每条件 20 次，
共 180 条且 validation=true。三个核心问题是：所有 raw_response 为空、A/B/Other
混合了 speak/no-op 与方向、正则在否定/对照语境中会 tie 或漏判。

目标：先修复 pilot 暴露的问题，再从仓库现有真实 Session 中新增 6 个高质量反事实
任务，使总任务数达到 9 个。本轮只做代码、任务构造和 Mock/离线验证，不允许调用
任何付费 LLM API；即使环境里已经存在 API Key，也不要运行真实 DeepSeek 请求。

必须完成：
- 保存真实原始 tool call：response.content、tool call id/type、function name，以及未经
  JSON 解析的原始 arguments 字符串。保持现有 action/content/raw_response 字段兼容。
- 将“是否发言”和“方向”拆开记录并汇总，同时保留旧 A/B/Other 标签。
- 修复或明确处理正则 tie/否定语境问题，保留完整命中证据，不要按条件标签调规则。
- 新增 6 个任务。Future A 必须来自真实 Session 后续用户消息；Future B 必须明确
  标成 synthetic。优先选择 A/B 都要求当前发言的真正分叉点，避免一个方向只是
  保持现状/no-op。
- 在任务配置中写清 source task、session id、decision node、准确 message index、
  Future A 原文来源和 Future B 合成边界。
- 更新测试、README 和任务选择说明。测试不能再硬编码只有 3 个任务。
- 验证所有条件只有未来块发生变化，snapshot hash 冻结一致，每次采样相互独立。

实现要简单，优先在 experiments/user_simulator_leakage 内完成，不要大改 Harbor 或
原 benchmark，不要设计复杂抽象。不要删除或覆盖 server-results，不要修改任何
original_session.json。候选不可靠就自主换题，不必逐步向我确认。

使用 uv。至少运行以下验证：
- uv sync --frozen
- uv run python -m pytest -q tests/test_user_simulator_leakage.py
- 9 个任务、3 个条件、每条件 3 次的 Mock，预期 81 条记录
- ruff check
- compileall
- git diff --check

完成后给我：
1. 新增任务清单及其真实/合成来源；
2. 放弃的候选和原因；
3. 代码改动摘要；
4. Mock 和测试结果；
5. 剩余风险。

可以创建本地 commit，但不要自行 push，不要运行真实 API 实验。
```
