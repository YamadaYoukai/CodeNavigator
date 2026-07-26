# 2026-07-23 检索失败分析

## 范围与口径

数据源为 `evaluation/reports/baseline-2026-07-23.json`，固定评测集为 18 条：16 条正例、2 条无命中样例。以下“金标排名”按 Zoekt 原始返回顺序、从 1 开始计数；金标为其 `repo`、`path` 与可接受行号范围同时匹配的第一条候选。

- Top-1 未命中：4 条——`click-duplicate-hit-001`、`click-irrelevant-result-001`、`click-unscoped-path-001`、`click-duplicate-hit-002`。
- Hit@5 未命中：3 条——除 `click-unscoped-path-001` 外的其余 3 条。它们是上述 Top-1 未命中的子集，因此共有 4 个不同 case，而非 7 个不同 case。

基线指标（正例分母为 16）：Hit@1 = 12/16（75.00%）；Hit@5 = 13/16（81.25%）；`search_code → get_file_context` 闭环成功率 = 12/16（75.00%）。

## 失败记录

| ID / 未命中指标 | 金标位置 | Top-1 候选 | Top-5 候选（原始顺序） | 金标排名 | 失败类型 | 原因假设 |
| --- | --- | --- | --- | ---: | --- | --- |
| `click-duplicate-hit-001`<br>Top-1、Hit@5 | `src/click/globals.py:54`<br>`def resolve_color_default(...)` | `src/click/utils.py:23`<br>导入 `resolve_color_default` | 1. `utils.py:23` 导入<br>2. `utils.py:323` 调用<br>3. `termui.py:19` 导入<br>4. `termui.py:315` 调用<br>5. `termui.py:530` 调用 | 6 / 8 | `duplicate_hits`（已知优先分类） | 对裸符号字面量，当前原始排序没有把定义行与导入、调用区分开；前 5 个重复引用挤掉了实际定义。 |
| `click-irrelevant-result-001`<br>Top-1、Hit@5 | `src/click/core.py:817–877`<br>`Context.invoke` overload / 实现区间 | `src/click/core.py:148`<br>文档文字中的 `callback evaluation` | 1. `core.py:148` 文档说明<br>2. `core.py:149` 文档 URL<br>3. `core.py:242` 注释 / 说明文字<br>4. `core.py:363` 注释<br>5. `core.py:371` 注释 | 7 / 10<br>首个金标命中为 `core.py:817` | `irrelevant_results`（已知优先分类） | 单词 `callback` 的文本命中把文档、注释排在目标 API 签名之前；排序未利用“处于声明/签名中”的局部代码语境。 |
| `click-unscoped-path-001`<br>仅 Top-1 | `src/click/utils.py:349`<br>`def get_text_stream(...)` | `docs/api.md:128`<br>`.. autofunction:: get_text_stream` | 1. `docs/api.md:128` API 文档<br>2. `CHANGES.rst:1183` 变更记录<br>3. `src/click/utils.py:349` 定义（金标）<br>4. `docs/utils.md:260` 文档<br>5. `docs/utils.md:271` 文档示例 | 3 / 7 | `unscoped_path`（已知优先分类） | 查询未传 `path`，因此文档、变更记录与源码都可召回；原始排序未优先源码中的定义，造成 Top-1 失准，但金标仍保留在 Top-5。 |
| `click-duplicate-hit-002`<br>Top-1、Hit@5 | `src/click/exceptions.py:65`<br>`class UsageError(...)` | `src/click/core.py:33`<br>导入 `UsageError` | 1. `core.py:33` 导入<br>2. `core.py:130` 捕获异常<br>3. `core.py:779` 抛出异常<br>4. `parser.py:38` 导入<br>5. `parser.py:311` 捕获异常 | 10 / 10 | `duplicate_hits`（已知优先分类） | 类名在导入、捕获、抛出和文档引用中高度重复；没有“类定义优先”的排序信号，导致金标落到已返回候选的末位。 |

所有 4 条均成功读取了 Top-1 的文件上下文（`context_ok=true`），但因为闭环以 Top-1 是否为金标为前提，四条的 `closed_loop=false`。这说明当前失败主要是检索结果排序/选择问题，而不是上下文读取或工具错误。

## 唯一的最小改进假设

**仅对“无 `path` 的裸标识符字面量查询”增加一个稳定的声明语境后重排：**在不改变 Zoekt 的召回集合、过滤条件或并列候选相对顺序的前提下，将源码中的符号定义及其声明签名排在导入、普通引用、注释和文档命中之前。

这里“声明签名”包括类/函数定义行以及同一声明的多行参数签名，因此可以覆盖 `UsageError`、`resolve_color_default`、`get_text_stream`，也可验证 `callback` 在 `Context.invoke` overload 签名中的排序是否提升。该假设只改变符合该查询形态的候选顺序，其他查询仍保留 Zoekt 原始顺序。

### 可验证对照

用完全相同的 `evaluation/cases.jsonl`（固定 18 条、同一 Click revision、相同 `limit=10`）分别运行基线与实验版本；正例指标分母继续固定为 16。实验版本至少满足：

| 指标 | 基线 | 不回归门槛 |
| --- | ---: | ---: |
| Hit@1 | 12/16（75.00%） | `>= 12/16`（`>= 75.00%`） |
| Hit@5 | 13/16（81.25%） | `>= 13/16`（`>= 81.25%`） |
| 闭环成功率 | 12/16（75.00%） | `>= 12/16`（`>= 75.00%`） |

验证时还应逐条确认以上 4 个 case 的候选排序变化及两个无命中样例保持正确；后者不进入三个正例指标的分母。只有在上述三项均不回归时，才接受这一最小改动；本报告不提出第二个改进方向。
