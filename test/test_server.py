import unittest
from unittest.mock import AsyncMock, patch

from src.models import CodeSearchResponse
from src.server import call_tool, list_tools


class ToolSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_schema_only_requires_query(self):
        tools = await list_tools()
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


class SearchToolTest(unittest.IsolatedAsyncioTestCase):
    @patch("src.server.zoekt_client.search", new_callable=AsyncMock)
    async def test_search_uses_request_defaults(self, search: AsyncMock):
        search.return_value = CodeSearchResponse(
            query="UserService",
            duration_ms=1,
            matches=[],
        )

        result = await call_tool("search_code", {"query": "UserService"})

        search.assert_awaited_once_with(
            query="UserService",
            repo=None,
            lang=None,
            path=None,
            limit=20,
            literal=False,
        )
        self.assertIn("未找到匹配", result[0].text)

    @patch("src.server.zoekt_client.search", new_callable=AsyncMock)
    async def test_search_rejects_invalid_limit(self, search: AsyncMock):
        result = await call_tool(
            "search_code",
            {"query": "UserService", "limit": 0},
        )

        search.assert_not_awaited()
        self.assertIn("搜索失败", result[0].text)


if __name__ == "__main__":
    unittest.main()
