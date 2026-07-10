"""本地直接调用 server.py 中的 call_tool 测试搜索逻辑(无需 MCP 客户端)。

用法:
    cd ~/code-mcp-demo/mcp-server
    source .venv/bin/activate
    python test_search.py
"""
import asyncio
from src.server import call_tool


async def main():
    cases = [
        {"query": "@RestController", "lang": "java", "limit": 5},
        {"query": "@Autowired", "lang": "java", "limit": 5},
        {"query": "throw new", "lang": "java", "limit": 5},
        # 含连字符的字面量 — literal=true 防止 - 被当成否定运算符
        {"query": "fintech-mx-wallet-proxy", "lang": "java", "literal": True, "limit": 5},
    ]
    for c in cases:
        print(f"\n>>> {c}")
        try:
            result = await call_tool("search_code", c)
            print(result[0].text[:500])
        except Exception as e:
            print(f"!! error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
