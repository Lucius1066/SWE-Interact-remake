# 任务选择说明：9 个冻结上下文决策点

本文件记录 9 个任务的来源、真伪边界与被放弃的候选，便于后续审计和复现。
所有任务都来自仓库 `tasks/<name>/original_session.json` 中真实存在的 Session；本
实验从不修改 `original_session.json`。

## 1. 选取标准

1. 决策节点之前存在足以理解当前决策的对话与 Agent 状态；
2. 决策节点处有一条真实用户消息（"Future A"），内容具体、可执行，不是
   `yes` / `continue` / 状态查询 / 纯 bug 复现；
3. Future B 是人工构造的反事实，与 A 方向互斥，并且**同样要求当前发言**，避免
   "B = 保持现状 = no-op" 的不对称；
4. 冻结快照本身不包含未来需求原文；
5. 方向可以用少量预先写好的正则审计，不需要语义分类器；
6. 覆盖架构选择、API/行为选择、测试/兼容性/流程策略等不同类别。

## 2. 任务清单

| # | 任务 id | 来源 task / session | message index | 类别 | Future A（真实） | Future B（合成） |
|---|---|---|---|---|---|---|
| 1 | `lumina-newbie-merge-into-existing-classes` | `comfyui-newbie-lumina-refactor` / `d3a75944-9c4f-4769-92e9-636eeb172bb7` | 71 | 架构归属 | 把 `NewBieNextDiT`/`NewBieImage` 的特性合并进 `NextDiT`/`Lumina2`，不要新建类 | 保留 NewBie 独立架构模块，不在共享类里加分支/开关 |
| 2 | `anonymizer-regex-compile-vs-lazy` | `dataclaw-anonymizer-tests` / `ses_3630c353bffeKbS5sfGjwToZGP` | 31 | 性能策略 | 预编译并复用 anonymizer 正则 | 保留原始模式并按次惰性处理 |
| 3 | `tree-diff-blob-hash-vs-content-cache` | `cli-fix-2026-0` / `2026-01-26-4fec7ea0-3335-43ec-a178-d4ab47d5aef3` | 158 | Git 性能 | 用 blob hash/object id 判断变化 | 禁止 hash，缓存并复用实际文件内容 |
| 4 | `parallel-tool-production-vs-unit-test` | `pi-mono-parallel-tool-stall` / `976d9c32-9767-4617-9b72-fc351c99b2b7` | 114 | 测试策略 | 用生产 Session 路径、尽量少 mock 重写失败测试 | 保留隔离 scheduler 单测，另测 runtime wiring |
| 5 | `triton-amd-compiler-vs-runtime-fallback` | `comfyui-triton-windows-amd-fix` / `167b3c69-fb33-43fb-80ff-367a81c81ce4` | 6 | 修复边界 | 在 Triton repo 修 AMD/Windows 编译器问题 | 不改 Triton，在 caller 对 Windows+AMD 走安全回退 |
| 6 | `restore-unknown-agent-skip-vs-abort` | `cli-task-aa4038` / `aa4038a5-34b6-41e4-8541-9cca654dcbc5` | 192 | 错误语义 | warning 后跳过该 session，继续恢复其余 session | 写文件前整体失败，保证原子性 |
| 7 | `openclaw-security-reviewer` | `openclaw-security-review-flow` | 75 | 安全审查 | 引入第二个 LLM reviewer | 保持本地确定性规则与人工批准 |
| 8 | `pi-mono-extension-to-core` | `pi-mono-auto-cbb62cbe` | 89 | 代码归属 | 移入 core 并接入模型选择 | 保持可热加载的 extension-only 实现 |
| 9 | `entire-protected-dirs` | `cli-task-2a55af` | 219、223 | 配置归属 | 由各 Agent 声明 protected paths | rewind 包保留 Claude/Gemini 静态 allowlist |

每个任务都有不进入模型提示的 `counterfactual` 审计块，明确唯一决策轴、A/B
立场和互斥理由。首轮扩展中的 4 个弱任务已经替换：UUID 两个条件实际上都拒绝
过滤；regularization 两个条件都可归结为“先过滤再注册”；skills 的共享 helper
可能同时满足真实要求；shadow 的冻结前缀已明显预告 prompt 处理方向。

## 3. 真实/合成边界

- **Future A**：`futures.future_a.provenance = "real"`，文本为 decision node 处
  用户消息的原文，`source_message` 记录精确的 message index。测试
  `test_future_a_texts_match_the_exact_recorded_real_messages` 会按
  `source.future_a_message_indices` 精确读取消息、核对 session id，并做全文相等
  比较，防止“在 node 后找三条消息”的宽松校验掩盖漂移。
- **Future B**：`futures.future_b.provenance = "synthetic"`，
  `source_message` 明确写出 "no such message exists in the original session"。
  合成内容只借用当前分支的真实代码状态（类名、函数名、文件名），不引用原始
  Session 中不存在的用户原话。
- `entire-protected-dirs` 的 Future A 合并两条真实消息（219 与 223）；这两个非连续
  索引被显式记录，不再依赖模糊的向后扫描。

## 4. 上下文质量声明

`source.decision_context_status` 逐任务说明冻结上下文是否为可解释的决策前状态。
当前 9 个任务都没有在 node 前宣布 Future A 或 Future B 的最终答案；某些上下文会
暴露现状或失败点（例如测试绕过 `sdk.ts`、restore 正在 fallback），这是形成决策
所需的公开状态，不是未来需求。

## 5. 被放弃的候选及原因

| 候选 | 来源 | 放弃原因 |
|---|---|---|
| 非 UUID session 过滤 | `cli-task-b3d2dd` message 66 | 首版 A/B 都要求不做 UUID 过滤，方向并不互斥；且冻结前缀已复述过滤计划 |
| regularization 两阶段注册 | `sd-scripts-reg-image-dedup` message 33 | 两个条件最终都可实现为“过滤后只注册一次”，无法可靠区分 |
| skills loader ignore 复用 | `pi-mono-auto-0467b78e` message 16 | “像 resolver 一样工作”足够宽，抽共享 helper 可能同时满足 A/B |
| shadow prompt carry-over | `cli-task-2f5833` message 163 | public request 已把 `prompt.txt` 定义为 source of truth，前缀对保留方向有明显倡导 |
| `GetWorktreePath` 缓存一致性 | `cli-task-408b8c` message 135 | 冻结上下文里 agent 已在 119 明确说出"delegate to `paths.RepoRoot()`"，等于给出答案；换 node 又无法形成真正的二选一 |
| 用 external type 实现 cursor agent | `cli-task-b12319` message 248 | 会话在 node 之后被用户中断，不存在真实的后续用户消息，无法提供 real Future A |
| Gemini session 路径格式 | `cli-task-aa4038` message 286 | node 之前的 agent 回复里已列出 `projectHash`、`session-<startTime>-<id>` 的推导方式，等于泄露 Future A 的关键内容 |
| skills.ts 与 package-manager 的 ignore 复用 | `pi-mono-auto-0467b78e`（最终采用） | 已采用；此处保留说明：`instructions.md` 要求"检查文档是否需要更新"，属于无关的 PR 审查要求，不影响方向 |
| 测试是否值得补 / PR 提交顺序 | `cli-task-cd4662` message 103、`cli-task-c01017` message 415 | A 依赖 agent 刚刚提出的建议，B 容易退化为"什么都不做"，不满足"两个方向都要求发言" |
| 回退 fix commit + 记入 KNOWN_LIMITATIONS | `cli-task-c01017` message 514 | 该要求写在任务 `instruction.md` 里，也就是 public request 本身，无法作为"未来"信息 |
| lock 协调器 `_async_setup` 拆分 | `lock-code-manager-fix-7c955a` message 250 | 相反方向（不拆方法）几乎等于 no-op；同方向的"提取到函数"缺少可审计的分歧点 |
| `Setup_complete` 改公开 API | `lock-code-manager-fix-7c955a` message 196 | 同一会话 message 250 已包含后续决定，且 directions 只能表述为"改/不改"，B 退化为 no-op |
| 注释/命名清理类请求 | `cli-task-2c3e30`、`cli-task-19def0` 等 | 后续消息是命名、注释、PR 描述等元数据修正，难以写出互斥且有意义的 A/B 正则 |
| 界面/排序类请求 | `gemini-voyager-*`、`reigh-*` 等 | 依赖截图、视觉效果与主观审美，冻结文本上下文不足以判断决策 |

## 6. 复现方式

任务文件中的 snapshot 由人工从真实 Session 的可见信息整理而成（对话、agent
状态、最近回复、diff、step、时间）。仓库同时提供四个只读的辅助脚本，用于核对
来源，不参与运行：

```bash
# 查看某个 node 附近的真实消息窗口
uv run python experiments/user_simulator_leakage/_peek_node.py TASK_ID INDEX

# 导出 node 原文、后续用户消息与 oracle turn 区间
uv run python experiments/user_simulator_leakage/_dump_node.py TASK_ID INDEX

# 打印某个 node 的精确用户消息文本
uv run python experiments/user_simulator_leakage/_node_text.py TASK_ID INDEX

# 只读核对每个任务 mock 决策的分类标签
uv run python experiments/user_simulator_leakage/_check_mock_labels.py
```

这些脚本不写任何文件，也不会调用模型 API。
