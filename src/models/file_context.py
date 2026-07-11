from pydantic import BaseModel, Field


class FileContextRequest(BaseModel):
    repository: str = Field(
        min_length=1,
        description="Repository name registered in the code search service.",
    )
    file_path: str = Field(
        min_length=1,
        description="File path relative to the repository root.",
    )
    line_number: int = Field(
        ge=1,
        description="Target line number, starting from 1.",
    )
    lines_before: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Number of lines to return before the target line.",
    )
    lines_after: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Number of lines to return after the target line.",
    )


class FileContext(BaseModel):
    repository: str
    file_path: str

    target_line: int
    start_line: int
    end_line: int
    total_lines: int

    content: str
    truncated: bool = False
