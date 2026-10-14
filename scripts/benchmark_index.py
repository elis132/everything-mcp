"""Synthetic search benchmark. Reuse --database to avoid repeating row insertion."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from everything_mcp.index import LocalIndex
from everything_mcp.query import QueryError
from everything_mcp.storage import register_functions


async def benchmark(entries: int, database: Path | None = None) -> dict:
    manager = (
        contextlib.nullcontext(None)
        if database
        else tempfile.TemporaryDirectory(prefix="everything-mcp-benchmark-")
    )
    with manager as temp:
        if database:
            with sqlite3.connect(database) as connection:
                register_functions(connection)
                root = Path(connection.execute("SELECT root FROM entries LIMIT 1").fetchone()[0])
                entries = connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        else:
            root = Path(temp) / "files"
            root.mkdir()
            database = Path(temp) / "index.db"
        index = LocalIndex(database, [root])
        try:
            elapsed = None
            first_batch = None
            if temp is not None:
                canonical_root = index.roots[0]
                stat = SimpleNamespace(st_size=1024, st_mtime=1791800000, st_ctime=1791800000)
                started = time.perf_counter()
                for offset in range(0, entries, 5000):
                    rows = []
                    for number in range(offset, min(offset + 5000, entries)):
                        extension = "pdf" if number % 100 == 0 else "txt"
                        prefix = "invoice" if number % 1000 == 0 else "document"
                        path = (
                            root / f"project-{number % 100}" / f"{prefix}-{number:07d}.{extension}"
                        )
                        rows.append(index._row(str(path), canonical_root, "synthetic", stat, False))
                    index._upsert(rows)
                    if first_batch is None:
                        visible_at = time.perf_counter() - started
                        begin = time.perf_counter()
                        found = index.search("invoice", max_results=3)
                        first_batch = {
                            "after_seconds": round(visible_at, 3),
                            "indexed_entries": index.count(),
                            "query_ms": round((time.perf_counter() - begin) * 1000, 3),
                            "returned": len(found),
                            "scan_complete": False,
                        }
                elapsed = round(time.perf_counter() - started, 3)
            result = {
                "kind": "synthetic SQLite rows; excludes filesystem crawl",
                "entries": entries,
                "insert_seconds": elapsed,
                "first_batch": first_batch,
                "queries": [],
            }
            candidate_times = []
            candidate_depth = 0
            original_candidates = index._names.candidates

            def timed_candidates(connection, term):
                nonlocal candidate_depth
                candidate_depth += 1
                begin = time.perf_counter()
                try:
                    return original_candidates(connection, term)
                finally:
                    candidate_depth -= 1
                    candidate_times.append((time.perf_counter() - begin) * 1000)

            index._names.candidates = timed_candidates
            original_limited = index._names.limited_candidates

            def timed_limited(connection, term):
                begin = time.perf_counter()
                try:
                    return original_limited(connection, term)
                finally:
                    if not candidate_depth:
                        candidate_times.append((time.perf_counter() - begin) * 1000)

            index._names.limited_candidates = timed_limited
            for query, sort in [
                ("invoice", "name"),
                ("invoice ext:pdf", "name"),
                ("ext:pdf", "name"),
                ("document-0999999", "name"),
                ("missing-needle", "name"),
                ("in", "name"),
                ("invoice", "date-modified-desc"),
                ("in", "date-modified-desc"),
                ("i", "name"),
                ("o", "name"),
            ]:
                times = []
                partials = []
                candidate_times.clear()
                row = {"query": query, "sort": sort}
                try:
                    for _ in range(5):
                        begin = time.perf_counter()
                        rows = index.search(query, max_results=50, sort=sort)
                        times.append((time.perf_counter() - begin) * 1000)
                        partials.append(rows.partial)
                    row.update(
                        returned=len(rows),
                        median_ms=round(statistics.median(times), 3),
                        max_ms=round(max(times), 3),
                        first_ms=round(times[0], 3),
                        partial=any(partials),
                        first_candidate_ms=round(candidate_times[0], 3) if candidate_times else 0,
                        median_candidate_ms=round(statistics.median(candidate_times), 3)
                        if candidate_times
                        else 0,
                    )
                except QueryError as exc:
                    row["error"] = str(exc)
                row["plan"] = index.explain(query, sort=sort)
                result["queries"].append(row)
            result["filename_memory_bytes"] = index.status()["filename_memory_bytes"]
            result["candidate_cache_bytes"] = index.status()["candidate_cache_bytes"]
            index._writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            result["db_bytes"] = sum(
                p.stat().st_size for p in database.parent.glob(database.name + "*")
            )
            result["aggregate_checks"] = []
            for query, expected in [
                ("", entries),
                ("invoice", (entries + 999) // 1000),
                ("in", (entries + 999) // 1000),
            ]:
                check = {"query": query, "expected": expected}
                begin = time.perf_counter()
                try:
                    check["actual"] = index.count(query)
                    check["passed"] = check["actual"] == expected
                except QueryError as exc:
                    check.update(passed=False, error=str(exc))
                check["elapsed_ms"] = round((time.perf_counter() - begin) * 1000, 3)
                result["aggregate_checks"].append(check)
            return result
        finally:
            await index.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=1_000_000)
    parser.add_argument(
        "--database", type=Path, help="Reuse a previously populated synthetic database"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = json.dumps(asyncio.run(benchmark(args.entries, args.database)), indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
