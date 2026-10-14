"""Exercise actual stdio MCP against isolated local fixtures; no Everything needed."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="everything-mcp-smoke-") as temp:
        root = Path(temp) / "files"
        root.mkdir()
        (root / "needle-invoice.txt").write_text("independent index\n", encoding="utf-8")
        (root / "photo.jpg").write_bytes(b"fixture")
        env = dict(os.environ)
        env["EVERYTHING_MCP_ROOTS"] = json.dumps([str(root)])
        env["EVERYTHING_MCP_DB"] = str(Path(temp) / "index.sqlite3")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "everything_mcp"], env=env
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            names = {tool.name for tool in tools}
            assert {
                "everything_search",
                "everything_count_stats",
                "everything_index_status",
            } <= names
            tool = next(tool for tool in tools if tool.name == "everything_search")
            args = {"query": "needle-invoice", "max_results": 10}
            if "params" in tool.inputSchema.get("properties", {}):
                args = {"params": args}
            for _ in range(100):
                result = await session.call_tool("everything_search", args)
                output = "\n".join(getattr(block, "text", "") for block in result.content)
                if "needle-invoice.txt" in output:
                    assert not result.isError
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError(f"Fixture not found through MCP: {output}")
            status = await session.call_tool("everything_index_status", {})
            assert not status.isError
            print(
                json.dumps(
                    {
                        "tools": sorted(names),
                        "search": output,
                        "status": [getattr(b, "text", "") for b in status.content],
                    },
                    indent=2,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
