# Click 8.4.1 Agent Holdout 冻结记录 — 2026-08-19

## 结论

新的 Agent holdout 已在任何模型调用前冻结。数据集严格包含 10 条任务，其中 8 条可回答、2 条应以信息不足收口；所有样例固定到 Click revision `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`，ID 与问题均唯一。

- 数据文件：`evaluation/agent_holdout_cases_2026-08-19.jsonl`
- 数据 SHA-256：`e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396`
- 排除集：`evaluation/fixtures/agent-holdout-exclusion-set-2026-08-19.json`
- 排除集 SHA-256：`716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e`
- 独立 Runner：`evaluation/run_agent_holdout.py`
- 冻结日期与人工复核日期：2026-08-19

本次工作没有调用真实模型或执行真实 Agent Eval，没有生成新任务成功率、引用有效率、Trace 或 Tool 指标。旧数据上的真实指标仍是唯一已有指标，不可用于声明新 holdout 表现。

## 开工基线与隔离

开工时主项目 HEAD 为 `19f9bfe63ba655e9ea43d0193efed261bad6d377`，工作树干净。Click checkout 为 detached HEAD `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`，`git config zoekt.name` 为 `click`。

排除集逐条导出了三类既有材料的 ID、原问题或查询及目标位置，并固定各源文件哈希：

| 来源 | 条数 | SHA-256 |
| --- | ---: | --- |
| 原 Agent Eval `evaluation/agent_cases.jsonl` | 10 | `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129` |
| 原检索评测 `evaluation/cases.jsonl` | 18 | `7c8468dba4d7d4c9f571c0ffa51efbc669185b4ac16511ee3e9b7b4c2ba51be0` |
| 08-16 范围引用失败 fixture | 6 | `72c22db0d1bdd466d6a7eb40ce9b7bdc76c937b662214728a6b8b8251198f1b3` |

离线校验会重新计算这三个源文件及排除集自身的哈希，检查 `10/18/6` 覆盖数，并拒绝与排除集重复的 ID、完全相同的问题或相交的正向 gold 行范围。人工复核还确认，新问题不是旧问题的同目标位置改写。

## 人工金标复核

所有正向 gold 均直接阅读固定 revision 源码，并用以下只读命令定位；没有依赖模型生成结论：

```bash
git -C "$REPOSITORY_ROOT/click" grep -nF -- '<symbol>' \
  6eeb50e948ea136db145280f6f5dd52eca3fa7e5 -- <path>
git -C "$REPOSITORY_ROOT/click" show \
  6eeb50e948ea136db145280f6f5dd52eca3fa7e5:<path> | nl -ba
```

| ID | 类别 | `git grep` 定位 | 人工复核后的可接受位置 | 复核事实 |
| --- | --- | --- | --- | --- |
| `holdout-click-unpack-args-001` | 参数解析 | `def _unpack_args(` → `src/click/parser.py:51` | `src/click/parser.py:51-108` | 固定参数位从两端取值，缺项填 `UNSET`；负 `nargs` 标记唯一 wildcard，最终用剩余参数回填并恢复其后结果顺序。 |
| `holdout-click-pass-decorator-001` | Context 行为 | `def make_pass_decorator(` → `src/click/decorators.py:51` | `src/click/decorators.py:51-97` | `ensure=True` 使用 `ensure_object`，否则使用 `find_object`；找不到对象时抛出 `RuntimeError`，找到后通过 `ctx.invoke` 调用。 |
| `holdout-click-datetime-convert-001` | 类型转换 | `class DateTime(` → `src/click/types.py:436` | `src/click/types.py:459-499` | 已是 `datetime` 的值原样返回；配置格式按顺序尝试，全部失败后根据格式数量构造单复数错误文本并调用 `fail`。 |
| `holdout-click-bad-parameter-001` | 异常渲染 | `class BadParameter(` → `src/click/exceptions.py:108` | `src/click/exceptions.py:137-147` | 显式 `param_hint` 优先，其次调用 `param.get_error_hint`；两者都没有时使用无参数名的通用消息。 |
| `holdout-click-write-dl-001` | Help 格式化 | `def write_dl(` → `src/click/formatting.py:224` | `src/click/formatting.py:224-266` | 强制两列；过宽 term 把 description 换到下一行，description 按可用宽度换行，后续行按第二列缩进。 |
| `holdout-click-runner-isolation-001` | 终端交互 | `def isolation(` → `src/click/testing.py:363` | `src/click/testing.py:396-436`、`528-558` | 安装捕获用 stdin/stdout/stderr 与环境覆盖；退出时恢复环境、标准流、prompt/getchar、ANSI 判断、强制宽度和 `pdb` 初始化器。 |
| `holdout-click-editor-selection-001` | 环境变量 | `def get_editor(` → `src/click/_termui_impl.py:653` | `src/click/_termui_impl.py:653-668` | 优先显式 editor，再依次读取 `VISUAL`、`EDITOR`；Windows 回退 `notepad`，Unix 依次探测三个候选，最终回退 `vi`。 |
| `holdout-click-command-main-001` | 命令调用 | `def main(` → runtime overload `src/click/core.py:1377` | `src/click/core.py:1442-1488` | 非 standalone 正常返回 invoke 值并重新抛出 `ClickException`/`Abort`；standalone 显示或转换异常并退出，`Exit` 在非 standalone 返回退出码。 |

两条负向任务使用固定字符串、全仓范围复核：

```bash
git -C "$REPOSITORY_ROOT/click" grep -nF -- 'ClickSessionStore' \
  6eeb50e948ea136db145280f6f5dd52eca3fa7e5 -- .
# 无输出，exit status 1

git -C "$REPOSITORY_ROOT/click" grep -nF -- 'SandboxPermissionPolicy' \
  6eeb50e948ea136db145280f6f5dd52eca3fa7e5 -- .
# 无输出，exit status 1
```

因此两条 gold 均为 `insufficient_evidence`，`locations` 为空，并保存了全仓 absence query 和 scope。

## 离线冻结校验

只运行以下不读取模型凭据、不调用模型、不请求 Zoekt 的入口：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  evaluation/run_agent_holdout.py --validate-only
```

稳定输出为：

```json
{"answerable_cases": 8, "case_count": 10, "click_revision": "6eeb50e948ea136db145280f6f5dd52eca3fa7e5", "dataset_sha256": "e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396", "exclusion_counts": {"agent_cases": 10, "range_citation_failures": 6, "retrieval_cases": 18}, "exclusion_set_sha256": "716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e", "insufficient_evidence_cases": 2, "status": "valid"}
```

真实运行入口只接受上述默认 holdout 文件和冻结哈希，默认输出使用 `agent-holdout-eval-YYYY-MM-DD.json` 与 `agent-holdout-eval-YYYY-MM-DD.md`，不会覆盖原 08-16 报告。真实预检和一次性评测留到后续独立工作日。
