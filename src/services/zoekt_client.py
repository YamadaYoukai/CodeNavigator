import base64

from typing import Any
import httpx

from src.models import CodeMatch, CodeSearchResponse

_ZOEKT_OPS = set("-!()|&\" ")  # zoekt 查询语法运算符:-(否定) !(否定) ()|& 引号 空格


class ZoektError(RuntimeError):
    """Raised when Zoekt cannot complete a search."""


def _quote_if_literal(query: str, literal: bool) -> str:
    """字面量模式下,若 query 含运算符则用双引号包起来,避免被当成查询语法。"""
    if not literal:
        return query
    if any(c in _ZOEKT_OPS for c in query):
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return query


def build_zoekt_query(
        query: str,
        repo: str | None,
        lang: str | None,
        path: str | None,
        literal: bool = False,
) -> str:
    parts = [_quote_if_literal(query, literal)]
    if repo:
        parts.append(f"r:{repo}")
    if lang:
        parts.append(f"lang:{lang}")
    if path:
        parts.append(f"f:{path}")
    return " ".join(parts)


class ZoektClient:

    def __init__(
            self,
            base_url: str,
            *,
            timeout: float = 10.0,
            http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http_client = http_client

    async def search(self,
                     query: str,
                     *,
                     repo: str | None = None,
                     lang: str | None = None,
                     path: str | None = None,
                     limit: int = 20,
                     literal: bool = False,
                     ) -> CodeSearchResponse:

        query = query.strip()
        if not query:
            raise ValueError("query must not be blank")

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        zoekt_query = build_zoekt_query(query=query,
                                        repo=repo,
                                        lang=lang,
                                        path=path,
                                        literal=literal)

        # zoekt-webserver -rpc: POST /api/search,字段 Q + Opts;响应 {Result: SearchResult}
        # SearchResult 含 Files []FileMatch;FileMatch.Line[]byte 在 JSON 中是 base64
        payload = {
            "Q": zoekt_query,
            "Opts": {
                "NumContextLines": 0,
                "TotalMaxMatchCount": limit * 5,
                "MaxDocDisplayCount": limit,
                "MaxMatchDisplayCount": limit * 3,
            }
        }

        try:
            data = await self._post_search(payload)
        except httpx.HTTPError as exc:
            raise ZoektError(f"failed to call Zoekt: {exc}") from exc

        error = data.get("Error")
        if error:
            raise ZoektError(str(error))

        return self._parse_response(query, data, limit)

    async def _post_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._http_client is not None:
            response = await self._http_client.post(
                f"{self._base_url}/api/search",
                json=payload,
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/api/search",
                    json=payload,
                )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_response(
            query: str,
            data: dict[str, Any],
            limit: int,
    ) -> CodeSearchResponse:
        result = data.get("Result") or {}
        matches: list[CodeMatch] = []

        for fm in result.get("Files") or []:
            for m in fm.get("LineMatches") or []:
                if len(matches) >= limit:
                    break

                matches.append(CodeMatch(
                    repo=fm.get("Repository", "?"),
                    path=fm.get("FileName", "?"),
                    line=max(1, m.get("LineNumber", 1)),
                    snippet=_decode_line(m.get("Line")),
                ))

            if len(matches) >= limit:
                break

        return CodeSearchResponse(
            query=query,
            duration_ms=result.get("Duration", 0) // 1_000_000,
            matches=matches,
        )


def _decode_line(encoded_line: str | None) -> str:
    if not encoded_line:
        return ""

    try:
        return base64.b64decode(encoded_line).decode(
            "utf-8",
            errors="replace"
        ).strip()
    except (ValueError, TypeError):
        return "<decode error>"
