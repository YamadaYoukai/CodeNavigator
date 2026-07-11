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
        "Search indexed source repositories with Zoekt, then read source "
        "context around relevant matches."
    ),
)

zoekt_client = ZoektClient(
    base_url=os.getenv("ZOEKT_URL", "http://localhost:6070")
)


@mcp.tool()
async def search_code(
    query: Annotated[
        str,
        Field(min_length=1, description="搜索关键词或正则表达式"),
    ],
    repo: Annotated[
        str | None,
        Field(description="仓库名过滤（正则），可选"),
    ] = None,
    lang: Annotated[
        str | None,
        Field(description="编程语言过滤（如 java/go/python），可选"),
    ] = None,
    path: Annotated[
        str | None,
        Field(description="文件路径过滤（正则），可选"),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=100, description="返回结果数量上限"),
    ] = 20,
    literal: Annotated[
        bool,
        Field(description="是否将 query 作为纯字符串进行字面量搜索"),
    ] = False,
) -> CodeSearchResponse:
    """在团队所有已索引的代码仓库中跨仓库搜索代码。

    支持普通文本、正则表达式以及仓库、语言和路径过滤。搜索包含连字符、
    空格或其他 Zoekt 运算符的服务名、包名时，使用 literal=true。
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
        Field(min_length=1, description="代码仓库名称"),
    ],
    file_path: Annotated[
        str,
        Field(min_length=1, description="仓库根目录下的相对文件路径"),
    ],
    line_number: Annotated[
        int,
        Field(ge=1, description="从 1 开始的目标行号"),
    ],
    lines_before: Annotated[
        int,
        Field(ge=0, le=100, description="目标行之前返回的行数"),
    ] = 20,
    lines_after: Annotated[
        int,
        Field(ge=0, le=100, description="目标行之后返回的行数"),
    ] = 20,
) -> FileContext:
    """读取代码搜索结果所在行附近的源码。

    应在 search_code 返回仓库、相对文件路径和行号后调用。
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
