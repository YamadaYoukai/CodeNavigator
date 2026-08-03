# 2026-08-02 真实模型决策 Smoke 证据

## 结论

2026-08-02 针对同一公开 Click 问题执行了两次相互独立的真实模型
Smoke。按目标主机绕过进程继承的 HTTP、HTTPS 和 SOCKS 代理后，两次调用均成功
返回结构合法的 `search_code` 决策，`ModelResult.elapsed_ms` 分别为 `6176` 和
`7706`。两次 Trace 的顺序、请求/结果关联和脱敏断言均一致。

本文只固化可公开的白名单字段，不复制本地 artifact，也不记录凭据、地址、代理值、
原始异常或 Provider 原始响应。

## 公开问题与范围

- 公开问题：`Where is make_context implemented in Click, and what does it do?`
- 模型输入只包含上述公开问题、公开系统指令和两个工具 Schema；`evidence` 为空，未发送
  本地源码。
- 本次 Smoke 的终点是结构化模型决策。2026-08-02 当时尚未执行模型返回的 Tool，
  因而本证据不声称搜索已经命中。

摘录白名单限定为：决策类型、工具名、工具参数、模型耗时、Trace 事件类型与顺序、
标识符之间的关联断言、稳定错误分类、HTTP 状态以及两项脱敏布尔值。各类标识符只记录
相等或不等关系，不公开其原值。

## 结构化决策

第一次成功 Smoke：

```json
{
  "model_elapsed_ms": 6176,
  "decision": {
    "decision_type": "tool_call",
    "tool_name": "search_code",
    "arguments": {
      "query": "def make_context",
      "repo": "pallets/click",
      "lang": "python",
      "path": null,
      "limit": 20,
      "literal": true
    }
  }
}
```

独立验收 Smoke：

```json
{
  "model_elapsed_ms": 7706,
  "decision": {
    "decision_type": "tool_call",
    "tool_name": "search_code",
    "arguments": {
      "query": "make_context",
      "repo": "pallets/click",
      "lang": "python",
      "path": null,
      "limit": 20,
      "literal": true
    }
  }
}
```

## Trace 顺序与关联

两次 Smoke 均记录相同的事件序列：

| `sequence` | `event_type` | 作用 |
| ---: | --- | --- |
| 1 | `session` | 建立任务与可用工具边界 |
| 2 | `step` | 记录本次模型决策步骤 |
| 3 | `model_request` | 记录脱敏后的模型请求边界 |
| 4 | `model_result` | 记录结构化决策及 `elapsed_ms` |

关联关系均通过断言：

- 四个事件具有同一个 `task_id`。
- `ModelRequest.request_id == ModelResult.request_id`，请求与结果一一关联。
- 决策中的 `call_id != request_id`；`call_id` 独立标识后续工具调用，未与模型请求混用。
- Trace 中没有 `tool_call` 或 `tool_result`，与“本次只验证模型决策”的范围一致。

## 代理失败/成功对照

| 场景 | 进程级代理处理 | 白名单结果 |
| --- | --- | --- |
| 绕过前 | 继承系统 HTTP、HTTPS、SOCKS 代理；未对 API 目标主机设置旁路 | 模型边界返回稳定分类 `model_execution_error`；安全诊断为 `InternalServerError`、HTTP `502`；最小公开请求同样为 HTTP `502` |
| 绕过后 | 只在当前进程把 API 目标主机追加到 `NO_PROXY` 和 `no_proxy` | 两次真实模型决策成功，耗时分别为 `6176 ms`、`7706 ms` |

该对照表明失败发生在代理网络路径，而不是工具 Schema 或结构化输出解析。旁路范围仅限
当前进程和单一目标主机，没有修改系统代理配置；目标主机及所有代理值均未记录。

## 脱敏结果

| Smoke | `configured_secret_values_absent` | `transport_and_raw_provider_fields_absent` |
| --- | --- | --- |
| `6176 ms` | `true` | `true` |
| `7706 ms` | `true` | `true` |

公开摘录明确排除 API Key、Base URL、目标主机、HTTP/HTTPS/SOCKS 代理值、请求头、
原始异常、Provider 原始响应、本地 artifact 路径，以及 `task_id`、`request_id`、
`call_id` 的具体值。

## 当日已知边界

两次决策都生成了 `repo="pallets/click"`，而当日本地索引的规范仓库名是 `click`。
因此，这份 2026-08-02 证据只证明真实模型能够产出符合 Schema、可关联、可计时且已
脱敏的决策；它不证明当时的仓库参数能够在实际搜索工具上命中。后续仓库别名解析修复及
Tool 执行结果应由各自的回归和执行证据单独证明。
