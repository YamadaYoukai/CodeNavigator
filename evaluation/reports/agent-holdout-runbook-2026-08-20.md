# Click 8.4.1 Agent Holdout 唯一运行清单 — 2026-08-20

## 冻结结论

本文档仅冻结 2026-08-20 的预检、唯一真实运行命令、输出路径和停止规则；它不是真实评测报告，不包含任何新质量指标。

2026-08-19 收口时：

- 主项目 HEAD 为 `898e158ddc4266dfcf4203dd65976720ed27ef23`，工作树干净；`evaluation/reports/agent-holdout-eval-2026-08-19.*` 和 `agent-holdout-eval-2026-08-20.*` 均不存在。
- 只执行了 `evaluation/run_agent_holdout.py --validate-only`，返回 `status=valid`：共 `10` 条，其中可回答 `8` 条、应信息不足 `2` 条，Click revision 为 `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`。
- 数据 SHA-256 为 `e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396`，排除集 SHA-256 为 `716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e`，排除覆盖为 Agent/Retrieval/范围引用失败 `10/18/6`。
- 离线校验没有读取模型凭据，没有调用模型或请求 Zoekt，也没有生成真实报告。
- 主项目代码、Prompt、Tool、Runner、数据、gold、检索排序和评分口径均未修改。M3 继续保持 `90%`；唯一可引用的真实指标仍是旧评测的任务 `3/10`、引用 `6/20（30%）`、Trace `10/10`、Tool 最大 `6`。

## 运行前不变式

以下条件必须按顺序全部满足；任一项不满足就不启动 Runner：

1. 主项目仍基于 `898e158ddc4266dfcf4203dd65976720ed27ef23`，冻结后只允许增加本运行清单；若代码、Prompt、Tool、Runner、数据、gold、检索排序或评分口径发生变化，当天停止。
2. 重新执行 `--validate-only` 时，必须仍得到上述 `10`、`8/2`、revision、两份 SHA-256 和 `10/18/6`。任一冻结值变化都停止，不现场修数据或 gold。
3. `REPOSITORY_ROOT/click` 的 checkout HEAD 必须是 `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`，且该 checkout 的 `git config zoekt.name` 必须精确为 `click`。
4. 索引创建方必须确认索引来自同一 revision；`ZOEKT_INDEX_REVISION` 必须声明为 `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`。Zoekt 未暴露 server-native revision，因此这一声明和下述内容探针共同构成索引一致性证据。
5. 只检查以下环境变量是否已配置，不打印值：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`ZOEKT_URL`、`REPOSITORY_ROOT`、`ZOEKT_INDEX_REVISION`。凭据值、Base URL 和本机仓库路径不得进入命令输出、文档或报告。
6. 真实命令固定携带 `--no-proxy-base-url`：它只在当前 Runner 进程中把已配置 Base URL 的主机加入 `NO_PROXY`/`no_proxy`，不打印主机、不修改系统代理。若环境所有者确认该主机必须通过代理访问，则停止本轮，不在现场删除参数。
7. 两个固定输出路径在启动前必须都不存在：`evaluation/reports/agent-holdout-eval-2026-08-20.json` 和 `evaluation/reports/agent-holdout-eval-2026-08-20.md`。任一路径已存在都停止，不删除、改名或覆盖旧证据。

## 固定索引探针

Runner 会在第一次模型请求之前按顺序执行三个探针：

| 探针 | 查询与作用域 | 唯一通过条件 |
| --- | --- | --- |
| `echo-definition` | literal `def echo\(`，仓库 `click`，Python，路径 `src/click/utils\.py` | 候选中存在 `click/src/click/utils.py:234` |
| `choice-message` | literal `def get_missing_message\(`，仓库 `click`，Python，路径 `src/click/types\.py` | 候选中存在 `click/src/click/types.py:358` |
| `negative-symbol` | literal `ClickOptionRegistry`，仓库 `click`，Python，不限路径 | 返回空候选 |

任一探针服务不可用、响应结构无效或内容不匹配都是预检失败。预检还必须确认既有检索数据契约仍为 `18` 条。因本轮不修改检索实现或数据，18 条检索门槛继续引用已冻结的后重排证据：正例 Hit@1、Hit@5、上下文读取和闭环均为 `16/16`，无命中为 `2/2`，搜索/上下文错误为 `0/0`。本轮不额外运行检索评测来制造新基线。

## 2026-08-20 唯一真实命令

环境变量必须在命令外预先配置。除以下命令外，当天不运行其他真实 Agent Eval，不添加 retry、best-of 或 `--cases` 变体：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  evaluation/run_agent_holdout.py \
  --no-proxy-base-url \
  --out evaluation/reports/agent-holdout-eval-2026-08-20.json \
  --summary-out evaluation/reports/agent-holdout-eval-2026-08-20.md
```

“一次真实运行”从第一个 holdout case 发出第一次模型请求开始计算。在此之前的冻结数据、checkout 和索引预检不计为真实运行，但预检一旦失败，当天仍必须停止。

## 停止与验收规则

- **预检失败**：只保留 Runner 输出的稳定 `error_type` 并停止。不绕过检查、不在现场修配置后重跑、不把未发出模型请求的预检失败写成质量指标。
- **模型已开始调用**：从第一次模型请求起，无论后续是模型、Tool、网络、中断或质量失败，都将其视为当天唯一一轮；保留原始稳定事实，不 retry、不 best-of、不运行第二轮。
- **报告写盘**：只接受 Runner 一次性生成的固定 JSON 和 Markdown。先从 JSON 逐题明细重算任务、引用、Trace、Tool 和失败分类，再阅读 Markdown 结论；不手工编辑任一报告。
- **脱敏失败**：若 Runner 输出 `report_sanitization_failed`，立即停止；不生成、不提交人工替代报告，更不提交凭据、Base URL 或本机私有路径。
- **门槛失败**：引用有效率 `<95%`、任一题 Tool `>6`、Trace 不是 `10/10`、任一负向样例未安全以信息不足收口，或既有 18 条检索冻结证据不再可引用，都保持 M3 未完成。只分类并记录失败，不现场调 Prompt、Tool、检索、Runner、gold 或评分口径。
- **门槛通过**：只有引用有效率 `>=95%`、每题 Tool `<=6`、Trace `10/10`、两条负向任务均安全拒答、环境/索引预检全部通过且 18 条检索门槛证据仍有效时，才可把 M3 标记为完成。Runner 的 Markdown verdict 必须与 JSON 复算结果一致，不能仅依据任务成功数或测试数上调进度。

## 唯一下一步

2026-08-20 先完成上述无泄露预检，再执行一次且仅一次固定命令。不提前进入 M4。
