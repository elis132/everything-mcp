"""Tests for everything_mcp.server tool functions and helpers."""

from __future__ import annotations

import json

import pytest

from everything_mcp.server import (
    _TEXT_EXTENSIONS,
    _TEXT_FILENAMES,
    _format_search_results,
    _read_preview,
)

# ── _format_search_results ────────────────────────────────────────────────


class TestFormatSearchResults:
    def test_empty_results(self):
        result = _format_search_results([], "*.py", max_results=50)
        assert "No results found" in result
        assert "*.py" in result

    def test_with_results(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=50)
        assert "Found 3 results" in result
        assert r"C:\Projects\app\main.py" in result
        assert r"C:\Projects\app\utils.py" in result
        assert "[FILE]" in result
        assert "[DIR]" in result

    def test_file_metadata_shown(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=50)
        assert "2.0 KB" in result
        assert "2026-01-15" in result

    def test_offset_shown(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=50, offset=100)
        assert "offset: 100" in result

    def test_pagination_hint_when_at_limit(self, sample_results):
        # When results == max_results, show pagination hint
        result = _format_search_results(sample_results, "*.py", max_results=3)
        assert "offset" in result.lower()

    def test_no_pagination_hint_when_under_limit(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=100)
        assert "Showing first" not in result

    def test_folder_no_size(self, sample_results):
        result = _format_search_results(sample_results, "*", max_results=50)
        # The directory "src" line should not have a size
        lines = result.split("\n")
        src_line = [line for line in lines if "src" in line and "[DIR]" in line]
        assert len(src_line) == 1
        # Should not show "unknown" or negative size
        assert "unknown" not in src_line[0]


# ── _read_preview ─────────────────────────────────────────────────────────


class TestReadPreview:
    def test_reads_python_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("line 1\nline 2\nline 3\n")
        result = _read_preview(f, 2)
        assert result == "line 1\nline 2"

    def test_reads_all_lines_when_fewer_than_max(self, tmp_path):
        f = tmp_path / "short.py"
        f.write_text("only line\n")
        result = _read_preview(f, 100)
        assert result == "only line"

    def test_returns_none_for_binary(self, tmp_path):
        f = tmp_path / "image.bin"
        f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
        result = _read_preview(f, 10)
        assert result is None

    def test_returns_none_for_unknown_extension(self, tmp_path):
        # Create a binary file with unknown extension
        f = tmp_path / "data.xyz123"
        f.write_bytes(b"\x00\x01\x02\x03")
        result = _read_preview(f, 10)
        assert result is None

    def test_known_filenames(self, tmp_path):
        for name in ("Makefile", "Dockerfile", "LICENSE", "README"):
            f = tmp_path / name
            f.write_text(f"content of {name}\n")
            result = _read_preview(f, 1)
            assert result is not None, f"Failed for {name}"

    def test_dotfile_is_text(self, tmp_path):
        f = tmp_path / ".gitignore"
        f.write_text("node_modules/\n")
        result = _read_preview(f, 1)
        assert result == "node_modules/"

    def test_large_file_returns_message(self, tmp_path):
        f = tmp_path / "huge.txt"
        f.write_bytes(b"x" * (11 * 1024 * 1024))  # 11 MB
        result = _read_preview(f, 10)
        assert result is not None
        assert "too large" in result

    def test_utf8_content(self, tmp_path):
        f = tmp_path / "unicode.py"
        f.write_text("# Hälsningar från Sverige\n", encoding="utf-8")
        result = _read_preview(f, 1)
        assert "Sverige" in result

    def test_latin1_fallback(self, tmp_path):
        f = tmp_path / "latin.txt"
        f.write_bytes("café\n".encode("latin-1"))
        result = _read_preview(f, 1)
        assert result is not None
        assert "caf" in result

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "missing.txt"
        result = _read_preview(f, 10)
        assert result is None

    def test_preview_is_char_capped(self, tmp_path):
        f = tmp_path / "long_line.txt"
        f.write_text("x" * 70_000 + "\n")
        result = _read_preview(f, 1)
        assert result is not None
        assert "preview truncated" in result


# ── Extension/filename sets ───────────────────────────────────────────────


class TestTextSets:
    def test_common_code_extensions(self):
        for ext in ("py", "js", "ts", "c", "cpp", "go", "rs", "java"):
            assert ext in _TEXT_EXTENSIONS, f"Missing: {ext}"

    def test_common_config_extensions(self):
        for ext in ("json", "yaml", "yml", "toml", "ini", "xml"):
            assert ext in _TEXT_EXTENSIONS, f"Missing: {ext}"

    def test_common_text_filenames(self):
        for name in ("makefile", "dockerfile", "license", "readme"):
            assert name in _TEXT_FILENAMES, f"Missing: {name}"

    def test_modern_web_extensions(self):
        """Verify modern web framework extensions are supported."""
        for ext in ("astro", "mdx", "svelte", "vue", "prisma"):
            assert ext in _TEXT_EXTENSIONS, f"Missing modern extension: {ext}"


# ── Sort validation ────────────────────────────────────────────────────────


class TestSortValidation:
    """Test that invalid sort options are rejected."""

    def test_valid_sort_accepted(self):
        from everything_mcp.server import SearchInput

        params = SearchInput(query="*.py", sort="date-modified-desc")
        assert params.sort == "date-modified-desc"

    def test_invalid_sort_rejected(self):
        from pydantic import ValidationError

        from everything_mcp.server import SearchInput

        with pytest.raises(ValidationError, match="Invalid sort option"):
            SearchInput(query="*.py", sort="invalid-sort")

    def test_all_sort_options_valid(self):
        from everything_mcp.backend import SORT_MAP
        from everything_mcp.server import SearchInput

        for sort_key in SORT_MAP:
            params = SearchInput(query="test", sort=sort_key)
            assert params.sort == sort_key


# ── Tool error handling ───────────────────────────────────────────────────


async def test_all_six_tools_registered_read_only():
    from everything_mcp.server import mcp

    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "everything_search",
        "everything_search_by_type",
        "everything_find_recent",
        "everything_file_details",
        "everything_count_stats",
        "everything_index_status",
    }
    for tool in tools:
        annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        assert annotations["readOnlyHint"] is True and annotations["destructiveHint"] is False
        assert "es.exe" not in tool.description and "sub-millisecond" not in tool.description


@pytest.fixture
async def tool_backend(backend, monkeypatch):
    from everything_mcp import server

    monkeypatch.setattr(server, "_backend", backend)
    monkeypatch.setattr(server, "_config", backend.config)
    return backend


async def test_tools_use_real_independent_index(tool_backend, valid_config):
    from everything_mcp import server

    output = await server.everything_search(server.SearchInput(query="ext:py"))
    assert "main.py" in output and "Found 1 results" in output
    category = await server.everything_search_by_type(server.SearchByTypeInput(file_type="image"))
    assert "photo.jpg" in category and "main.py" not in category
    recent = await server.everything_find_recent(
        server.FindRecentInput(period="today", extensions="py")
    )
    assert "main.py" in recent
    stats = json.loads(
        await server.everything_count_stats(
            server.CountStatsInput(query="file:", breakdown_by_extension=True)
        )
    )
    assert stats["total_count"] == 3 and stats["total_size"] > 0
    assert stats["extension_breakdown"]["py"]["count"] == 1
    status = json.loads(await server.everything_index_status())
    assert status["independent"] and status["state"] == "ready"
    assert status["roots"] == [str(valid_config.roots[0])]


async def test_cached_search_reports_reconciliation(tool_backend, monkeypatch):
    from everything_mcp import server

    status = tool_backend.index.status()
    monkeypatch.setattr(tool_backend.index, "status", lambda: {**status, "state": "building"})
    output = await server.everything_search(server.SearchInput(query="main"))
    assert "main.py" in output and "partial" in output and "searchable" in output


async def test_search_and_stats_report_incomplete_index_coverage(tool_backend, monkeypatch):
    from everything_mcp import server

    status = tool_backend.index.status()
    note = "Some paths were unavailable; cached entries under them were retained."
    monkeypatch.setattr(
        tool_backend.index,
        "status",
        lambda: {**status, "state": "ready", "partial": True, "coverage_note": note},
    )
    search = await server.everything_search(server.SearchInput(query="main"))
    stats = json.loads(await server.everything_count_stats(server.CountStatsInput(query="main")))

    assert "Index coverage is incomplete" in search and note in search
    assert "cached statistics may be stale" in stats["freshness_note"]


async def test_bounded_search_labels_partial_ordering(tool_backend, monkeypatch):
    from everything_mcp import server
    from everything_mcp.query import QueryError

    def timed_out(*args, **kwargs):
        raise QueryError("Query exceeded its time limit")

    monkeypatch.setattr(tool_backend.index, "_execute", timed_out)
    output = await server.everything_search(server.SearchInput(query="main"))
    assert "main.py" in output
    assert "bounded subset" in output and "Ordering and pagination" in output


@pytest.mark.parametrize("diagnostic", ["", "Filesystem watching unavailable: root removed"])
async def test_cached_search_labels_degraded_watching(tool_backend, monkeypatch, diagnostic):
    from everything_mcp import server

    status = tool_backend.index.status()
    monkeypatch.setattr(
        tool_backend.index,
        "status",
        lambda: {**status, "state": "ready", "watching": False, "last_error": diagnostic},
    )
    output = await server.everything_search(server.SearchInput(query="main"))
    assert "main.py" in output and "cached results may be stale" in output
    assert diagnostic in output if diagnostic else "watching is unavailable" in output
    stats = json.loads(await server.everything_count_stats(server.CountStatsInput(query="main")))
    assert "cached statistics may be stale" in stats["freshness_note"]


def test_fifo_preview_never_opens_special_file(tmp_path, monkeypatch):
    import os

    from everything_mcp import server

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation requires POSIX")
    pipe = tmp_path / "pipe.txt"
    os.mkfifo(pipe)

    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must reject FIFO before opening it")

    monkeypatch.setattr(server, "open", forbidden, raising=False)
    assert server._read_preview(pipe, 5) is None


async def test_tool_error_is_clean(tool_backend, monkeypatch):
    from everything_mcp import server

    async def fail(*args, **kwargs):
        raise RuntimeError("deliberate query failure")

    monkeypatch.setattr(tool_backend, "search", fail)
    result = await server.everything_search(server.SearchInput(query="main"))
    assert result == "Error: deliberate query failure"


async def test_count_failure_stops_more_queries(tool_backend, monkeypatch):
    from everything_mcp import server

    async def fail(*args, **kwargs):
        raise RuntimeError("deliberate count failure")

    async def must_not_run(*args, **kwargs):
        raise AssertionError("Size must not follow count failure")

    monkeypatch.setattr(tool_backend, "count", fail)
    monkeypatch.setattr(tool_backend, "get_total_size", must_not_run)
    output = await server.everything_count_stats(server.CountStatsInput(query="file:"))
    assert output == "Error: deliberate count failure"


async def test_details_preview_and_directory_summary(tmp_path):
    from everything_mcp import server

    file = tmp_path / "example.py"
    file.write_text("first\nsecond\n", encoding="utf-8")
    details = json.loads(
        await server.everything_file_details(
            server.FileDetailsInput(paths=[str(file)], preview_lines=1)
        )
    )
    assert details["preview"] == "first" and details["size"] > 0
    summary = json.loads(
        await server.everything_file_details(server.FileDetailsInput(paths=[str(tmp_path)]))
    )
    assert summary["item_count"] == 1 and summary["files_sample"] == ["example.py"]


async def test_lifespan_builds_and_closes_own_index(tmp_path, monkeypatch):
    from everything_mcp import server

    root = tmp_path / "files"
    root.mkdir()
    (root / "hello.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setenv("EVERYTHING_MCP_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv("EVERYTHING_MCP_DB", str(tmp_path / "index.db"))
    async with server.lifespan(None):
        backend = server._backend
        await backend.wait_ready(15)
        assert "hello.txt" in await server.everything_search(server.SearchInput(query="hello"))
    assert server._backend is None and backend.index is None


async def test_lifespan_start_failure_remains_diagnosable(tmp_path, monkeypatch):
    from everything_mcp import server

    monkeypatch.setenv("EVERYTHING_MCP_ROOTS", json.dumps([str(tmp_path)]))
    monkeypatch.setenv("EVERYTHING_MCP_DB", str(tmp_path / "index.db"))

    async def denied(self):
        raise OSError("database write denied")

    monkeypatch.setattr(server.EverythingBackend, "start", denied)
    async with server.lifespan(None):
        status = json.loads(await server.everything_index_status())
        assert status["state"] == "error" and "database write denied" in status["errors"][0]
        output = await server.everything_search(server.SearchInput(query="file:"))
        assert output.startswith("Error:") and "database write denied" in output
