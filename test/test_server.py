import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from src.models import CodeSearchResponse
from src.server import mcp, search_code


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


if __name__ == "__main__":
    unittest.main()
