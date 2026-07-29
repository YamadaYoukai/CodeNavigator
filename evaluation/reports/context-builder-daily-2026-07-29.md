# Context Builder 日报（2026-07-29）

## 完成情况

- 新增可独立调用的 `ContextBuilder`：固定输出系统指令、当前任务、`search_code` 与 `get_file_context` 两个 Tool Schema、已有证据和剩余 Tool 调用预算。
- Builder 只做 `ContextState → ModelInput` 的纯转换；不调用模型或工具，不引入 Loop、超时、重试、Memory、RAG、Multi-Agent 或新框架。
- 证据预算使用确定性的条目数，而不是 token 估算；构造上下文不会消耗 `remaining_tool_calls`。
- 设计决策已记录在 `src/examples/code_understanding_agent/DESIGN.md`：优先保留事实证据，同层保留原顺序，最后才丢弃低价值历史。

## 测试

- 新增三类确定性测试：完整上下文字段、预算不足时的事实优先裁剪、相同输入下的输出顺序与无副作用。
- 基线全量回归：49 passed。
- 本次全量回归：52 passed（新增 3 项，执行命令：`PYTHONPATH=. .venv/bin/python -m pytest -q`）。

## 改动边界

- 仅改动 `src/examples/code_understanding_agent/` 的上下文构造与公开导出，并新增对应测试和设计/日报文档。
- 未修改 MCP Server、ToolRouter 的执行路径、Zoekt 调用、评测语料或已有检索排序逻辑。

## 下一步

- 当真正接入模型执行层时，再在独立边界消费该稳定 `ModelInput`；届时需另行评审模型调用、超时和重试策略，不能隐式加入本轮 Builder。
