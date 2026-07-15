import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from src.models import CodeSearchResponse
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
