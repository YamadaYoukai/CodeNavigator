# Click 8.4.1 Agent Holdout 预检阻塞记录 — 2026-08-22

## 结论

2026-08-22（UTC+08:00）按冻结清单执行 Gate 0。代码、冻结数据和固定输出路径检查通过，但六个必需环境变量均未配置；因此在启动 Runner、索引探针和第一次模型请求之前停止。

- 分支结论：Gate 3 分支 C（第一次模型请求前预检阻塞）。
- 稳定阻塞类型：`required_environment_missing:OPENAI_API_KEY`。这是 Runner 按固定读取顺序会返回的第一个稳定类型；固定真实命令本次没有执行。
- 发生阶段：Gate 0 环境变量存在性检查，早于 Runner、Zoekt 探针和模型调用。
- 模型请求：`0`；真实 holdout 轮次未开始，也未消耗冻结的一次运行机会。
- 固定 JSON/Markdown：均未生成；没有质量分子、分母、Trace、Tool、终止或延迟新指标。
- M3：保持 `90%`；求职全链路保持约 `53.0%`；M4 保持 `0%`。

## Gate 0 证据

- 主项目 HEAD 为 `1e8cd6295878461b2d741d72ee3f0187387733fc`，父提交为 `898e158ddc4266dfcf4203dd65976720ed27ef23`，开工工作树干净。
- `898e158..1e8cd62` 只新增 `evaluation/reports/agent-holdout-runbook-2026-08-20.md`，没有实现、Prompt、Tool、Runner、数据、gold、检索排序或评分口径漂移。
- 两个冻结输出路径在检查时均不存在，且本次未删除、改名或覆盖旧证据：
  - `evaluation/reports/agent-holdout-eval-2026-08-20.json`
  - `evaluation/reports/agent-holdout-eval-2026-08-20.md`
- `evaluation/run_agent_holdout.py --validate-only` 返回 `status=valid`：`10` 条，分布 `8/2`，Click revision 为 `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`。
- 数据 SHA-256 为 `e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396`；排除集 SHA-256 为 `716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e`；排除覆盖为 `10/18/6`。
- 只检查存在性后，`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`ZOEKT_URL`、`REPOSITORY_ROOT`、`ZOEKT_INDEX_REVISION` 均为缺失；没有读取或输出任何值。
- 因 `REPOSITORY_ROOT` 和 `ZOEKT_INDEX_REVISION` 缺失，Click checkout、`zoekt.name` 和声明索引 revision 只能记为不可核验，不能记为不匹配；三个固定索引探针也没有执行。
- 固定命令要求的 `--no-proxy-base-url` 没有被修改；由于 Gate 0 未通过，固定真实命令没有启动。

## 离线回归

- 全量测试：`191 passed in 1.12s`。
- 检索数据校验：`18` 条通过。
- 旧 Agent 数据校验：`10` 条通过，数据 SHA-256 为 `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`。
- 新 holdout 校验：`10` 条、`8/2` 分布及两份冻结 SHA-256 继续通过。
- `AgentLoop` / `FinalAnswerCitation` 包级导入、`git diff --check` 和 `git show --check 1e8cd62` 均通过。
- 本轮未修改冻结 holdout、排除集、gold、Prompt、Tool、Runner、检索或评分代码，也没有执行 retry、best-of、第二轮或 `--cases` 变体。

## 诚实边界与下一步

旧评测的任务 `3/10`、引用 `6/20（30%）`、Trace `10/10`、Tool 最大 `6` 仍是当前唯一真实质量指标。本次只证明离线冻结契约和回归仍成立，不能据此推断新 holdout 质量，也不能完成 M3。

下一独立工作日只处理相同的环境阻塞，并从同一冻结清单重新判断是否仍有合法的一次运行机会；在 Gate 0 全部通过前不启动 Runner，也不进入 M4。
