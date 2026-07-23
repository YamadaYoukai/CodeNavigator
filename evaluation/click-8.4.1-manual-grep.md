# Click 8.4.1 人工 `grep` 复核

复核对象固定为 [`pallets/click` 8.4.1 的 commit
`6eeb50e948ea136db145280f6f5dd52eca3fa7e5`](https://github.com/pallets/click/tree/6eeb50e948ea136db145280f6f5dd52eca3fa7e5)。每条金标均在该 checkout 上人工执行 `git grep -n` 或 `git grep -nE` 复核；表中的 `repo`、目标 `path`、可接受行号范围和 `category` 与 `evaluation/cases.jsonl` 一一对应。

无命中样例的可接受行号为 `null`：对应的限定文件中 `grep` 没有输出。这是有意保留的负向金标，而不是遗漏标签。

复核命令的统一形式如下；精确文本使用 `-F`，符号声明等模式使用 `-E`：

```bash
git -C "$REPOSITORY_ROOT/click" grep -nF -- '<literal>' \
  6eeb50e948ea136db145280f6f5dd52eca3fa7e5 -- <path>
git -C "$REPOSITORY_ROOT/click" grep -nE -- '<regex>' \
  6eeb50e948ea136db145280f6f5dd52eca3fa7e5 -- <path>
```

| ID | Category | repo | 目标 path | 可接受行号 | 人工 `grep` 复核 |
| --- | --- | --- | --- | --- | --- |
| `click-error-001` | `error_message` | `click` | `src/click/core.py` | 848 | 精确错误文本位于 `core.py:848`。 |
| `click-symbol-001` | `symbol_search` | `click` | `src/click/core.py` | 163 | `class ParameterSource(` 位于 `core.py:163`。 |
| `click-error-002` | `error_message` | `click` | `src/click/exceptions.py` | 265 | `No such command {name!r}.` 位于 `exceptions.py:265`。 |
| `click-config-001` | `config_key` | `click` | `src/click/core.py` | 312 | `auto_envvar_prefix: str \| None = None` 位于 `core.py:312`。 |
| `click-path-001` | `path_filter` | `click` | `src/click/utils.py` | 349 | `^def get_text_stream` 位于 `utils.py:349`。 |
| `click-language-001` | `language_filter` | `click` | `src/click/globals.py` | 54 | `def resolve_color_default(` 位于 `globals.py:54`。 |
| `click-no-match-001` | `no_match` | `click` | `src/click/core.py` | `null` | 限定文件中不存在 `auto_envvar_suffix`。 |
| `click-no-match-002` | `no_match` | `click` | `src/click/globals.py` | `null` | 限定文件中不存在 `resolve_color_fallback`。 |
| `click-context-start-001` | `context_boundary_start` | `click` | `src/click/globals.py` | 1 | `from __future__ import annotations` 位于 `globals.py:1`。 |
| `click-context-end-001` | `context_boundary_end` | `click` | `src/click/globals.py` | 64 | `if ctx is not None:` 位于 `globals.py:64`。 |
| `click-duplicate-hit-001` | `duplicate_hits` | `click` | `src/click/globals.py` | 54 | `resolve_color_default` 的定义在 `globals.py:54`；同一文本还在 `exceptions.py`、`termui.py`、`utils.py` 中出现。 |
| `click-irrelevant-result-001` | `irrelevant_results` | `click` | `src/click/core.py` | 817–877 | `callback` 的目标是 `Context.invoke` 的 overload/实现区间；同文件更早的文档和注释命中也保留为排序噪声。 |
| `click-unscoped-path-001` | `unscoped_path` | `click` | `src/click/utils.py` | 349 | `get_text_stream` 的定义在 `utils.py:349`；该查询故意不设置 `path`，并保留文档/API 命中。 |
| `click-symbol-candidate-001` | `symbol_candidates` | `click` | `src/click/core.py` | 163 | `ParameterSource` 的类定义在 `core.py:163`，同时存在导出、类型注解和引用候选。 |
| `click-error-text-003` | `error_text` | `click` | `src/click/decorators.py` | 219 | 精确错误文本位于 `decorators.py:219`。 |
| `click-language-path-001` | `language_path_filter` | `click` | `src/click/types.py` | 800 | `class File(` 位于 `types.py:800`，并要求同时满足 Python 和路径过滤。 |
| `click-context-overlong-001` | `context_overlong` | `click` | `src/click/core.py` | 1107 | `def get_help_option(` 位于 `core.py:1107`；目标文件共 3542 行，默认上下文应为 1087–1127 且 `truncated=true`。 |
| `click-duplicate-hit-002` | `duplicate_hits` | `click` | `src/click/exceptions.py` | 65 | `UsageError` 的类定义在 `exceptions.py:65`；导入、捕获、子类和文档引用均保留为重复候选。 |

`baseline-2026-07-23.json` 保留 Zoekt 返回的原始顺序。尤其是两个 `duplicate_hits`、`irrelevant_results` 和 `unscoped_path` 不会因金标位于后续候选而调整查询、过滤条件或结果排序。
