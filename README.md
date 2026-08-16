# Code Search MCP

基于 [Zoekt](https://github.com/sourcegraph/zoekt) 和 Model Context Protocol（MCP）的代码搜索服务，让 AI 客户端能够快速检索大型代码库中的真实代码。

## 为什么做这个项目

大模型拥有通用编程知识，但默认不了解企业内部代码，也无法知道一个服务当前的目录结构、接口实现、配置依赖和跨仓库调用关系。当研发人员排查问题或理解陌生业务时，仍然需要在多个仓库之间反复切换、搜索和整理上下文。

这个项目尝试解决这一问题：

- **MCP 连接 AI 与代码库**：把代码搜索能力暴露成标准化工具，让支持 MCP 的 AI 客户端可以按需调用，而不是依赖人工复制代码。
- **Zoekt 提供高性能索引**：利用面向源码设计的索引和搜索能力，在大型、多仓库环境中快速定位关键词、符号和代码片段。
- **为代码理解提供可靠上下文**：先检索真实代码，再让模型分析结果，降低仅凭模型已有知识猜测实现的风险。
- **降低跨团队搜索成本**：研发人员不必预先熟悉所有仓库，也可以通过服务名、类名、接口、配置项或错误信息定位相关代码。
- **沉淀可复用的研发工具**：代码检索能力可以继续用于故障排查、代码问答、影响面分析和研发 Agent，而不局限于单一聊天界面。

本项目当前的目标不是让模型一次性“读懂整个代码库”，而是为模型提供一个快速、可控、可追溯的代码检索入口。代码理解、调用链分析和故障定位将在此基础上逐步实现。

## 工作方式

```text
用户提出代码问题
        │
        ▼
支持 MCP 的 AI 客户端
        │ 调用 search_code
        ▼
Code Search MCP Server
        │ 构造查询并转换结果
        ▼
Zoekt Web Server
        │ 查询预先生成的代码索引
        ▼
仓库、文件、行号和代码片段
```

职责划分：

- `src/server.py`：声明 MCP 工具，接收工具参数并格式化返回内容。
- `src/services/zoekt_client.py`：构造 Zoekt 查询、调用 Zoekt API 并解析响应。
- `src/services/file_reader.py`：安全解析仓库内路径并读取目标行上下文。
- `src/models/`：定义与 MCP 和 Zoekt 实现解耦的结构化结果模型。

## 当前能力

项目目前提供 `search_code` 和 `get_file_context` 两个工具，支持：

- 跨已索引仓库搜索代码；
- 按仓库、语言和文件路径过滤；
- 普通查询与正则查询；
- 对包含连字符、空格等特殊字符的内容进行字面量搜索；
- 返回仓库名、文件路径、行号和匹配代码片段；
- 限制返回结果数量，避免向模型传入过多上下文；
- 根据搜索命中的仓库、相对路径和行号读取源码上下文；
- 拒绝绝对路径和目录穿越，限制文件访问范围。

示例问题：

```text
搜索所有 Java 仓库中使用 @RestController 的代码。

sample-service-v2 在哪些仓库中被引用？

哪个文件读取了指定的配置项？
```

## 公开 Click 端到端演示

可用公开仓库 [`pallets/click` 8.4.1（固定 commit）](https://github.com/pallets/click/tree/6eeb50e948ea136db145280f6f5dd52eca3fa7e5) 复核一次真实的 MCP 调用闭环。演示以错误文本
`The given command does not have a callback that can be invoked.` 为查询条件，按
`search_code → get_file_context → 仅基于返回源码回答` 执行。

- [首次 E2E 证据：`repo` 映射不一致导致 `get_file_context` 失败](https://github.com/YamadaYoukai/AIEngineerRoadmap/blob/main/docs/evidence/2026-07-19-click-mcp-client-e2e.md)
- [修复后重跑 E2E 证据：成功读取 `click/src/click/core.py:848` 的上下文](https://github.com/YamadaYoukai/AIEngineerRoadmap/blob/main/docs/evidence/2026-07-19-click-mcp-client-e2e-rerun.md)

重跑的真实返回表明：当 `callback` 是 `Command` 且
`other_cmd.callback is None` 时，Click 在该位置抛出 `TypeError`。返回范围为第
836～860 行，并带有 `truncated=true`；因此该证据不把未出现在上下文中的类名或方法名
当作已验证结论。

## 本地运行

### 前置条件

- Python 3.10 或更高版本；
- 已运行的 Zoekt Web Server；
- 已由 Zoekt 建立索引的代码仓库。

### 安装依赖

项目运行依赖和开发依赖定义在 `pyproject.toml` 中。创建并激活虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
```

仅安装运行依赖：

```bash
python -m pip install .
```

本地开发或运行测试时，安装可编辑版本和开发依赖：

```bash
python -m pip install -e ".[dev]"
```

### 可选：真实模型决策冒烟

`evaluation/run_model_smoke.py` 会通过 OpenAI Python SDK 请求一次模型决策，
但不会执行模型返回的检索工具。它要求调用方显式提供
`OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL`。建议将本地配置放在已被
Git 忽略的 `.secrets/openai-smoke.env`，然后执行：

```bash
set -a
source .secrets/openai-smoke.env
set +a
PYTHONPATH=. .venv/bin/python evaluation/run_model_smoke.py \
  --no-proxy-base-url \
  --out .artifacts/model-smoke.json
```

`OPENAI_BASE_URL` 是传输配置：既可以指向 OpenAI API，也可以指向受信任的
OpenAI-compatible API 网关。`OpenAIModel` 本身只是应用内适配器，并不实现网络代理；
网关需要兼容 Chat Completions 的 strict function tools、`tool_choice` 和
`parallel_tool_calls`。远程环境应使用 TLS；仅在隔离且可信的本地/内网链路中接受明文
HTTP，并由部署方承担该链路的安全控制。

若机器设置了通用 HTTP、HTTPS 或 SOCKS 代理，而私有网关必须直连，可显式传入
`--no-proxy-base-url`。脚本只在当前进程中把 `OPENAI_BASE_URL` 的主机追加到
`NO_PROXY` 和 `no_proxy`，不会输出主机、修改系统代理或影响其他进程。

模型 Trace 只保留模型名、不含传输元数据的 `ModelInput`、稳定决策/错误分类、关联 ID 和
`elapsed_ms`。API Key、Base URL、请求头、原始异常和原始 Provider 响应均不进入
Trace。冒烟脚本会在写报告前验证已配置的 Key/Base URL 不在序列化结果中，并输出
`redaction_evidence`；任一检查失败都会终止执行，报告默认写入被 Git 忽略的
`.artifacts/`。

该 Click 冒烟会把规范索引名 `click` 及
`Click`、`pallets/click`、`github.com/pallets/click` 三个精确别名作为
`repository_hints` 提供给模型，并断言模型最终输出 `repo="click"`。Agent 执行层还会
通过 `RepositoryAliasResolver` 做第二次精确解析：原始别名保留在 `ModelResult`，真正
执行的规范名记录在 `ToolCall`；未知或冲突别名分别返回 `unknown_repository` 或
`ambiguous_repository`，不会再表现为成功的空搜索。

这里的脱敏边界只覆盖 Provider/传输元数据。`ModelInput`、业务证据和已校验决策会按
设计保留，脚本不是通用源码或 PII 脱敏器；调用方仍需在构造上下文前执行自己的仓库权限
与数据分级策略。

### Replay a persisted model decision

无需再次请求模型，即可用[公开脱敏 fixture](evaluation/fixtures/real-model-decision-click-replay.json)
和[单步重放 runner](evaluation/replay_tool_step.py)复现一次真实决策的
`ToolCall → ToolResult → 下一份 ModelInput`。准备好固定 Click 8.4.1 checkout 与 Zoekt
索引后，按[真实 Tool 证据](docs/evidence/2026-08-07-real-model-result-search-tool.md)中的命令
运行；该入口不读取模型凭据，也不重新生成原始模型 Trace。

### 冻结的 Agent Eval

独立 Agent 评测集位于
[`evaluation/agent_cases.jsonl`](evaluation/agent_cases.jsonl)，固定为 Click 8.4.1
revision `6eeb50e948ea136db145280f6f5dd52eca3fa7e5` 上人工复核的 10 条自然语言任务：
8 条可回答、2 条应以信息不足收口。冻结 SHA-256 为
`09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`。
真实运行前先做不调用模型或 Zoekt 的契约校验：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  evaluation/run_agent_eval.py --validate-only
```

真实入口要求调用方提供 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，以及由
索引创建方确认的 `ZOEKT_INDEX_REVISION`；同时要求 `ZOEKT_URL`、
`REPOSITORY_ROOT/click`、checkout revision 和 `git config zoekt.name` 一致。Runner 会在
任何模型调用前执行固定的正向/负向索引探针，并拒绝非默认文件或 SHA-256 不匹配的真实
评测。完整 10 条按文件顺序各运行一次，不提供 `--max-cases`、重试或 best-of 选项：

```bash
set -a
source .secrets/openai-smoke.env
set +a
export ZOEKT_INDEX_REVISION=6eeb50e948ea136db145280f6f5dd52eca3fa7e5

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  evaluation/run_agent_eval.py \
  --no-proxy-base-url \
  --out evaluation/reports/agent-eval-YYYY-MM-DD.json \
  --summary-out evaluation/reports/agent-eval-YYYY-MM-DD.md
```

逐题 JSON 保留终态、Tool 尝试与名称序列、模型提交的引用、当前任务成功 Tool 来源校验、
金标范围匹配、Trace 不变量、模型/Tool/任务耗时和稳定失败分类；汇总指标只从这些明细
重算。写盘前会断言 API Key、Base URL、本机仓库路径和传输/Provider 原始字段均不存在。

2026-08-16 的首轮真实执行保存在
[`agent-eval-2026-08-16.json`](evaluation/reports/agent-eval-2026-08-16.json) 和
[`agent-eval-2026-08-16.md`](evaluation/reports/agent-eval-2026-08-16.md)。该轮 10/10
均在首次模型调用处以 `model_execution_error` 终止，任务成功率为 0/10，Tool 尝试为
0，引用分母为 0，Trace 完整性为 10/10。

模型服务恢复后，用户明确授权使用相同冻结数据、Prompt、检索和金标执行一次独立恢复
重试；结果保存在
[`agent-eval-2026-08-16-retry-01.json`](evaluation/reports/agent-eval-2026-08-16-retry-01.json)
和
[`agent-eval-2026-08-16-retry-01.md`](evaluation/reports/agent-eval-2026-08-16-retry-01.md)。
重试轮任务成功率为 3/10，引用有效率 6/20（30%），Trace 完整性 10/10，每题 Tool
上限全部满足。6 个正向答案因模型提交范围格式引用而 fail closed，另 1 条为 Tool 错误；
没有根据结果修改机制或执行第三轮，M3 退出门槛仍未达到。

这 6 条范围引用失败已按原始报告哈希冻结在
[`agent-eval-range-citation-failures-2026-08-16.json`](evaluation/fixtures/agent-eval-range-citation-failures-2026-08-16.json)，
逐条保留模型输出、人工复核的结构化单行参考引用和稳定失败分类。下一项预注册的可证伪
假设见
[`agent-eval-range-citation-freeze-2026-08-16.md`](evaluation/reports/agent-eval-range-citation-freeze-2026-08-16.md)：
仅把最终决策中的自由文本引用改为严格的 `repo` / `path` / 正整数 `line` 字段，当前任务
成功 Tool 事实的精确运行时校验保持不变，不增加范围兼容或自动修复。

### 配置 Zoekt 地址

默认连接 `http://localhost:6070`。如果 Zoekt 运行在其他地址，可以设置：

```bash
export ZOEKT_URL=http://localhost:6070
export REPOSITORY_ROOT=/path/to/indexed/repositories
```

`REPOSITORY_ROOT` 下的一级目录名需要与 Zoekt 返回的仓库名一致。例如：

```text
/path/to/indexed/repositories/
├── code-search-mcp/
└── demo-service/
```

此时 Zoekt 命中中的 `repo` 应分别为 `code-search-mcp` 或 `demo-service`，
`get_file_context` 才能找到对应的本地源码。不要把 API Key、公司内网地址或
本地私有路径提交到仓库。

### 索引前固定 Zoekt 仓库名

本项目使用 `REPOSITORY_ROOT/<repo>` 将搜索结果映射到本地源码。对每个待索引的
Git 仓库，**必须在生成或重建 Zoekt 索引前**进入该仓库并执行：

```bash
git config zoekt.name <本地目录名>
```

例如，本地目录为 `REPOSITORY_ROOT/click` 时：

```bash
cd /path/to/indexed/repositories/click
git config zoekt.name click
```

随后再运行 Zoekt 的索引命令。若仓库已经索引过，修改配置后还必须重建该仓库的
索引；已有索引中的仓库名不会自动更新。

### 启动 MCP Server

```bash
PYTHONPATH=. .venv/bin/python -m src.server
```

该服务使用 stdio 与 MCP 客户端通信，因此直接启动后没有普通 HTTP 页面属于正常现象。

### 配置 MCP 客户端

不同客户端的配置入口不同，核心配置如下。请把 `cwd` 和环境变量替换为你
自己的绝对路径：

```json
{
  "mcpServers": {
    "code-search": {
      "command": "/absolute/path/to/mcp-server/.venv/bin/python",
      "args": ["-m", "src.server"],
      "cwd": "/absolute/path/to/mcp-server",
      "env": {
        "PYTHONPATH": ".",
        "ZOEKT_URL": "http://localhost:6070",
        "REPOSITORY_ROOT": "/absolute/path/to/repositories"
      }
    }
  }
}
```

### 使用 MCP Inspector 调试

```bash
PYTHONPATH=. .venv/bin/mcp dev src/server.py:mcp
```

该命令会通过 MCP Inspector 加载 FastMCP 服务，可以查看工具 schema 并手动调用
`search_code` 和 `get_file_context`。

## 工具说明

### `search_code`

在 Zoekt 索引中定位精确的源码文本或正则模式。适合查找类名、函数名、错误
信息、配置键和调用表达式；它是文本/正则搜索，不等同于语义级符号分析。

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `query` | `string` | 必填 | 源码文本或 Zoekt 正则表达式 |
| `repo` | `string \| null` | `null` | 仓库名正则过滤 |
| `lang` | `string \| null` | `null` | 语言过滤，如 `java`、`go`、`python` |
| `path` | `string \| null` | `null` | 仓库内文件路径正则过滤 |
| `limit` | `integer` | `20` | 返回命中数，范围 1～100 |
| `literal` | `boolean` | `false` | 是否把 `query` 当作完整字面量 |

返回值示例：

```json
{
  "query": "UserNotFound",
  "duration_ms": 3,
  "matches": [
    {
      "repo": "demo-service",
      "path": "src/service/user_service.py",
      "line": 128,
      "snippet": "raise UserNotFound(user_id)"
    }
  ]
}
```

以下情况建议设置 `literal=true`：

```text
sample-service-v2
connection refused: upstream unavailable
com.example.user-service
```

### `get_file_context`

读取一次搜索命中周围的源码。通常先调用 `search_code`，再将某条命中的
`repo`、`path` 和 `line` 分别传给 `repository`、`file_path` 和
`line_number`。

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `repository` | `string` | 必填 | `search_code` 返回的 `repo` |
| `file_path` | `string` | 必填 | `search_code` 返回的仓库内相对 `path` |
| `line_number` | `integer` | 必填 | `search_code` 返回的从 1 开始的 `line` |
| `lines_before` | `integer` | `20` | 目标行之前的行数，范围 0～100 |
| `lines_after` | `integer` | `20` | 目标行之后的行数，范围 0～100 |

返回的 `content` 带真实行号，并以 `>` 标出目标行：

```text
   126 |     user = repository.find(user_id)
   127 |     if user is None:
>  128 |         raise UserNotFound(user_id)
   129 |     return user
```

为避免任意文件读取，`file_path` 只接受仓库内相对路径，绝对路径和逃出仓库
根目录的 `..` 路径都会被拒绝。

## 完整调用流程

向 MCP 客户端提问：

```text
在 demo-service 中查找 UserNotFound，读取最相关命中前后各 10 行，
然后说明这段代码在什么情况下抛出异常。若信息不足，请明确说明。
```

理想工具调用链：

```text
search_code(query="UserNotFound", repo="demo-service", limit=10)
    ↓
选择相关的 repo + path + line
    ↓
get_file_context(
    repository="demo-service",
    file_path="src/service/user_service.py",
    line_number=128,
    lines_before=10,
    lines_after=10
)
    ↓
客户端基于真实源码回答，并引用文件与行号
```

## 手动验证搜索

确保 Zoekt 已启动并已加载索引，然后执行：

```bash
PYTHONPATH=. .venv/bin/python test/test_search.py
```

## 运行测试

单元测试默认不要求启动真实 Zoekt：

```bash
PYTHONPATH=. .venv/bin/pytest -v
```

若环境中没有 `pytest`，先安装：

```bash
pip install pytest
```

### 真实 Zoekt 验证（公开）

公开版使用 Click 8.4.1 作为真实 MCP Client 验证样例。完整的
`search_code → get_file_context` 调用轨迹、原始可见返回结果和限制说明见
上方“公开 Click 端到端演示”中的两份证据。

#### Click 检索评测基线

公开评测集位于 `evaluation/cases.jsonl`，当前共 18 条，固定使用
[`pallets/click` 8.4.1 commit `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`](https://github.com/pallets/click/tree/6eeb50e948ea136db145280f6f5dd52eca3fa7e5)。
先确认本地 checkout 与索引都使用该 revision，且 Zoekt 返回的仓库名为 `click`：

```bash
git -C "$REPOSITORY_ROOT/click" rev-parse HEAD
PYTHONPATH=. .venv/bin/python evaluation/run_eval.py --validate-only
PYTHONPATH=. .venv/bin/python evaluation/run_eval.py \
  --out evaluation/reports/baseline-YYYY-MM-DD.json
```

评测集包含正向命中、无命中、上下文边界，以及重复命中、无关结果、未限定
路径、符号候选、错误文本、语言/路径组合和长文件上下文等样例。每个正向样例声明金标
`repo`、`path` 与精确行号或可接受行号范围；无命中样例则明确声明受检
`repo`、`path`、`line: null` 和 `failure_reason`。无命中正确时不会调用
`get_file_context`，也不会被算入 Hit@1、Hit@5 或 `search_code → get_file_context`
闭环的分母，而是在 `no_match_success` 中单独统计。

固定 revision 上的逐条人工 `git grep` 复核记录见
[`evaluation/click-8.4.1-manual-grep.md`](evaluation/click-8.4.1-manual-grep.md)。该清单记录每条样例的类别、目标仓库、路径和可接受行号范围。重复候选、无关结果和未限定路径的样例会保留 Zoekt 原始返回顺序；即使金标未排在 Top-1 或 Top-5，也不会为了提高指标收窄查询或调整排序。

#### 2026-07-23 真实 Zoekt 重跑结果

[`baseline-2026-07-23.json`](evaluation/reports/baseline-2026-07-23.json) 与 2026-07-22 的基线对比如下。新集新增的 8 条故意覆盖了候选重复、文档/注释噪声和未限定路径的情况，因此质量指标的分母扩大，不能把百分比降低解读为对原 10 条已覆盖场景的回归。

| 指标 | 2026-07-22（10 条 / 8 类） | 2026-07-23（18 条 / 15 类） |
| --- | ---: | ---: |
| 正向 / 无命中样例 | 8 / 2 | 16 / 2 |
| Hit@1 | 8/8（100%） | 12/16（75%） |
| Hit@5 | 8/8（100%） | 13/16（81.25%） |
| `search_code → get_file_context` 闭环 | 8/8（100%） | 12/16（75%） |
| 无命中正确率 | 2/2（100%） | 2/2（100%） |
| 平均 search wall latency | 28.551 ms | 19.231 ms |
| 平均 closed-loop wall latency | 29.840 ms | 20.320 ms |

重跑没有搜索或上下文读取错误。4 条如实保留为 `top1_miss`：两个 `duplicate_hits`、`irrelevant_results`，以及 `unscoped_path`；其中 `unscoped_path` 的金标仍在 Top-5。延迟反映此次本地服务状态，适合作为同一环境内的对照而非跨机器 SLA。

两个 `context_boundary_*` 样例分别命中 `src/click/globals.py` 的第 1 行和第
64 行，并断言 `get_file_context` 返回的实际 `start_line`、`end_line`、
`total_lines`、`truncated` 以及目标行标记。这会覆盖文件开头和结尾的截断行为。

报告会记录每条样例的原始 wall-clock 时间、聚合的 `latency_ms.search_wall` 与
`latency_ms.closed_loop_wall`，以及本地 checkout revision。调用顺序、固定查询和
`time.perf_counter` 的计时定义也写入 JSON，因而质量指标可以精确复现；延迟会随
机器与 Zoekt 运行状态变化，应比较同一环境下的报告而非要求逐毫秒一致。

本地私有仓库的测试基线和输出只保存在被 Git 忽略的目录，不纳入公开仓库或发行物。

每次已启用的执行都会将可复现性信息写入
`.artifacts/zoekt-integration.json`（可通过 `ZOEKT_INTEGRATION_REPORT` 改写路径）：
Zoekt 地址、Python/HTTP 客户端信息、Zoekt Server commit、索引仓库 commit、本地
checkout commit、起止时间、耗时和执行结果。当前演示环境的服务器 `/about` 未暴露
build commit，因此该字段会记录为不可获取；如部署方能确认该值，可额外设置
`ZOEKT_SERVER_COMMIT`，它会优先写入报告。

## 常见问题

### 客户端启动后看不到工具

- 确认客户端配置中的 Python、`cwd` 都是绝对路径；
- 确认虚拟环境已经安装 `requirements.txt`；
- 确认以项目根目录作为工作目录，并设置 `PYTHONPATH=.`；
- 在 MCP Inspector 中先验证服务能否加载。

### `search_code` 报连接失败

- 确认 Zoekt Web Server 正在运行；
- 确认 `ZOEKT_URL` 可从 MCP Server 进程访问；
- 确认 Zoekt 已加载目标仓库索引。

### 搜索有结果，但 `get_file_context` 提示仓库或文件不存在

- 确认 `REPOSITORY_ROOT/<repo>` 是实际存在的目录；
- 确认 Zoekt 返回的仓库名与本地一级目录名一致；
- 确认搜索结果中的路径是仓库内相对路径；
- 确认 Zoekt 索引对应的源码与本地源码版本一致。

### Zoekt 返回的 `repo` 与本地目录不一致

公开 Click 演示的首次调用就是这一失败案例：`search_code` 返回
`repo=github.com/pallets/click`，而本地源码目录是
`REPOSITORY_ROOT/click`。`get_file_context` 按 Tool 返回值拼接本地目录后，会尝试
读取 `REPOSITORY_ROOT/github.com/pallets/click`，因而报 `Repository 不存在`。

根因是未显式设置 `zoekt.name` 时，Zoekt 可能根据 Git `origin` 推导出
`github.com/<owner>/<repo>` 形式的仓库名；该名字与本项目按本地一级目录寻址的契约不一致。

修复方式是在**索引前**进入本地 checkout，执行
`git config zoekt.name <本地目录名>`，然后重建索引。例如目录名为 `click` 时执行
`git config zoekt.name click`。修复后先确认 `search_code` 返回 `repo=click`，再将其
返回的 `repo`、`path`、`line` 原样传给 `get_file_context`。

### 搜索服务名或完整错误信息时没有结果

带空格、连字符或 Zoekt 查询运算符的完整文本应设置 `literal=true`。需要正则
能力时则保持 `literal=false`。

## 设计取舍

### 为什么使用 Zoekt，而不是向量数据库？

当前阶段主要解决精确代码检索问题，例如类名、方法名、错误信息、配置项和 API 调用位置。这些查询通常包含明确的文本或正则特征，Zoekt 更适合作为第一层检索基础设施。

向量检索更适合语义相似内容，但可能遗漏必须精确匹配的符号，也会引入代码切分、Embedding 更新和索引一致性等额外成本。后续可以采用“Zoekt 精确检索 + 语义检索”的混合方案，而不是让二者互相替代。

### 为什么通过 MCP 暴露能力？

MCP 将搜索能力与具体 AI 客户端解耦。同一个服务可以被不同的 Agent 或开发工具调用，也便于未来继续增加 `find_symbol` 和 `find_references` 等工具。

## Roadmap

- [x] 接入 Zoekt 搜索 API
- [x] 提供 `search_code` MCP 工具
- [x] 支持仓库、语言、路径和字面量过滤
- [x] 补充 Zoekt 查询构造和响应解析单元测试
- [x] 补充 MCP Server 基本测试
- [x] 补充真实 Zoekt 集成测试（默认关闭）
- [x] 增加 `get_file_context`
- [ ] 增加 `find_symbol` 和 `find_references`
- [ ] 控制上下文长度并改善结果排序
- [ ] 接入代码理解 Agent
- [ ] 结合错误日志实现故障代码定位

## 当前限制

- 当前演示环境的 Zoekt Server `/about` 未暴露 build commit，故 Server commit 不可获取；
  不应根据猜测补写版本或 commit。部署方可通过 `ZOEKT_SERVER_COMMIT` 显式提供已确认的值。
- `get_file_context` 只返回目标行前后请求的有限行数（每侧最多 100 行）。当返回
  `truncated=true` 时，文件前部或后部没有包含在上下文中；未出现在返回内容中的类、方法或
  调用关系不能视为已验证。
- 搜索结果质量依赖 Zoekt 索引是否及时、完整；
- 当前主要提供文本和正则搜索，不等同于完整的语义代码理解；
- 当前不会自动判断搜索结果中的代码是否正确，也不会直接修改代码；
- 企业代码接入时还需要补充仓库权限、访问审计和敏感信息保护。
