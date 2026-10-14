"""Time real MCP startup and searches against explicitly selected local roots."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(roots: list[str], database: str, queries: list[str]) -> dict:
    environment = dict(os.environ)
    environment["EVERYTHING_MCP_ROOTS"] = json.dumps(roots)
    environment["EVERYTHING_MCP_DB"] = database
    started = time.perf_counter()
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "everything_mcp"], env=environment
    )
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        startup_ms = (time.perf_counter() - started) * 1000
        for _ in range(600):
            status = await client.call_tool("everything_index_status", {})
            status_text = "\n".join(getattr(block, "text", "") for block in status.content)
            parsed = json.loads(status_text)
            if parsed.get("state") == "ready":
                break
            if parsed.get("state") == "error":
                raise RuntimeError(status_text)
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError("Index not ready after 60 seconds")
        ready_ms = (time.perf_counter() - started) * 1000
        results = []
        for query in queries:
            begin = time.perf_counter()
            result = await client.call_tool(
                "everything_search", {"params": {"query": query, "max_results": 30}}
            )
            results.append(
                {
                    "query": query,
                    "roundtrip_ms": round((time.perf_counter() - begin) * 1000, 3),
                    "output": "\n".join(getattr(block, "text", "") for block in result.content),
                }
            )
        return {
            "startup_ms": round(startup_ms, 3),
            "ready_ms": round(ready_ms, 3),
            "status": parsed,
            "queries": results,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", nargs="+", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--queries", nargs="+", default=["config | known_hosts"])
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = json.dumps(
        asyncio.run(probe(arguments.roots, arguments.database, arguments.queries)), indent=2
    )
    print(report)
    if arguments.output:
        arguments.output.write_text(report + "\n", encoding="utf-8")
