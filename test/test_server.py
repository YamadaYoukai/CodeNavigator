import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from src.models import CodeMatch, CodeSearchResponse
from src.server import mcp, search_code, get_file_context


class ToolSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_schema_only_requires_query(self):
        tools = await mcp.list_tools()
        search_tool = next(tool for tool in tools if tool.name == "search_code")

        self.assertEqual(search_tool.inputSchema["required"], ["query"])
        self.assertEqual(
            search_tool.inputSchema["properties"]["limit"]["default"],
            20,
        )
        self.assertEqual(
            search_tool.inputSchema["properties"]["limit"]["minimum"],
            1,
        )

    async def test_registers_both_tools(self):
        tools = await mcp.list_tools()

        self.assertEqual(
            {tool.name for tool in tools},
            {"search_code", "get_file_context"},
        )


class SearchToolTest(unittest.IsolatedAsyncioTestCase):
    @patch("src.server.zoekt_client.search", new_callable=AsyncMock)
    async def test_search_uses_defaults(self, search: AsyncMock):
        search.return_value = CodeSearchResponse(
            query="UserService",
            duration_ms=1,
            matches=[],
        )

        result = await search_code("UserService")

        search.assert_awaited_once_with(
            query="UserService",
            repo=None,
            lang=None,
            path=None,
            limit=20,
            literal=False,
        )
        self.assertEqual(result.matches, [])

    @patch("src.server.zoekt_client.search", new_callable=AsyncMock)
    async def test_search_converts_service_error(self, search: AsyncMock):
        search.side_effect = ValueError("limit must be between 1 and 100")

        with self.assertRaisesRegex(ToolError, "搜索失败"):
            await search_code("UserService", limit=0)

    @patch("src.server.zoekt_client.search", new_callable=AsyncMock)
    async def test_search_preserves_raw_matches_while_stably_prioritizing_declaration(
            self,
            search: AsyncMock,
    ):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory) / "click"
            source_file = repository_root / "src/click/globals.py"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                "\n".join([
                    "from .base import resolve_color_default",
                    "def resolve_color_default() -> bool:",
                    "    return True",
                    "",
                ]),
                encoding="utf-8",
            )
            raw_matches = [
                CodeMatch(
                    repo="click",
                    path="src/click/globals.py",
                    line=1,
                    snippet="from .base import resolve_color_default",
                ),
                CodeMatch(
                    repo="click",
                    path="src/click/globals.py",
                    line=2,
                    snippet="def resolve_color_default() -> bool:",
                ),
            ]
            search.return_value = CodeSearchResponse(
                query="resolve_color_default",
                duration_ms=1,
                matches=raw_matches,
            )

            with patch(
                    "src.server.get_repository_root",
                    return_value=repository_root,
            ):
                result = await search_code(
                    "resolve_color_default",
                    literal=True,
                )

        self.assertEqual(
            [(item.path, item.line) for item in result.matches],
            [
                ("src/click/globals.py", 2),
                ("src/click/globals.py", 1),
            ],
        )
        self.assertEqual(result._zoekt_matches, raw_matches)
        self.assertNotIn("_zoekt_matches", result.model_dump())


class GetFileContextTest(unittest.TestCase):
    @patch("src.server.read_file_context")
    @patch("src.server.get_repository_root", return_value="/tmp/demo")
    def test_converts_value_error_to_tool_error_with_original_reason(
        self,
        get_repository_root: unittest.mock.Mock,
        read_file_context: unittest.mock.Mock,
    ):
        reason = "line_number 99 exceeds file length 3"
        read_file_context.side_effect = ValueError(reason)

        with self.assertRaises(ToolError) as raised:
            get_file_context(
                repository="demo",
                file_path="example.py",
                line_number=99,
            )

        message = str(raised.exception)
        self.assertIn("读取文件上下文失败", message)
        self.assertIn(reason, message)
        get_repository_root.assert_called_once_with("demo")

    @patch("src.server.read_file_context")
    @patch("src.server.get_repository_root", return_value="/tmp/demo")
    def test_converts_file_not_found_error_to_tool_error_with_original_reason(
        self,
        get_repository_root: unittest.mock.Mock,
        read_file_context: unittest.mock.Mock,
    ):
        reason = "File does not exist: missing.py"
        read_file_context.side_effect = FileNotFoundError(reason)

        with self.assertRaises(ToolError) as raised:
            get_file_context(
                repository="demo",
                file_path="missing.py",
                line_number=1,
            )

        message = str(raised.exception)
        self.assertIn("读取文件上下文失败", message)
        self.assertIn(reason, message)
        get_repository_root.assert_called_once_with("demo")


if __name__ == "__main__":
    unittest.main()
