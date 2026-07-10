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
- `src/zoekt/client.py`：构造 Zoekt 查询、调用 Zoekt API 并解析响应。
- `src/models.py`：定义与 MCP 和 Zoekt 实现解耦的结构化结果模型。

## 当前能力

项目目前提供 `search_code` 工具，支持：

- 跨已索引仓库搜索代码；
- 按仓库、语言和文件路径过滤；
- 普通查询与正则查询；
- 对包含连字符、空格等特殊字符的内容进行字面量搜索；
- 返回仓库名、文件路径、行号和匹配代码片段；
- 限制返回结果数量，避免向模型传入过多上下文。

示例问题：

```text
搜索所有 Java 仓库中使用 @RestController 的代码。

fintech-mx-wallet-proxy 在哪些仓库中被引用？

哪个文件读取了指定的配置项？
```

## 本地运行

### 前置条件

- Python 3.10 或更高版本；
- 已运行的 Zoekt Web Server；
- 已由 Zoekt 建立索引的代码仓库。

### 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置 Zoekt 地址

默认连接 `http://localhost:6070`。如果 Zoekt 运行在其他地址，可以设置：

```bash
export ZOEKT_URL=http://localhost:6070
```

### 启动 MCP Server

```bash
PYTHONPATH=. .venv/bin/python -m src.server
```

该服务使用 stdio 与 MCP 客户端通信，因此直接启动后没有普通 HTTP 页面属于正常现象。

## 手动验证搜索

确保 Zoekt 已启动并已加载索引，然后执行：

```bash
PYTHONPATH=. .venv/bin/python test/test_search.py
```

## 设计取舍

### 为什么使用 Zoekt，而不是向量数据库？

当前阶段主要解决精确代码检索问题，例如类名、方法名、错误信息、配置项和 API 调用位置。这些查询通常包含明确的文本或正则特征，Zoekt 更适合作为第一层检索基础设施。

向量检索更适合语义相似内容，但可能遗漏必须精确匹配的符号，也会引入代码切分、Embedding 更新和索引一致性等额外成本。后续可以采用“Zoekt 精确检索 + 语义检索”的混合方案，而不是让二者互相替代。

### 为什么通过 MCP 暴露能力？

MCP 将搜索能力与具体 AI 客户端解耦。同一个服务可以被不同的 Agent 或开发工具调用，也便于未来继续增加 `get_file_context`、`find_symbol` 和 `find_references` 等工具。

## Roadmap

- [x] 接入 Zoekt 搜索 API
- [x] 提供 `search_code` MCP 工具
- [x] 支持仓库、语言、路径和字面量过滤
- [x] 补充 Zoekt 查询构造和响应解析单元测试
- [ ] 补充 MCP Server 和 Zoekt 集成测试
- [ ] 增加 `get_file_context`
- [ ] 增加 `find_symbol` 和 `find_references`
- [ ] 控制上下文长度并改善结果排序
- [ ] 接入代码理解 Agent
- [ ] 结合错误日志实现故障代码定位

## 项目边界

- 搜索结果质量依赖 Zoekt 索引是否及时、完整；
- 当前主要提供文本和正则搜索，不等同于完整的语义代码理解；
- 当前不会自动判断搜索结果中的代码是否正确，也不会直接修改代码；
- 企业代码接入时还需要补充仓库权限、访问审计和敏感信息保护。
