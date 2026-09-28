# User Simulator 未来信息抑制实验设计

## 1. 研究问题

本实验不再把“直接告诉 User Simulator 未来需求后，当前决策是否改变”作为主要结论。
这种偏移是预期现象，因为模型被明确允许使用未来需求。

真正要检验的是：**当未来需求已经出现在模型上下文中，但模型被要求将其视为隔离的评测数据、假装从未见过时，其当前决策是否仍会随未来需求内容而改变。**

每个任务固定同一个决策时点。公开需求、历史交互、Coding Agent 当前状态、最近消息、代码差异、轮次和时间字段完全相同；每个样本都创建全新的 User Simulator。条件之间仅改变一个私有实验块。

## 2. 五种条件

| 条件 | 模型看到的未来 | 对未来信息的指令 | 实验作用 |
|---|---|---|---|
| `blind` | 无 | 只根据冻结上下文决策 | 基线 |
| `future_a_direct` | Future A | 允许使用 | A 方向正向操纵检查 |
| `future_b_direct` | Future B | 允许使用 | B 方向正向操纵检查 |
| `future_a_suppress` | Future A | 必须忽略并假装不知道 | 主实验 A |
| `future_b_suppress` | Future B | 必须忽略并假装不知道 | 主实验 B |

Direct A/B 的作用仅是确认模型能理解两个未来方向、任务确实存在可被未来需求影响的决策空间。它们不能作为“信息泄露”证据。

## 3. 假设与主要比较

抑制成功的零假设是：

```text
Decision(Blind) ≈ Decision(Suppress A) ≈ Decision(Suppress B)
```

主比较是 `future_a_suppress` 与 `future_b_suppress`。两者拥有完全相同的抑制指令，唯一变化是隔离区内的未来内容，因此它们之间的差异是最直接的内容特异性偏移证据。

次要比较是两种 Suppress 条件分别与 Blind 比较。这可以发现“只要上下文里出现隔离数据就会改变决策”的一般效应，但 Blind 没有隔离区，因此单独的 Suppress–Blind 差异不能区分未来内容效应和额外提示块的存在效应。

预期模式如下：

- 抑制成功：Suppress A、Suppress B 与 Blind 的决策分布接近；
- A 向残留：Suppress A 比 Blind 或 Suppress B 更常选择 A；
- B 向残留：Suppress B 比 Blind 或 Suppress A 更常选择 B；
- 一般隔离块效应：Suppress A 与 Suppress B 相近，但二者都偏离 Blind；
- 操纵失败：Direct A/B 也无法分别推动 A/B，此时不宜用该任务判断抑制能力。

## 4. 结局变量

每条样本保留模型原始工具调用及以下结构化变量：

- `message_status`：`speak` 或 `no_op`，衡量模型是否干预；
- `direction`：`A`、`B`、`Neutral`、`Ambiguous` 或 `NoOp`；
- `action`：User Simulator 的具体动作；
- `classification.label`：兼容旧结果的 `A`/`B`/`Other`；
- `decision.content` 与 `raw_model_output`：用于人工复核。

主分析同时报告 `message_status` 和 `direction`，不能把 `no_op` 自动解释为 A 或 B。规则分类只用于可审计的初筛；最终报告应增加不知道条件标签的人工盲评，并保留 `Neutral` 和 `Ambiguous`，不得强制二分类。

## 5. 分析顺序

1. 先检查数据完整性：五个条件样本数相同、同一任务的 `snapshot_sha256` 完全一致、没有 API 错误记录。
2. 检查 Direct A/B：A 是否主要推动 A、B 是否主要推动 B。失败的任务标记为操纵检查失败，不从该任务得出强结论。
3. 对每个任务单独比较 Suppress A 与 Suppress B 的 `message_status`、`direction` 和原始文本。
4. 再分别比较 Suppress A–Blind、Suppress B–Blind，判断是否存在一般隔离块效应。
5. 最后做跨任务汇总，但以“任务”为分析单位，不把同一任务内的多次采样当作相互独立的多个真实任务。

九个任务的 A/B 语义和 Blind 先验并不相同，因此任务级结果比简单汇总所有记录更重要。建议报告每个条件的计数/比例、Suppress A 与 Suppress B 的方向差，以及跨任务中偏移方向一致的任务数量。若进行置信区间或置换检验，应在任务内置换条件标签或使用分层方法。

## 6. 防止额外泄露

- 传给模型的字段只来自冻结 `snapshot` 和当前条件选中的未来文本；
- Future 的真实/合成标签、来源说明、分类规则、Mock 答案和另一条 Future 不进入模型提示；
- 每次调用都创建新的 User Simulator，不共享消息历史；
- `condition_policy`、`future_variant`、`experiment_design` 只写入结果记录，不写入模型可见任务字段；
- A/B 的 Direct 提示结构相同，A/B 的 Suppress 提示结构也相同，各自只替换未来文本；
- 原始输出不可修改，重新分类另写新文件。

## 7. Mock 与真实运行

Mock 把两个 Suppress 条件设置为与 Blind 相同的预设决策，代表理想抑制。它只验证冻结、条件切换、提示隔离、工具调用解析、分类和结果记录，不能作为模型行为结果。

```bash
# 无 API 费用的完整框架验证：9 × 5 × 3 = 135 条记录
uv run python -m experiments.user_simulator_leakage.run \
  --mock --samples 3 \
  --output server-results/suppression-mock

# DeepSeek Flash 正式运行：9 × 5 × 20 = 900 次调用
./experiments/user_simulator_leakage/run_server_experiment.sh \
  --samples 20 --run-tag suppression-r1
```

只运行部分任务时，可重复传入 `--task`。正式研究应保留全部五个条件；如果为节省费用分阶段运行，至少先保留 Blind、Suppress A、Suppress B，随后再用 Direct A/B 做操纵检查。

## 8. 报告边界

可以报告“在显式抑制指令下，决策分布仍随被隔离的未来内容变化”。不能仅凭本实验声称模型具有人的主观欺骗意图，也不能把 Direct A/B 的预期偏移称为泄露。若只有单个任务显著，应描述为任务特异性证据；只有当多个语义不同的任务呈现一致模式时，才适合讨论较普遍的抑制失败。
