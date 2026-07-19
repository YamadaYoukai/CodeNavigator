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
