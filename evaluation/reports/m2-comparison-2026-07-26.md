# M2 声明语境后重排：冻结基线对比（2026-07-26）

## 结论

在 **2026-07-23 冻结的 18 条 Click 评测集**、相同 Click revision 与可核验的同一份 Click Zoekt 索引快照上，声明语境优先的稳定后重排通过了本轮 M2 验证：四个原始失败 case 的金标都从原始第 3、6、7 或 10 位提升至第 1 位；16 条正例的 Hit@1、Hit@5 和闭环均为 16/16；两个无命中样例仍为 2/2。

这只是一轮固定语料、固定索引的可复核结果，**不能据此提前宣称整体检索质量已经提升**，也不能将未确认 Server commit 下的服务状态变化归因于代码。M2 的受限验证条件已经满足；后续若要作泛化质量结论，仍需重复运行并固定/确认 Server build 信息。

## 变更范围与规则

实现不新增 MCP Tool、RAG、Memory、LangGraph 或其他框架。Zoekt 继续独占召回和过滤；后处理只对已返回候选做稳定分区：声明候选在前，其余候选在后，两个分区内均保留 Zoekt 原始顺序。

规则仅在同时满足以下条件时生效：

- `literal=true`；
- 没有 `path` 过滤；
- 查询为 ASCII 裸标识符（`[A-Za-z_][A-Za-z0-9_]*`）。

Python 候选只在查询标识符本身是函数/类定义、赋值定义，或函数参数声明时提升；导入、调用、注释、文档、变更记录及子类基类引用不会被当作目标声明。源码不可读或不可解析时保留原位置。

## 可复现输入与环境

| 项目 | 冻结/本次记录 |
| --- | --- |
| 固定评测集 | `evaluation/cases.jsonl`，18 条（16 正例、2 无命中），SHA-256 `7c8468dba4d7d4c9f571c0ffa51efbc669185b4ac16511ee3e9b7b4c2ba51be0` |
| 冻结基线 | `evaluation/reports/baseline-2026-07-23.json`，SHA-256 `b90042066948736a32a5ad04bd2bf29b5483bf7f918d6b5e92460c2089f3c352` |
| 本次逐条结果 | `evaluation/reports/post-rerank-2026-07-26.json`，SHA-256 `099546a6f97bda61d4b57739a432a604e834372acf7ecdd2e3a01985b9bc254d` |
| Click checkout | `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`（8.4.1 固定 revision） |
| MCP Server HEAD | `c6705f7d118cc14dc09e94ec4329afb6ae7ade8c`，执行时包含本次未提交的后重排改动 |
| 索引 | `/private/tmp/codex-click-zoekt-index/click_v16.00000.zoekt`，SHA-256 `6bc3d13e5afa7b870c6998baf27c21083b5636959d220bbaee7f82cf0a9fa00e`，文件时间 `2026-07-23T23:58:34+0800` |
| 索引仓库名 | 本地 Click checkout 的 `git config zoekt.name` 为 `click`；`/about` 确认加载 1 个仓库、156 个文档 |
| Zoekt Server | `/private/tmp/codex-zoekt-bin/zoekt-webserver`，`-version` 输出空字符串；`/about` 未暴露 build commit，故 commit **unconfirmed** |
| 本次时间 | `2026-07-26T15:18:53.555377+00:00` 至 `2026-07-26T15:18:54.087413+00:00` |

端口 `6070` 在受限环境中不可用，因此本次用 `127.0.0.1:16070` 启动同一索引快照；端口变化不改变索引内容。执行命令如下：

```bash
/private/tmp/codex-zoekt-bin/zoekt-webserver \
  -rpc -index /private/tmp/codex-click-zoekt-index \
  -listen 127.0.0.1:16070

PYTHONPATH=. .venv/bin/python evaluation/run_eval.py --validate-only
PYTHONPATH=. .venv/bin/python -m pytest -q \
  test/test_declaration_reranker.py test/test_evaluation.py test/test_server.py
PYTHONPATH=. .venv/bin/python -m pytest -q
ZOEKT_URL=http://127.0.0.1:16070 PYTHONPATH=. .venv/bin/python \
  evaluation/run_eval.py --cases evaluation/cases.jsonl \
  --out evaluation/reports/post-rerank-2026-07-26.json
```

数据校验通过；相关测试 23 passed；全量测试 40 passed。

## 召回一致性与适用范围复核

本次报告在每个 case 同时保存 `zoekt_matches`（后重排前）和 `matches`（后重排后）。将本次 18 条 `zoekt_matches` 与冻结基线的原始 `matches` 逐条、逐候选比较，**18/18 完全一致，顺序也一致**。因此没有观察到本轮候选召回集合或原始排序漂移；下表中的变化来自后重排本身。

5 条查询满足窄规则：4 个冻结失败 case 与已正确的 `click-symbol-candidate-001`。后者的金标原本就是第 1 位，未发生排序变化。其余 13 条因有 `path`、非裸标识符、非字面量或无命中而未被该规则改写；两个无命中均仍返回空候选且未读取上下文。

## 指标对比

| 指标 | 07-23 冻结基线 | 本次后重排 | 变化 | 门槛 |
| --- | ---: | ---: | ---: | --- |
| Hit@1（正例） | 12/16 | 16/16 | +4 | 不低于 12/16 |
| Hit@5（正例） | 13/16 | 16/16 | +3 | 不低于 13/16 |
| 上下文读取成功（正例） | 16/16 | 16/16 | 0 | 保持 |
| 闭环成功（正例） | 12/16 | 16/16 | +4 | 不低于 12/16 |
| 无命中正确 | 2/2 | 2/2 | 0 | 必须 2/2 |
| 搜索/上下文错误 | 0/0 | 0/0 | 0 | 保持 0 |

延迟是单次本机观测，不是跨环境 SLA：

| 平均延迟 | 07-23 | 本次 | 变化 |
| --- | ---: | ---: | ---: |
| Zoekt tool | 0.000 ms | 0.333 ms | +0.333 ms |
| `search_code` wall | 19.231 ms | 28.815 ms | +9.584 ms |
| 上下文 wall | 0.628 ms | 0.651 ms | +0.023 ms |
| 闭环 wall | 20.320 ms | 30.650 ms | +10.330 ms |
| 总 wall | 19.810 ms | 29.429 ms | +9.619 ms |

## 四个冻结失败 case 的逐条复核

| Case | 金标原始排名（与 07-23 一致） | 后重排排名 | 结果与排序说明 |
| --- | ---: | ---: | --- |
| `click-duplicate-hit-001` / `resolve_color_default` | 6 | 1 | `globals.py:54` 的 `def resolve_color_default` 前置；后续导入与调用的原相对顺序保留。 |
| `click-irrelevant-result-001` / `callback` | 7 | 1 | `core.py:817` 的 `Context.invoke` 多行参数声明前置；同一原始声明组 `817 → 821 → 824` 保持顺序，文档/注释/调用仍按原序排在其后。 |
| `click-unscoped-path-001` / `get_text_stream` | 3 | 1 | `utils.py:349` 的函数定义前置；`docs/api.md`、`CHANGES.rst` 与其余文档候选仍按原序。 |
| `click-duplicate-hit-002` / `UsageError` | 10 | 1 | `exceptions.py:65` 的 `class UsageError` 前置；导入、捕获、抛出未被误判为声明。子类头部中作为基类引用的 `UsageError` 也不会被提升。 |

所有四条后重排后均为 `status=ok`、Hit@1/Hit@5/上下文/闭环均成功。没有新错误排序：16 个正例均为 `ok`，原来正确的 12 条没有发生后重排，唯一额外符合条件的 `click-symbol-candidate-001` 保持金标第 1 位。

## M2 判定与限制

M2 的实际状态为 **完成**：至少一个目标失败得到可复核改善（实际为 4/4），整体指标无回归，无命中保持 2/2，逐条原始/后重排候选、执行命令、时间、revision、索引条件和测试结果均已保留。

限制仍然有效：Zoekt Server build commit 无法确认，`/about` 也不提供它；本报告只说明本机保留索引快照上的一次可复核回放，不能外推为普遍质量收益，也不把服务侧未验证变化计作代码收益。
