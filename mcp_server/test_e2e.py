"""
End-to-end test of the MCP server: launches server.py as a real MCP subprocess
over stdio, performs the MCP handshake, lists the tools, and calls them exactly
as an AI client (e.g. Claude Desktop) would. Proves the accounts domain is served
THROUGH the MCP server (not Cube, not a direct mf call).

Run:  venv/bin/python mcp_server/test_e2e.py
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=os.environ.copy())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            print("\n--- list_metrics() ---")
            r = await session.call_tool("list_metrics", {})
            print(r.content[0].text[:500])

            print("\n--- list_dimensions('account_count') ---")
            r = await session.call_tool("list_dimensions", {"metric": "account_count"})
            print(r.content[0].text[:500])

            print("\n--- query_metric('account_count', group_by='account__industry') ---")
            r = await session.call_tool("query_metric", {
                "metrics": "account_count", "group_by": "account__industry",
                "order_by": "-account_count", "limit": 5})
            print(r.content[0].text)

            print("\n--- query_metric('block_rate', group_by='account__risk_scoring') ---")
            r = await session.call_tool("query_metric", {
                "metrics": "block_rate", "group_by": "account__risk_scoring"})
            print(r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
