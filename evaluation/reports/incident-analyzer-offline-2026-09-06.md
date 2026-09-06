# Incident analyzer 离线边界验收（2026-09-06）

本次从 `102dc29` 新增供应商无关 `IncidentAnalyzer` Protocol、Fake、来源输入投影
和单次公开入口 `analyze_incident()`，离线接入 Gate 通过。该结论限于一个冻结的
Click case 的结构、来源与对象隔离；不代表根因判断、真实模型质量、置信度校准、
失败恢复或五案例退出标准通过。M4 保持 `30%`，总进度保持 `60.5%`。

## 设计与实际行为

调用链与拷贝位置见 [英文设计](../../src/examples/code_understanding_agent/DESIGN.md)。
输入复用 `load_context_replay()` 和 `validate_execution_artifact()`，在 analyzer
调用前要求合法的成功 context。公开输入只有 case ID、原始脱敏 Incident 来源、
代码 citation/snippet；坐标取自已验证 context match，单行片段取自已验证 fixture。
Artifact 未保存完整 context 原文，因此本次不重建或提供完整窗口，不传入 rank、
gold 字段、预期假设或评分信息。

编排将脱离原对象的输入交给 analyzer 一次。返回类型必须为 Candidate；Result
也是非可信 Candidate，不能因为类名而跳过既有 validator。来源由 validator
重新加载，输入对象污染不能更改校验依据。异常不包装成功，不重试。

Fake 构造参数、脚本返回值、内部输入快照、公开快照及可信结果互相隔离。每个
类型正确的 Fake 调用尝试计入 `call_count`，包括耗尽尝试；耗尽报 RuntimeError。
该对象边界不是任意 Python 适配器的进程沙箱。

## 红灯证据

先建立可导入的 API 骨架与来源准备，编排直接透传 Candidate，Fake 尚未实施
深拷贝、类型检查和明确耗尽错误。对该未完成骨架运行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider \
  test/test_incident_analyzer.py
```

实际为 **28 failed, 5 passed**，均完成收集，没有缺模块错误。具体失败包括：

- 正常与证据不足返回了 Candidate，未建立可信 Result（2 项）。
- 错误 case/source/repo/path/line/snippet 分别经 Candidate 与 Result 标签被接受（12 项）。
- 被篡改 Result 的 confidence 被接受（2 项），错误返回类型未拒绝（4 项）。
- Result/脚本/输入快照隔离、Incident/代码输入污染、顺序脚本耗尽、空脚本与类型边界失败（8 项）。

代表性断言为 `Failed: DID NOT RAISE ValueError/TypeError`；脚本耗尽得到错误的
`IndexError: tuple index out of range`。本地保留原始输出、当时 API 骨架和测试快照，
未把临时骨架当作可用实现提交。接入现有输出 gate 并补齐复制后，原 33 项通过；
随后增加来源文件在调用中被替换及 analyzer 收到准备输入的独立副本两项检查，
最终新模块 **35 项**通过。既有 63 项 analysis 契约测试原样保留。

## 验收结果与重跑

从项目根目录执行；所有输出为本次实现后的实测，不沿用开工基线：

| 检查 | 实际结果 |
| --- | --- |
| 开工 analysis 基线 | `63 passed` |
| analyzer + analysis | `98 passed`（35 + 63） |
| 六个 Incident 模块聚焦 | `232 passed` |
| 全量 pytest | `481 passed` |
| 检索 / Agent / holdout 数据 | `18 / 10 / 10`，均 valid |
| context validate-only | valid；本次所有执行计数 0 |
| 旧 analysis runner | 与既有 09-05 JSON 字节一致；8 项输出篡改、2 项标量篡改拒绝 |
| 新 analyzer runner | 正常/信息不足通过；调用后 12 项异常及调用前 3 项篡改均拒绝 |
| 新 runner 确定性 | 两次输出字节一致 |
| 包级公开符号导入 | 通过 |
| 冻结输入与既有报告 | 三份哈希匹配，开工时已有的全部 tracked report 字节未变 |
| Diff 与新增/修改公开内容扫描 | 通过；扫描不覆盖历史与作者元数据 |

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider \
  test/test_incident_analyzer.py test/test_incident_analysis.py \
  test/test_incident_extraction.py test/test_incident_context.py \
  test/test_incident_search_replay.py test/test_incident_context_replay.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python evaluation/run_eval.py --validate-only
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python evaluation/run_agent_eval.py --validate-only
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python evaluation/run_agent_holdout.py --validate-only
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m evaluation.replay_incident_context --validate-only
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m evaluation.validate_incident_analysis
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m evaluation.validate_incident_analyzer
git diff --check
```

独立 runner 未引用测试助手。实际输出见
[`incident-analyzer-offline-2026-09-06.json`](incident-analyzer-offline-2026-09-06.json)。
其中 `fake_calls=16`；自定义适配器分别执行 wrong_type/exception/pollution/capture
各 1 次，capture 内部的 Fake 调用已包含在 16 中，不能重复相加。
`model/search_code/get_file_context/agent_loop/tool_router/network/subprocess` 均为 0；
runner 在这些入口安装 fail-if-called 阻断并核验尝试计数，没有读取凭证或执行真实
Tool。历史 context artifact 的一次 `get_file_context` 不属于本次调用。

## 冻结来源

| 文件 | SHA-256 |
| --- | --- |
| `evaluation/fixtures/incident-click-search-public.json` | `2634a72fca1c8702e0250b8948241ae0d0a6dceaf18e8f6b56039a5734944fe0` |
| `evaluation/reports/incident-search-replay-real-2026-08-30.json` | `41607f1a163440d3b517bb05e637f15dc8c1924b11c26323b9f91f57a5500acf` |
| `evaluation/reports/incident-context-replay-real-2026-09-02.json` | `dae39f4618cb15d0f353db4d5c5bc131b524be2aa226970f1908b4f161646545` |

用户既有中文设计文件未修改，未纳入候选提交；没有 push 或改写历史。
下一步为独立审查本次 analyzer 增量，审查通过后再设计同一案例的真实 analyzer
输入、质量判定与受控调用计划；真实调用范围和预算需另行明确。三类失败注入、
五案例和完整演示仍未完成，不按原 09-06 目标日期自动启动 M5 主线。
