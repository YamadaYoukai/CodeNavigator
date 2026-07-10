import asyncio
import os
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from src.zoekt.client import ZoektClient, ZoektError
from src.models import CodeSearchResponse

app = Server("code-search-mcp")

zoekt_client = ZoektClient(base_url=os.getenv("ZOEKT_URL", "http://localhost:6070"))


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_code",
            description=(
                "在团队所有已索引的代码仓库中跨仓库搜索代码。"
                "支持普通文本、正则表达式、按仓库/语言/路径过滤。"
                "返回匹配的代码片段、文件路径、行号、所在仓库。"
                "适用场景：跨仓库查找 API 调用、定位实现、代码复用查找。"
                "提示：搜服务名 / 包名等含连字符 - 或冒号 : 的字面值时,设 literal=true 以防被当成查询语法。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或正则表达式",
                    },
                    "repo": {
                        "type": "string",
                        "description": "仓库名过滤（正则），可选",
                    },
                    "lang": {
                        "type": "string",
                        "description": "编程语言过滤（如 java/go/python），可选",
                    },
                    "path": {
                        "type": "string",
                        "description": "文件路径过滤（正则），可选",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回结果数量上限",
                        "default": 20,
                    },
                    "literal": {
                        "type": "boolean",
                        "description": (
                            "字面量搜索:true 时把 query 当成纯字符串,自动转义 zoekt 运算符 "
                            "(连字符 - / ! / 引号 / 空格 等)。搜服务名 'fintech-mx-wallet-proxy'、"
                            "包名 com.didi.foo 等含特殊字符的字面值时建议设 true。"
                            "regex 搜索请保持 false。默认 false。"
                        ),
                        "default": False,
                    },
                },
                "required": ["query"],
            },
        )
    ]


@app.call_tool()
async def call_tool(
        name: str,
        arguments: dict[str, Any],
) -> list[TextContent]:
    if name != "search_code":
        raise ValueError(f"Unknown tool: {name}")

    try:
        result = await zoekt_client.search(
            query=arguments["query"],
            repo=arguments.get("repo"),
            lang=arguments.get("lang"),
            path=arguments.get("path"),
            limit=arguments.get("limit", 20),
            literal=arguments.get("literal", False),
        )
    except (ValueError, ZoektError) as exc:
        return [TextContent(type="text", text=f"搜索失败：{exc}")]

    return [
        TextContent(
            type="text",
            text=format_search_response(result),
        )
    ]


def format_search_response(result: CodeSearchResponse) -> str:
    if not result.matches:
        return f"未找到匹配 '{result.query}' 的代码"

    output = [
        f"找到 {len(result.matches)} 处匹配"
        f"（耗时 {result.duration_ms}ms）：",
        "",
    ]

    for match in result.matches:
        output.append(f"📁 {match.repo}/{match.path}:{match.line}")
        output.append(f"   {match.snippet}")
        output.append("")

    return "\n".join(output)


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
