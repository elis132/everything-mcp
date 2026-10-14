import asyncio
import time

import pytest

from everything_mcp.backend import (
    EverythingBackend,
    SearchResult,
    build_recent_query,
    build_type_query,
    human_size,
)


async def test_real_own_index_search_aggregates_and_metadata(backend, valid_config):
    results = await backend.search("ext:py")
    assert len(results) == 1 and results[0].name == "main.py"
    assert results[0].path == str(valid_config.roots[0] / "main.py")
    assert results[0].size > 0 and results[0].date_modified
    assert await backend.count("file:") == 3
    assert await backend.get_total_size("ext:txt") == 5
    status = await backend.health_check()
    assert status["independent"] and status["state"] == "ready"
    assert status["indexed_entries"] == 4


async def test_cap_pagination_sort_and_flags(backend):
    backend.config.max_results_cap = 1
    assert len(await backend.search("file:", max_results=100)) == 1
    assert (await backend.search("file:", offset=1))[0].name == "notes.txt"
    assert not await backend.search("MAIN", match_case=True)
    assert (await backend.search(r"^main\.py$", match_regex=True))[0].name == "main.py"
    assert (await backend.search("file:", sort="size-desc"))[0].name == "main.py"


async def test_configuration_error_never_opens_index(tmp_path):
    from everything_mcp.config import EverythingConfig

    backend = EverythingBackend(
        EverythingConfig(roots=[tmp_path / "missing"], db_path=tmp_path / "db")
    )
    with pytest.raises(RuntimeError, match="Invalid index configuration"):
        await backend.start()
    assert backend.index is None
    assert (await backend.health_check())["state"] == "error"
    assert not (tmp_path / "db").exists()


async def test_worker_queries_keep_event_loop_responsive(backend, monkeypatch):
    original = backend.index.count

    def slow(query="", **kwargs):
        time.sleep(0.12)
        return original(query, **kwargs)

    monkeypatch.setattr(backend.index, "count", slow)
    query = asyncio.create_task(backend.count("file:"))
    await asyncio.sleep(0.025)
    assert not query.done()
    assert await query == 3


async def test_query_timeout_reports_clear_error(backend, monkeypatch):
    def timed_out(*args, **kwargs):
        raise TimeoutError("SQLite deadline")

    monkeypatch.setattr(backend.index, "search", timed_out)
    with pytest.raises(RuntimeError, match="query exceeded"):
        await backend.search("anything")


async def test_cached_results_available_while_reconciling(backend, monkeypatch):
    status = backend.index.status()
    monkeypatch.setattr(backend.index, "status", lambda: {**status, "state": "building"})

    async def must_not_wait(*args, **kwargs):
        raise AssertionError("Cached results must not wait for a full scan")

    monkeypatch.setattr(backend.index, "wait_ready", must_not_wait)
    assert (await backend.search("main"))[0].name == "main.py"


async def test_first_build_returns_available_rows_without_waiting(backend, monkeypatch):
    monkeypatch.setattr(
        backend.index, "status", lambda: {"state": "building", "indexed_entries": 0}
    )

    async def not_ready(*args, **kwargs):
        raise AssertionError("First-run searches must not wait for the initial scan")

    monkeypatch.setattr(backend.index, "wait_ready", not_ready)
    assert await backend.count("main") == 1


async def test_unsupported_sort_and_offsets_rejected(backend):
    with pytest.raises(ValueError):
        await backend.search("main", sort="unknown")
    with pytest.raises(ValueError):
        await backend.search("main", offset=-1)


async def test_start_and_query_need_no_external_process(valid_config, monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("Independent index must not launch any external program")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    instance = EverythingBackend(valid_config)
    try:
        await instance.start()
        await instance.wait_ready(15)
        assert (await instance.search("main"))[0].name == "main.py"
    finally:
        await instance.close()


def test_builders_and_result_format():
    assert build_type_query("code", "main", "D:/Dev").startswith("ext:")
    assert 'path:"D:/Dev"' in build_type_query("code", "main", "D:/Dev")
    assert build_recent_query("1hour", "", ".py, .txt") == "dm:last1hours ext:py;txt"
    with pytest.raises(ValueError):
        build_type_query("unknown")
    assert human_size(2048) == "2.0 KB" and human_size(-1) == "unknown"
    assert SearchResult("/tmp/x", "x", is_dir=True).to_dict() == {
        "path": "/tmp/x",
        "name": "x",
        "type": "folder",
    }
