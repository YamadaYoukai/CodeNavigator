# src/models.py
from pydantic import BaseModel, Field


class CodeMatch(BaseModel):
    repo: str = Field(description="Repository name")
    path: str = Field(description="File path inside the repository")
    line: int = Field(ge=1, description="One-based line number")
    snippet: str = Field(description="Matched source line")


class CodeSearchResponse(BaseModel):
    query: str
    duration_ms: int = Field(ge=0)
    matches: list[CodeMatch]
