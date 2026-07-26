"""Verify the FastMCP wrapper is valid: list tools and their schemas in-memory
(no Warp window opened)."""
import asyncio

from fastmcp import Client

import fleet_mcp


async def main():
    async with Client(fleet_mcp.mcp) as c:
        tools = await c.list_tools()
        print(f"server: {fleet_mcp.mcp.name}  tools: {len(tools)}")
        for t in tools:
            params = list((t.inputSchema or {}).get("properties", {}).keys())
            print(f"  - {t.name}({', '.join(params)})")


if __name__ == "__main__":
    asyncio.run(main())
