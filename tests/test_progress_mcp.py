"""Actual MCP stdio requests remain useful before the first scan completes."""

import asyncio
import json
import os
import sys
import textwrap

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_first_run_mcp_returns_labeled_partial_results(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    for name in ["invoice-first.txt", "invoice-later.txt"]:
        (root / name).write_text("fixture")
    release = tmp_path / "release"
    wrapper = tmp_path / "slow_scan.py"
    wrapper.write_text(
        textwrap.dedent("""
        import os,time
        from pathlib import Path
        from everything_mcp.index import LocalIndex
        from everything_mcp import main
        def slow_walk(self,path,root,generation,failed):
            first=Path(root)/'invoice-first.txt'
            yield self._row(str(first),root,generation,first.stat(),False)
            deadline=time.monotonic()+10
            while not Path(os.environ['SCAN_RELEASE']).exists():
                if self._stop.is_set() or time.monotonic()>deadline:return
                time.sleep(.02)
            later=Path(root)/'invoice-later.txt'
            yield self._row(str(later),root,generation,later.stat(),False)
        LocalIndex._walk=slow_walk
        main()
    """),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "EVERYTHING_MCP_ROOTS": json.dumps([str(root)]),
        "EVERYTHING_MCP_DB": str(tmp_path / "index.db"),
        "SCAN_RELEASE": str(release),
    }
    parameters = StdioServerParameters(command=sys.executable, args=[str(wrapper)], env=env)

    def output(result):
        assert not result.isError
        return "\n".join(getattr(block, "text", "") for block in result.content)

    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        for _ in range(100):
            status = json.loads(output(await client.call_tool("everything_index_status", {})))
            if status["indexed_entries"] == 1:
                break
            await asyncio.sleep(0.05)
        assert status["state"] == "building" and status["indexed_entries"] == 1
        partial = output(await client.call_tool("everything_search", {"params": {"query": "in"}}))
        assert "invoice-first.txt" in partial and "invoice-later.txt" not in partial
        assert "partial" in partial and "1 entries currently searchable" in partial
        release.touch()
        for _ in range(100):
            status = json.loads(output(await client.call_tool("everything_index_status", {})))
            if status["state"] == "ready":
                break
            await asyncio.sleep(0.05)
        complete = output(await client.call_tool("everything_search", {"params": {"query": "in"}}))
        assert status["state"] == "ready" and status["indexed_entries"] == 2
        assert "invoice-first.txt" in complete and "invoice-later.txt" in complete
        assert "Results are partial" not in complete
