from pydantic import BaseModel, Field


class CodeSearchRequest(BaseModel):
    query: str = Field(
        min_length=1,
        description="搜索关键词或正则表达式",
    )
    repo: str | None = Field(
        default=None,
        description="仓库名过滤（正则），可选",
    )
    lang: str | None = Field(
        default=None,
        description="编程语言过滤（如 java/go/python），可选",
    )
    path: str | None = Field(
        default=None,
        description="文件路径过滤（正则），可选",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="返回结果数量上限",
    )
    literal: bool = Field(
        default=False,
        description=(
            "字面量搜索:true 时把 query 当成纯字符串,自动转义 zoekt 运算符 "
            "(连字符 - / ! / 引号 / 空格 等)。搜索包含特殊字符的服务名或"
            "包名时建议设 true。"
            "regex 搜索请保持 false。默认 false。"
        ),
    )


class CodeMatch(BaseModel):
    repo: str = Field(description="Repository name")
    path: str = Field(description="File path inside the repository")
    line: int = Field(ge=1, description="One-based line number")
    snippet: str = Field(description="Matched source line")


class CodeSearchResponse(BaseModel):
    query: str
    duration_ms: int = Field(ge=0)
    matches: list[CodeMatch]
