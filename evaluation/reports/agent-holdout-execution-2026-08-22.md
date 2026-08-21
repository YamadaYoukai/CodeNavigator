# Click Agent Holdout 执行与独立复算说明 — 2026-08-22

## 执行边界

- 08-22 首次预检因环境变量缺失停止时，固定 Runner 没有启动，模型请求为 `0`，唯一真实运行机会尚未消耗。
- 用户随后明确要求补齐环境后继续，并单独授权将冻结的 10 条提示及检索到的公开 Click 8.4.1 代码片段发送到既有 OpenAI-compatible 端点。该授权显式覆盖原运行清单的“同日不修复后继续”停止规则；本说明保留这一流程偏差，不把它写成原清单自然通过。
- 为保持技术边界不变，真实命令从干净的 detached worktree 执行，HEAD 为 `1e8cd6295878461b2d741d72ee3f0187387733fc`，父级技术基线为 `898e158ddc4266dfcf4203dd65976720ed27ef23`；相对技术基线只增加冻结运行清单。当前主分支上的 `f4226d0` 只包含先前预检阻塞说明，没有进入实际 Runner 的代码路径。
- OpenAI 配置复用用户指定的本地 secrets 文件，只检查存在性，不输出凭据或 Base URL。Zoekt 索引由固定 Click revision `6eeb50e948ea136db145280f6f5dd52eca3fa7e5` 重新生成；checkout、`zoekt.name=click`、声明 revision、18 条检索数据契约及两个正向/一个负向内容探针均在第一次模型请求前通过。
- 两个固定输出路径在启动前均不存在。固定真实命令只启动一次；SDK `max_retries=0`，没有 best-of、第二轮或 `--cases` 变体。

## 唯一真实运行

- 实际运行时间：`2026-08-21T17:13:30.602519+00:00` 至 `2026-08-21T17:19:04.092774+00:00`，即北京时间 2026-08-22 01:13:30 至 01:19:04。
- 固定产物：`evaluation/reports/agent-holdout-eval-2026-08-20.json` 与 `evaluation/reports/agent-holdout-eval-2026-08-20.md`。文件名继续保留冻结的 08-20 标签，报告内时间使用真实执行时间。
- 10 条按冻结文件顺序各执行一次：任务成功 `9/10`；7 条可回答任务以 `completed` 结束，2 条负向任务以 `insufficient_evidence` 安全结束，`holdout-click-command-main-001` 以 `tool_error` 失败并归类为 `tool_or_service_unavailable`。

## JSON 独立复算

以下数值由 JSON 逐题明细使用独立脚本重新计算，不依赖 Markdown 或 Runner 的汇总布尔值：

- case 数与顺序：`10/10`，ID 唯一且与冻结 JSONL 顺序一致；数据分布仍为 `8/2`，数据和排除集 SHA-256 均与冻结值一致。
- 任务成功：`9/10（90%）`。该指标仍没有预填退出阈值，也不代表回答全文的独立语义 Judge 评分。
- 引用来源有效：`45/45（100%）`，分母非零；其中 `43/45` 同时命中对应 gold 位置。M3 的引用门槛度量是“来自当前任务成功 Tool 事实”，不能把 `43/45` 改写成引用来源有效率。
- Tool 尝试：平均 `4.8`，最大 `6`，分布为 `1×1、2×1、3×1、6×7`；从 Trace 的 `tool_call` 事件重算后与逐题字段一致，全部 `<=6`。
- Trace：`10/10（100%）`。逐题重新核对连续序号、统一 `task_id`、模型请求/结果和 Tool 调用/结果一一关联、唯一终态及终态最后。
- 两条负向 case 均成功，最终原因均为 `insufficient_evidence`，最终证据、提交证据和引用列表均为空，没有伪引用。
- 终止分布：`completed=7`、`insufficient_evidence=2`、`tool_error=1`。失败分类只有 `tool_or_service_unavailable=1`，对应 `holdout-click-command-main-001`。
- 任务延迟：平均 `33337.8 ms`，最小 `5082 ms`，最大 `49551 ms`，总计 `333378 ms`。57 次模型调用总计 `332764 ms`；48 次 Tool 调用总计 `444 ms`。
- 环境预检、3 条索引探针、18 条检索契约、报告结构、Markdown 一致性和脱敏扫描全部通过；报告未包含已配置凭据、Base URL、本机仓库路径或 Provider 原始响应。
- 独立复算得到 `m3_exit_criteria_met=true`，与 Runner verdict 一致。该结论只覆盖固定 Click 8.4.1、Python、单仓库评测，不外推到生产、跨仓库或跨语言质量。

## 离线回归

- 全量测试：`191 passed in 1.08s`。
- 检索数据：18 条校验通过。
- 旧 Agent 数据：10 条校验通过，SHA-256 保持 `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`。
- 新 holdout：10 条、`8/2`、两份固定 SHA-256 和排除覆盖 `10/18/6` 均通过。
- `AgentLoop` / `FinalAnswerCitation` 包级导入、`git diff --check`、`git show --check 1e8cd62` 均通过；复制到主工作树的 JSON/Markdown 与 frozen worktree 原件 SHA-256 完全一致。

## 收口结论

按预先固定的质量退出项，引用来源有效率 `100% >= 95%`、Tool 最大 `6`、Trace `10/10`、两条负向安全拒答，且环境/索引/数据/报告门槛均通过，因此 M3 可由 `90%` 更新为 `100%`。任务成功 `9/10` 和单个 Tool 错误继续作为诚实边界保留，不因 M3 通过而删除或改写。
