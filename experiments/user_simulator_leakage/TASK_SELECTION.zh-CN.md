# 任务选择说明：9 个冻结上下文决策点

本文件记录本轮新增 6 个任务的来源、真伪边界与被放弃的候选，便于后续审计和复现。
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

## 2. 新增任务清单

| # | 任务 id | 来源 task / session | message index | 类别 | Future A（真实） | Future B（合成） |
|---|---|---|---|---|---|---|
| 1 | `lumina-newbie-merge-into-existing-classes` | `comfyui-newbie-lumina-refactor` / `d3a75944-9c4f-4769-92e9-636eeb172bb7` | 71 | 架构归属 | 把 `NewBieNextDiT`/`NewBieImage` 的特性合并进 `NextDiT`/`Lumina2`，不要新建类 | 保留 NewBie 独立架构模块，不在共享类里加分支/开关 |
| 2 | `skills-loader-reuse-ignore-discovery` | `pi-mono-auto-0467b78e` / `0467b78e-e966-4892-b8d3-5054c5996917` | 16 | 复用与一致性 | 让 `skills.ts` 像 package-manager 的 resolver 一样工作（复用 ignore 规则） | 两个 loader 保持独立，只抽出共享的规则求值 helper |
| 3 | `regularization-balance-single-pass` | `sd-scripts-reg-image-dedup` / `ses_386b6b3f0ffeJdlRfG9K4aiWnO` | 33 | 流程/数据一致性 | 过滤之后再统一注册一次 reg images，去掉两次注册加 rebalance | 把 original-resolution 过滤提前到首次注册之前，让 rebalance 变得不必要 |
| 4 | `multisession-prompt-uuids` | `cli-task-b3d2dd` / `b3d2dd85-97f1-4a42-bb85-d5ddb6f44882` | 66 | 兼容性/防御式校验 | 真实用户原文：对 uuid 过滤持怀疑态度，担心以后被咬 | 不加 id 格式过滤，改为让写入脏 session 的测试自己清理 |
| 5 | `anonymizer-regex-compile-vs-lazy` | `dataclaw-anonymizer-tests` / `ses_3630c353bffeKbS5sfGjwToZGP` | 31 | 性能策略 | 编译 anonymizer 里的正则，并继续找加速点 | 保留可读的模式字符串，改为惰性/增量匿名化 |
| 6 | `shadow-prompt-keep-on-carryover` | `cli-task-2f5833` / `2f5833ec-423b-47b4-9535-556896507b53` | 163 | 数据保留策略 | 存在 carry-over 文件时不要删除 metadata 里的 `prompt.txt` | 只要 session 已 condense 就无条件删除 prompt 副本 |

原有 3 个任务（`openclaw-security-reviewer`、`pi-mono-extension-to-core`、
`entire-protected-dirs`）保持不变，仅在 `source` 中补齐
`decision_message_index` 与 `decision_context_status`。

## 3. 真实/合成边界

- **Future A**：`futures.future_a.provenance = "real"`，文本为 decision node 处
  用户消息的原文，`source_message` 记录精确的 message index。测试
  `test_future_a_texts_match_the_recorded_real_message` 会把任务文件与
  `tasks/<source>/original_session.json` 逐句比对，防止手写漂移。
- **Future B**：`futures.future_b.provenance = "synthetic"`，
  `source_message` 明确写出 "no such message exists in the original session"。
  合成内容只借用当前分支的真实代码状态（类名、函数名、文件名），不引用原始
  Session 中不存在的用户原话。
- `entire-protected-dirs` 的 Future A 合并了连续两条真实消息（219 与 223），
  因此测试按"从 node 起前若干条真实用户消息"做逐句包含校验。

## 4. 上下文质量声明

`source.decision_context_status` 逐任务说明冻结上下文是否"干净"。本轮 9 个任务中
有 7 个是独立的决策前状态，2 个需要分层解读：

- `multisession-prompt-uuids`：agent 在 node 之前复述了仓库 plan，plan 里已经列有
  "skip sessions with non-UUID session_id" 作为待办。也就是说冻结上下文本身偏向
  Future A，而真实用户随后恰好反对该方向。这个任务测的是"模型是否跟着上下文惯性
  走"，分析时必须与其余 7 个任务分层，不能混池。
- `shadow-prompt-keep-on-carryover`：public request 中的 plan 已声明
  `prompt.txt` 是 source of truth，但并没有决定 carry-over 场景；agent 最近可见
  的工作是集成测试的机械改动。

## 5. 被放弃的候选及原因

| 候选 | 来源 | 放弃原因 |
|---|---|---|
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
状态、最近回复、diff、step、时间）。仓库同时提供两个只读的辅助脚本，用于核对
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
