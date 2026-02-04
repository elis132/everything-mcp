"""Tests for everything_mcp.backend."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from everything_mcp.backend import (
    FILE_TYPES,
    SORT_MAP,
    TIME_PERIODS,
    EverythingBackend,
    SearchResult,
    _decode_output,
    _looks_like_path,
    _parse_paths_and_stat,
    build_recent_query,
    build_type_query,
    human_size,
)

# ── human_size ────────────────────────────────────────────────────────────


class TestHumanSize:
    def test_bytes(self):
        assert human_size(0) == "0 B"
        assert human_size(100) == "100 B"
        assert human_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert human_size(1024) == "1.0 KB"
        assert human_size(1536) == "1.5 KB"

    def test_megabytes(self):
        assert human_size(1024 * 1024) == "1.0 MB"
        assert human_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert human_size(1024**3) == "1.0 GB"

    def test_terabytes(self):
        assert human_size(1024**4) == "1.0 TB"

    def test_petabytes(self):
        assert human_size(1024**5) == "1.0 PB"

    def test_negative(self):
        assert human_size(-1) == "unknown"


# ── _looks_like_path ──────────────────────────────────────────────────────


class TestLooksLikePath:
    def test_drive_letter(self):
        assert _looks_like_path(r"C:\Windows\system32") is True
        assert _looks_like_path("D:\\") is True
        assert _looks_like_path(r"Z:\some\path") is True

    def test_forward_slash_drive(self):
        assert _looks_like_path("C:/Users/test") is True

    def test_unc(self):
        assert _looks_like_path(r"\\server\share\file.txt") is True

    def test_unix(self):
        assert _looks_like_path("/home/user/file.txt") is True

    def test_not_a_path(self):
        assert _looks_like_path("hello world") is False
        assert _looks_like_path("12345") is False
        assert _looks_like_path("") is False


# ── _decode_output ────────────────────────────────────────────────────────


class TestDecodeOutput:
    def test_utf8(self):
        assert _decode_output(b"hello\n") == "hello\n"

    def test_utf8_bom(self):
        data = b"\xef\xbb\xbfhello"
        assert _decode_output(data) == "hello"

    def test_latin1_fallback(self):
        # Byte 0xe9 is 'é' in latin-1 but invalid in UTF-8
        data = b"caf\xe9"
        result = _decode_output(data)
        assert "caf" in result

    def test_empty(self):
        assert _decode_output(b"") == ""


# ── _parse_paths_and_stat ─────────────────────────────────────────────────


class TestParsePathsAndStat:
    def test_empty_output(self):
        assert _parse_paths_and_stat("") == []
        assert _parse_paths_and_stat("\n\n\n") == []

    def test_skips_non_paths(self):
        # Lines that don't look like file paths should be skipped
        result = _parse_paths_and_stat("not a path\n12345\nhello world\n")
        assert result == []

    @patch("everything_mcp.backend._stat_to_result")
    def test_valid_paths_are_statted(self, mock_stat):
        mock_stat.return_value = SearchResult(path=r"C:\test.txt", name="test.txt")
        result = _parse_paths_and_stat(r"C:\test.txt" + "\n")
        assert len(result) == 1
        mock_stat.assert_called_once_with(r"C:\test.txt")

    @patch("everything_mcp.backend._stat_to_result")
    def test_multiple_paths(self, mock_stat):
        mock_stat.side_effect = [
            SearchResult(path=r"C:\a.txt", name="a.txt"),
            SearchResult(path=r"D:\b.py", name="b.py"),
        ]
        result = _parse_paths_and_stat(r"C:\a.txt" + "\n" + r"D:\b.py" + "\n")
        assert len(result) == 2

    @patch("everything_mcp.backend._stat_to_result")
    def test_blank_lines_skipped(self, mock_stat):
        mock_stat.return_value = SearchResult(path=r"C:\a.txt", name="a.txt")
        result = _parse_paths_and_stat("\n\n" + r"C:\a.txt" + "\n\n")
        assert len(result) == 1

    @patch("everything_mcp.backend._stat_to_result")
    def test_preserves_significant_trailing_whitespace(self, mock_stat):
        path_with_space = r"C:\folder\file.txt "
        mock_stat.return_value = SearchResult(path=path_with_space, name="file.txt ")
        result = _parse_paths_and_stat(path_with_space + "\n")
        assert len(result) == 1
        mock_stat.assert_called_once_with(path_with_space)


# ── build_type_query ──────────────────────────────────────────────────────


class TestBuildTypeQuery:
    def test_basic_type(self):
        q = build_type_query("code")
        assert q.startswith("ext:")
        assert "py" in q

    def test_with_path(self):
        q = build_type_query("image", path_filter=r"C:\Photos")
        assert 'path:"C:\\Photos"' in q
        assert "jpg" in q

    def test_with_additional_query(self):
        q = build_type_query("document", additional_query="report")
        assert "report" in q
        assert "pdf" in q

    def test_with_all_params(self):
        q = build_type_query("audio", additional_query="jazz", path_filter=r"D:\Music")
        assert "mp3" in q
        assert "jazz" in q
        assert 'path:"D:\\Music"' in q

    def test_case_insensitive(self):
        q = build_type_query("CODE")
        assert "py" in q

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown file type"):
            build_type_query("nonexistent")

    def test_all_types_valid(self):
        for ftype in FILE_TYPES:
            q = build_type_query(ftype)
            assert q.startswith("ext:")


# ── build_recent_query ────────────────────────────────────────────────────


class TestBuildRecentQuery:
    def test_default_period(self):
        q = build_recent_query()
        assert "dm:last1hour" in q

    def test_today(self):
        q = build_recent_query("today")
        assert "dm:today" in q

    def test_with_path(self):
        q = build_recent_query("1week", path_filter=r"C:\Projects")
        assert "dm:last1week" in q
        assert 'path:"C:\\Projects"' in q

    def test_with_extensions_comma(self):
        q = build_recent_query("1hour", extensions="py,js,ts")
        assert "ext:py;js;ts" in q

    def test_with_extensions_semicolon(self):
        q = build_recent_query("1hour", extensions="py;js;ts")
        assert "ext:py;js;ts" in q

    def test_with_dotted_extensions(self):
        q = build_recent_query("1hour", extensions=".py,.js")
        assert "ext:py;js" in q

    def test_empty_extensions(self):
        q = build_recent_query("1hour", extensions="")
        assert "ext:" not in q

    def test_unknown_period_passed_through(self):
        q = build_recent_query("last42days")
        assert "dm:last42days" in q

    def test_all_periods_valid(self):
        for period, value in TIME_PERIODS.items():
            q = build_recent_query(period)
            assert f"dm:{value}" in q


# ── SearchResult ──────────────────────────────────────────────────────────


class TestSearchResult:
    def test_file_to_dict(self):
        r = SearchResult(
            path=r"C:\test.py",
            name="test.py",
            size=1024,
            extension="py",
            date_modified="2026-01-15 10:00:00",
        )
        d = r.to_dict()
        assert d["path"] == r"C:\test.py"
        assert d["type"] == "file"
        assert d["size"] == 1024
        assert d["size_human"] == "1.0 KB"
        assert d["extension"] == "py"

    def test_folder_to_dict(self):
        r = SearchResult(path=r"C:\Projects", name="Projects", is_dir=True)
        d = r.to_dict()
        assert d["type"] == "folder"
        assert "size" not in d

    def test_unknown_size(self):
        r = SearchResult(path=r"C:\test.py", name="test.py", size=-1)
        d = r.to_dict()
        assert "size" not in d


# ── EverythingBackend ─────────────────────────────────────────────────────


class TestEverythingBackend:
    @pytest.mark.asyncio
    async def test_search_builds_correct_command(self, backend):
        """Verify the command built by search() includes expected flags."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            with patch("everything_mcp.backend._parse_paths_and_stat", return_value=[]):
                await backend.search("*.py", max_results=10, sort="name")

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == backend.config.es_path
            assert "-n" in cmd
            assert "10" in cmd
            assert "-sort" in cmd
            assert "name" in cmd
            assert "*.py" in cmd
            # No metadata flags
            assert "-size" not in cmd
            assert "-dm" not in cmd
            assert "-dc" not in cmd

    @pytest.mark.asyncio
    async def test_search_with_instance(self, config_15a):
        """Verify instance flag is included in commands."""
        backend = EverythingBackend(config_15a)
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            with patch("everything_mcp.backend._parse_paths_and_stat", return_value=[]):
                await backend.search("*.py")

            cmd = mock_run.call_args[0][0]
            assert "-instance" in cmd
            assert "1.5a" in cmd

    @pytest.mark.asyncio
    async def test_search_with_modifiers(self, backend):
        """Verify match flags are passed through."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            with patch("everything_mcp.backend._parse_paths_and_stat", return_value=[]):
                await backend.search(
                    "test", match_case=True, match_whole_word=True,
                    match_regex=True, match_path=True, offset=50,
                )
            cmd = mock_run.call_args[0][0]
            assert "-case" in cmd
            assert "-w" in cmd
            assert "-r" in cmd
            assert "-p" in cmd
            assert "-o" in cmd
            assert "50" in cmd

    @pytest.mark.asyncio
    async def test_search_error_raises(self, backend):
        """Non-zero exit code raises RuntimeError."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "IPC window not found", 1)
            with pytest.raises(RuntimeError, match="IPC window not found"):
                await backend.search("*.py")

    @pytest.mark.asyncio
    async def test_count(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("42\n", "", 0)
            result = await backend.count("ext:py")
            assert result == 42

    @pytest.mark.asyncio
    async def test_count_error(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "error", 1)
            with pytest.raises(RuntimeError):
                await backend.count("ext:py")

    @pytest.mark.asyncio
    async def test_get_total_size(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("1048576\n", "", 0)
            result = await backend.get_total_size("ext:log")
            assert result == 1048576

    @pytest.mark.asyncio
    async def test_health_check_ok(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("1.4.1.1024\n", "", 0)
            status = await backend.health_check()
            assert status["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_invalid_config(self, invalid_config):
        backend = EverythingBackend(invalid_config)
        status = await backend.health_check()
        assert status["status"] == "error"


# ── SORT_MAP / FILE_TYPES / TIME_PERIODS consistency ─────────────────────


class TestConstants:
    def test_sort_map_has_expected_keys(self):
        expected = {"name", "size", "size-desc", "date-modified-desc", "extension"}
        assert expected.issubset(SORT_MAP.keys())

    def test_file_types_all_start_with_ext(self):
        for name, query in FILE_TYPES.items():
            assert query.startswith("ext:"), f"{name} doesn't start with ext:"

    def test_time_periods_all_have_values(self):
        for key, value in TIME_PERIODS.items():
            assert value, f"TIME_PERIODS[{key}] is empty"
