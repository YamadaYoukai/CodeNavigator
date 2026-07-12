import os
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from src.config import get_repository_root
from src.models import CodeSearchResponse, FileContext
from src.services import ZoektClient, ZoektError, read_file_context

mcp = FastMCP(
    name="code-search-mcp",
    instructions=(
        "Use search_code to locate exact symbols, error messages, configuration "
        "keys, and other source text in Zoekt-indexed repositories. When a hit "
        "looks relevant, pass its repo, path, and one-based line number to "
        "get_file_context before explaining the code. Prefer narrow repository, "
        "language, or path filters when the user provides them."
    ),
)

zoekt_client = ZoektClient(
    base_url=os.getenv("ZOEKT_URL", "http://localhost:6070")
)


@mcp.tool()
async def search_code(
    query: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "要查找的源码文本或 Zoekt 正则表达式，例如类名、函数名、"
                "错误信息、配置键或调用表达式"
            ),
        ),
    ],
    repo: Annotated[
        str | None,
        Field(description="可选的仓库名正则过滤；已知目标仓库时应尽量设置"),
    ] = None,
    lang: Annotated[
        str | None,
        Field(description="可选的语言过滤，例如 java、go 或 python"),
    ] = None,
    path: Annotated[
        str | None,
        Field(description="可选的仓库内文件路径正则过滤，例如 src/.*\\.py"),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=100, description="最多返回的代码命中数，默认 20，最大 100"),
    ] = 20,
    literal: Annotated[
        bool,
        Field(
            description=(
                "true 表示将 query 作为完整字面量，而非 Zoekt 查询语法；"
                "搜索带空格、连字符或运算符的错误信息、服务名、包名时使用"
            )
        ),
    ] = False,
) -> CodeSearchResponse:
    """在 Zoekt 已索引的仓库中定位源码文本及其文件位置。

    当用户询问符号定义或引用、错误信息来源、配置项用法、接口实现或精确
    代码模式时使用。结果包含仓库、仓库内相对路径、从 1 开始的行号和命中
    行片段；需要理解周围实现时，再用该结果调用 get_file_context。

    query 默认按 Zoekt 查询语法解释。搜索包含空格、连字符或查询运算符的
    完整错误信息、服务名或包名时设置 literal=true。已知仓库、语言或路径
    范围时应使用对应过滤器，以减少无关结果。该工具只做文本/正则检索，
    不执行语义分析，也不保证命中就是定义。
    """
    try:
        return await zoekt_client.search(
            query=query,
            repo=repo,
            lang=lang,
            path=path,
            limit=limit,
            literal=literal,
        )
    except (ValueError, ZoektError) as exc:
        raise ToolError(f"搜索失败：{exc}") from exc


@mcp.tool()
def get_file_context(
    repository: Annotated[
        str,
        Field(
            min_length=1,
            description="search_code 命中结果中的 repo 字段（仓库名称）",
        ),
    ],
    file_path: Annotated[
        str,
        Field(
            min_length=1,
            description="search_code 命中结果中的 path 字段；必须是仓库内相对路径",
        ),
    ],
    line_number: Annotated[
        int,
        Field(ge=1, description="search_code 命中结果中的 line 字段（从 1 开始）"),
    ],
    lines_before: Annotated[
        int,
        Field(ge=0, le=100, description="目标行之前返回的源码行数，默认 20"),
    ] = 20,
    lines_after: Annotated[
        int,
        Field(ge=0, le=100, description="目标行之后返回的源码行数，默认 20"),
    ] = 20,
) -> FileContext:
    """读取一次代码搜索命中位置周围带行号的源码上下文。

    应在 search_code 返回候选命中后调用，并原样传入该命中的 repo、path 和
    line。适合确认命中属于定义、调用还是配置，以及在回答用户前理解附近
    的控制流。返回内容以 ``>`` 标出目标行，并包含实际起止行、文件总行数
    和是否截断。不要用它搜索未知文件，也不要传入绝对路径或 ``..`` 路径。
    """
    try:
        return read_file_context(
            repository=repository,
            repository_root=get_repository_root(repository),
            file_path=file_path,
            line_number=line_number,
            lines_before=lines_before,
            lines_after=lines_after,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise ToolError(f"读取文件上下文失败：{exc}") from exc


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
