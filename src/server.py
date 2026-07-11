import asyncio
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import ValidationError

from src.config import get_repository_root
from src.models import CodeSearchRequest, CodeSearchResponse, FileContextRequest
from src.services import ZoektClient, ZoektError, read_file_context

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
            inputSchema=CodeSearchRequest.model_json_schema(),
        ),
        Tool(
            name="get_file_context",
            description=(
                "读取代码搜索结果所在行附近的源码。应在 search_code 返回仓库、"
                "相对文件路径和行号后调用。"
            ),
            inputSchema=FileContextRequest.model_json_schema(),
        ),
    ]


@app.call_tool()
async def call_tool(
        name: str,
        arguments: dict[str, Any],
) -> list[TextContent]:
    if name == "search_code":
        return await _call_search_code(arguments)
    if name == "get_file_context":
        return _call_get_file_context(arguments)
    raise ValueError(f"Unknown tool: {name}")


async def _call_search_code(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        request = CodeSearchRequest.model_validate(arguments)
        result = await zoekt_client.search(
            query=request.query,
            repo=request.repo,
            lang=request.lang,
            path=request.path,
            limit=request.limit,
            literal=request.literal,
        )
    except (ValidationError, ValueError, ZoektError) as exc:
        return [TextContent(type="text", text=f"搜索失败：{exc}")]

    return [
        TextContent(
            type="text",
            text=format_search_response(result),
        )
    ]


def _call_get_file_context(
    arguments: dict[str, Any],
) -> list[TextContent]:
    try:
        request = FileContextRequest.model_validate(arguments)
        result = read_file_context(
            repository=request.repository,
            repository_root=get_repository_root(request.repository),
            file_path=request.file_path,
            line_number=request.line_number,
            lines_before=request.lines_before,
            lines_after=request.lines_after,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        return [TextContent(type="text", text=f"读取文件上下文失败：{exc}")]

    return [TextContent(type="text", text=result.model_dump_json(indent=2))]


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


async def main() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
