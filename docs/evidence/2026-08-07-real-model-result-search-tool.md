# 2026-08-07 真实 ModelResult → search_code → ToolResult 证据

## 结论

2026-08-07 将一份已持久化、已通过脱敏断言的真实模型 `ModelResult` 继续送入
`ToolStepExecutor`，真实调用 `src.server.search_code` 和本机 Zoekt。精确仓库别名
`pallets/click` 在执行前解析为规范索引名 `click`，Top-1 命中固定为
`click/src/click/core.py:1221`，且该行与 Click 8.4.1 固定 checkout 的源码一致。

为让这一步可以公开复核，仓库现提供：

- [公开脱敏 fixture](../../evaluation/fixtures/real-model-decision-click-replay.json)
- [单步重放 runner](../../evaluation/replay_tool_step.py)

公开 fixture 是从真实 `ModelResult` 及其输入状态按白名单派生的最小输入，不是原始
artifact 的副本。`.artifacts/model-smoke.json` 不提交；模型名、随机标识符、Provider API
地址、密钥、代理、本机路径和 Provider 原始响应均未进入 fixture。

fixture 中的 `call_id` 已明确替换为公开稳定值 `public-click-search-1`。它只用于公开重放中
关联 `ToolCall`、`ToolResult` 和新事实证据，不能被当作或描述成原始 artifact 的
`call_id`。

## 公开重放边界

公开 runner 从已持久化的决策开始，只复现一次
`ToolCall → ToolResult → 下一份 ModelInput`：

- 分别调用 `ContextState.model_validate()` 和
  `ToolCallDecision.model_validate()` 校验公开输入；
- 根据 `repository_hints` 做精确仓库别名解析；
- 注册现有 `search_code` 和 `get_file_context` 两个 Tool，但只执行 fixture 指定的一次
  `search_code`；
- 新建一份公开 Trace，因此 Trace 中恰好只有 `tool_call → tool_result`；
- 不构造模型适配器，不读取模型凭据，不请求模型，也不重试 Tool；
- Tool 失败、fixture 非法、预算或固定命中不符时返回非零退出码，且不输出原始异常。

它不会重新生成原始模型的 `Session → Step → ModelRequest → ModelResult` Trace，也不会
证明公开稳定 `call_id` 与私有 artifact 中随机 ID 的同一性。原始完整 Trace 的历史断言与
公开 Tool 单步重放必须分开理解。

## 环境前提

| 检查项 | 要求或固定值 |
| --- | --- |
| Zoekt URL | `ZOEKT_URL` 指向启用了 `-rpc` 的 Zoekt Web Server；示例为 `http://localhost:6070` |
| Click checkout | 存在目录 `REPOSITORY_ROOT/click` |
| Click 版本 | `8.4.1` |
| Click revision | `6eeb50e948ea136db145280f6f5dd52eca3fa7e5` |
| Zoekt 仓库名 | checkout 中 `git config zoekt.name` 为 `click`，且索引中的仓库名也是 `click` |
| 索引 revision | `6eeb50e948ea136db145280f6f5dd52eca3fa7e5` |
| Zoekt Server 源码 commit | **未知** |

`REPOSITORY_ROOT/click` 的 checkout 必须与索引 revision 一致。不要通过修改 runner 的固定
行号来迁就不同 revision；Zoekt 不可用或 revision 不一致时，应把它记录为环境阻塞。

## 可复制命令

```bash
cd /path/to/mcp-server

export ZOEKT_URL=http://localhost:6070
export REPOSITORY_ROOT=/path/to/public-repos

test "$(git -C "$REPOSITORY_ROOT/click" rev-parse HEAD)" = \
  "6eeb50e948ea136db145280f6f5dd52eca3fa7e5"
test "$(git -C "$REPOSITORY_ROOT/click" config --get zoekt.name)" = "click"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  evaluation/replay_tool_step.py \
  --fixture evaluation/fixtures/real-model-decision-click-replay.json
```

这条命令不需要任何模型环境变量。CLI 主入口才绑定真实的
`src.server.search_code` / `src.server.get_file_context`；单元测试向同一个 runner 注入假的
`search_code`，不会把 Fake Tool 输出作为真实证据。

## 实际重放输出

2026-08-08 使用固定 checkout 和只包含该 Click 索引的本地 Zoekt，再次执行上述 runner。
最终 stdout 是单一白名单 JSON 对象：

```json
{
  "model_repo": "pallets/click",
  "executed_repo": "click",
  "trace_suffix": [
    "tool_call",
    "tool_result"
  ],
  "budget": {
    "before": 1,
    "after": 0
  },
  "tool_result": {
    "status": "success"
  },
  "top_match": {
    "repo": "click",
    "path": "src/click/core.py",
    "line": 1221,
    "snippet": "def make_context("
  },
  "next_model_input": {
    "remaining_tool_calls": 0,
    "new_fact_evidence": {
      "kind": "fact",
      "source": "tool_result:search_code:public-click-search-1",
      "content": "{\"result\":{\"duration_ms\":0,\"matches\":[{\"line\":1221,\"path\":\"src/click/core.py\",\"repo\":\"click\",\"snippet\":\"def make_context(\"}],\"query\":\"def make_context\"},\"status\":\"success\"}"
    }
  }
}
```

关键字段可读为：

```text
model repo     = pallets/click
executed repo  = click
budget         = 1 -> 0
status         = success
top match      = click/src/click/core.py:1221
trace suffix   = tool_call -> tool_result
```

`ModelResult`/fixture 保留模型给出的原始别名，公开 Trace 中的 `ToolCall` 只记录真正发送给
Tool 的规范仓库名。因此既能审计原始选择，也不会把已知别名表现为一次“成功但零命中”的
搜索。`ToolResult` 的完整成功结果被编码为 `kind=fact` 的新证据，并出现在下一份
`ModelInput` 中；下一份输入的 Tool 预算保持为 `0`。

## Click revision 与索引复核

| 检查项 | 白名单结果 |
| --- | --- |
| 本地 checkout | `6eeb50e948ea136db145280f6f5dd52eca3fa7e5` |
| checkout 状态 | 无本地修改 |
| `git config zoekt.name` | `click` |
| Zoekt 搜索响应 `Repository` | `click` |
| Zoekt 搜索响应 `Version` | `6eeb50e948ea136db145280f6f5dd52eca3fa7e5` |
| checkout 与索引 revision | 相等 |
| 索引文件 | `click_v16.00000.zoekt`，`4,436,361` bytes |
| 索引 SHA-256 | `9855e8f5229b754298ea7e8dc35b4a94a72cd1a396d4a6366832eccef7834fce` |
| Zoekt Server 源码 commit | **未知** |

索引 revision 来自原始 Zoekt 搜索响应中的 `FileMatch.Version`，并与 checkout 的
`git rev-parse HEAD` 独立比较。Zoekt `/about` 没有提供可归因的 Server 源码 commit，
因此这里继续保持为“未知”，不根据模块元数据、下载引用或伪版本后缀推断。

## 回归结果

同一工作树最新完整测试结果为 `121 passed`。其中新增 runner 测试为 `6 passed`，覆盖：

- fixture 的完整字段与 `extra="forbid"` 边界；
- `pallets/click → click` 且原始 decision 不变；
- backing Tool 只调用一次、预算 `1 → 0`；
- Trace 恰好为 `tool_call → tool_result`；
- `ToolResult` 成为下一份 `ModelInput` 的事实证据；
- 公开报告只包含显式白名单字段。
