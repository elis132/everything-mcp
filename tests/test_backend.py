"""Tests for everything_mcp.backend."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

import everything_mcp.backend as backend_mod
from everything_mcp.backend import (
    FILE_TYPES,
    SORT_MAP,
    TIME_PERIODS,
    EverythingBackend,
    SearchResult,
    _decode_output,
    _looks_like_path,
    _parse_paths_and_stat,
    _split_query_terms,
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
        assert "dm:last1hours" in q

    def test_today(self):
        q = build_recent_query("today")
        assert "dm:today" in q

    def test_with_path(self):
        q = build_recent_query("1week", path_filter=r"C:\Projects")
        assert "dm:last7days" in q
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


# ── _split_query_terms ────────────────────────────────────────────────────


class TestSplitQueryTerms:
    def test_single_term(self):
        assert _split_query_terms("*.py") == ["*.py"]

    def test_multi_term_and(self):
        assert _split_query_terms("dm:today ext:md") == ["dm:today", "ext:md"]

    def test_quoted_phrase_kept_together(self):
        assert _split_query_terms('"exact name.txt"') == ['"exact name.txt"']

    def test_quoted_path_filter(self):
        assert _split_query_terms('ext:md path:"C:\\My Documents"') == [
            "ext:md",
            'path:"C:\\My Documents"',
        ]

    def test_mixed_quoted_and_plain(self):
        assert _split_query_terms('dupe: path:"C:\\Users\\me\\My Docs" ext:py') == [
            "dupe:",
            'path:"C:\\Users\\me\\My Docs"',
            "ext:py",
        ]

    def test_multiple_spaces_collapsed(self):
        assert _split_query_terms("ext:py   dm:today") == ["ext:py", "dm:today"]

    def test_empty_query(self):
        assert _split_query_terms("") == []

    def test_whitespace_only(self):
        assert _split_query_terms("   ") == []

    def test_unclosed_quote_consumes_rest(self):
        # Closed at the token's end, so reordered terms after it stay separate.
        assert _split_query_terms('path:"C:\\My Documents') == ['path:"C:\\My Documents"']

    def test_or_and_negation_terms_pass_through(self):
        assert _split_query_terms("project1 | project2 !node_modules") == [
            "project1",
            "|",
            "project2",
            "!node_modules",
        ]


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
                    "test",
                    match_case=True,
                    match_whole_word=True,
                    match_regex=True,
                    match_path=True,
                    offset=50,
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
            cmd = mock_run.call_args[0][0]
            assert "-get-result-count" in cmd
            assert "-n" not in cmd

    @pytest.mark.asyncio
    async def test_search_multi_term_query_split_into_args(self, backend):
        """Multi-term queries must become separate argv elements (AND logic)."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            with patch("everything_mcp.backend._parse_paths_and_stat", return_value=[]):
                await backend.search("dm:today ext:md")
            cmd = mock_run.call_args[0][0]
            assert "dm:today" in cmd
            assert "ext:md" in cmd
            assert "dm:today ext:md" not in cmd

    @pytest.mark.asyncio
    async def test_count_multi_term_query_split_into_args(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("7\n", "", 0)
            result = await backend.count('ext:md path:"C:\\My Docs"')
            assert result == 7
            cmd = mock_run.call_args[0][0]
            assert "ext:md" in cmd
            assert 'path:"C:\\My Docs"' in cmd

    @pytest.mark.asyncio
    async def test_count_uint64_error_sentinel(self, backend):
        """es.exe prints unsigned -1 when the count is unavailable."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (str(2**64 - 1) + "\n", "", 0)
            assert await backend.count("ext:py") == -1

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
            cmd = mock_run.call_args[0][0]
            assert "-get-total-size" in cmd
            assert "-n" not in cmd

    @pytest.mark.asyncio
    async def test_get_total_size_uint64_error_sentinel(self, backend):
        """es.exe prints unsigned -1 (16384 PB!) when the size is unavailable."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("18446744073709551615\n", "", 0)
            assert await backend.get_total_size("ext:py") == -1

    @pytest.mark.asyncio
    async def test_get_total_size_multi_term_query_split_into_args(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("2048\n", "", 0)
            result = await backend.get_total_size("ext:log dm:today")
            assert result == 2048
            cmd = mock_run.call_args[0][0]
            assert "ext:log" in cmd
            assert "dm:today" in cmd
            assert "ext:log dm:today" not in cmd

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


# ── Agreement with es.exe's own argument parser ───────────────────────────


def _es_argv(command_line: str) -> list[str]:
    """Port of es.exe's default-mode parser (es.c ``_es_get_argv``, 1.1.0.38).

    Unquoted space/tab/CR/LF end an argument; ``\"\"\"`` becomes a literal
    ``&quot:`` without toggling quoting; single quotes toggle and are kept.
    """
    args, i, n = [], 0, len(command_line)
    while True:
        while i < n and command_line[i] in " \t\r\n":
            i += 1
        if i >= n:
            return args
        out, in_quote = [], False
        while i < n and (in_quote or command_line[i] not in " \t\r\n"):
            if command_line.startswith('"""', i):
                out.append("&quot:")
                i += 3
                continue
            if command_line[i] == '"':
                in_quote = not in_quote
            out.append(command_line[i])
            i += 1
        args.append("".join(out))


_OUR_OPTIONS = {"-n", "-o", "-sort", "-case", "-w", "-p", "-r", "-instance"}
_OUR_OPTIONS |= {"-get-result-count", "-get-total-size"}


class TestEsParserAgreement:
    def test_tokens_are_exactly_the_arguments_es_exe_sees(self):
        import random

        rng = random.Random(18)
        alphabet = ' \t\r\n"ab-/'
        for _ in range(3000):
            query = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
            tokens = _split_query_terms(query)
            assert len(_es_argv(query)) == len(tokens), repr(query)
            assert len(_es_argv(" ".join(tokens))) == len(tokens), repr(query)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query",
        [
            'x""" -exit',
            'ext:py"a"\t-exit',
            '"a"\n-save-db',
            "a\r\n/reindex",
            '"x" -export-csv C:\\out.csv',
            "-exit",
        ],
    )
    async def test_no_query_term_reaches_es_exe_as_a_switch(self, backend, query):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            await backend.search(query)
        es_args = _es_argv(backend_mod._command_line(mock_run.call_args.args[0]))[1:]
        options = {a for a in es_args if a.startswith(("-", "/"))}
        assert options <= _OUR_OPTIONS, options

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("query", "regex"),
        [
            ('regex:"a"\tcontent:secret', False),
            ('regex:x""" content:secret', False),
            ("regex:a\ncontent:x", False),
            ('x""" content:secret', True),
        ],
    )
    async def test_disk_term_split_off_by_es_exe_is_still_guarded(self, backend, query, regex):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("5000\n", "", 0)
            with pytest.raises(RuntimeError, match="content:"):
                await backend.search(query, match_regex=regex)
        assert "-get-result-count" in mock_run.call_args_list[0].args[0]

    @pytest.mark.asyncio
    async def test_slow_regex_head_is_refused_not_moved(self, backend):
        with (
            patch.object(backend, "_run", new_callable=AsyncMock) as mock_run,
            pytest.raises(RuntimeError, match="the regex must not use content:"),
        ):
            await backend.search("content:x ext:py", match_regex=True)
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("glue", ["OR", "NOT", "AND", "(ext:py", "ext:py)", "!"])
    async def test_literal_operators_and_brackets_count_as_grouping(self, backend, glue):
        with (
            patch.object(backend, "_run", new_callable=AsyncMock) as mock_run,
            pytest.raises(RuntimeError, match="cannot be combined"),
        ):
            await backend.search(f"ext:txt {glue} content:x")
        mock_run.assert_not_called()


# ── _run process handling (#18) ───────────────────────────────────────────

_SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(30)"]
_QUICK_CMD = [sys.executable, "-c", "print('ok')"]


class TestRun:
    @pytest.fixture
    def spawned(self):
        """Record every process _run starts; kill leftovers at teardown."""
        procs = []
        real_popen = subprocess.Popen

        def spy(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            procs.append(proc)
            return proc

        with patch("everything_mcp.backend.subprocess.Popen", spy):
            yield procs
        for proc in procs:
            if proc.poll() is None:
                proc.kill()

    @pytest.mark.asyncio
    async def test_timeout_keeps_es_running_and_reports_busy(self, backend, spawned):
        backend.config.timeout = 0.5
        with pytest.raises(RuntimeError, match="timed out"):
            await backend._run(_SLEEP_CMD)
        es = spawned[0]
        # Killing es.exe would not cancel its query inside Everything; its
        # exit is the only signal that Everything is free again.
        assert es.returncode is None
        with pytest.raises(RuntimeError, match="still running an earlier query"):
            await backend._run(_QUICK_CMD)
        assert len(spawned) == 1  # nothing queued behind the slow query
        assert (await backend.health_check())["status"] == "busy"

        es.kill()
        await backend._pending
        stdout, _, rc = await backend._run(_QUICK_CMD)
        assert (stdout.strip(), rc) == ("ok", 0)

    @pytest.mark.asyncio
    async def test_cancel_keeps_es_running_and_reports_busy(self, backend, spawned):
        task = asyncio.create_task(backend._run(_SLEEP_CMD))
        while not spawned:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RuntimeError, match="still running an earlier query"):
            await backend._run(_QUICK_CMD)
        spawned[0].kill()
        await backend._pending

    @pytest.mark.asyncio
    async def test_pending_es_keeps_other_sessions_out(self, backend, spawned):
        backend.config.timeout = 0.5
        with pytest.raises(RuntimeError, match="timed out"):
            await backend._run(_SLEEP_CMD)
        assert backend_mod._try_machine_lock() is None  # another session would wait

        spawned[0].kill()
        await backend._pending
        fd = backend_mod._try_machine_lock()
        assert fd is not None and fd >= 0
        backend_mod._release_machine_lock(fd)

    @pytest.mark.asyncio
    async def test_other_session_query_makes_calls_fail_busy(self, backend, spawned, monkeypatch):
        monkeypatch.setattr(backend_mod, "_MACHINE_LOCK_WAIT", 0.3)
        other = backend_mod._try_machine_lock()  # another session's es.exe is in Everything
        try:
            with pytest.raises(RuntimeError, match="another everything-mcp session"):
                await backend._run(_QUICK_CMD)
            assert not spawned
        finally:
            backend_mod._release_machine_lock(other)
        stdout, _, _ = await backend._run(_QUICK_CMD)
        assert stdout.strip() == "ok"

    @pytest.mark.asyncio
    async def test_queued_calls_share_one_wait_for_another_session(self, backend, monkeypatch):
        monkeypatch.setattr(backend_mod, "_MACHINE_LOCK_WAIT", 0.3)
        other = backend_mod._try_machine_lock()
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            results = await asyncio.gather(
                *(backend._run(_QUICK_CMD) for _ in range(4)), return_exceptions=True
            )
        finally:
            backend_mod._release_machine_lock(other)
        assert all("another everything-mcp session" in str(r) for r in results)
        assert loop.time() - start < 0.9  # not 4 x 0.3 s one after another

    @pytest.mark.asyncio
    async def test_waiting_session_gets_a_turn_after_our_release(self, backend):
        await backend._run(_QUICK_CMD)
        # Right after our release, another session polling every 50 ms must win.
        other = backend_mod._try_machine_lock()
        assert other is not None and other >= 0
        task = asyncio.create_task(backend._run(_QUICK_CMD))
        await asyncio.sleep(0.2)
        assert not task.done()  # we wait for the other session's query
        backend_mod._release_machine_lock(other)
        stdout, _, _ = await task
        assert stdout.strip() == "ok"

    @pytest.mark.asyncio
    async def test_own_release_pauses_before_retaking_the_lock(self, backend):
        await backend._run(_QUICK_CMD)
        loop = asyncio.get_running_loop()
        start = loop.time()
        fd = await backend_mod._acquire_machine_lock(loop.time() + 5)
        backend_mod._release_machine_lock(fd)
        assert loop.time() - start >= backend_mod._LOCK_YIELD * 0.5

    @pytest.mark.asyncio
    async def test_everything_restart_drops_pending_es(self, backend, spawned, monkeypatch):
        windows = iter([111] + [222] * 1000)  # IPC window at spawn, then a new Everything
        monkeypatch.setattr(backend_mod, "_ipc_window", lambda instance: next(windows))
        monkeypatch.setattr(backend_mod, "_WATCH_INTERVAL", 0.05)
        backend.config.timeout = 0.5
        with pytest.raises(RuntimeError, match="timed out"):
            await backend._run(_SLEEP_CMD)
        backend.config.timeout = 30
        await asyncio.wait_for(backend._pending, 10)  # watcher killed the orphaned es.exe
        stdout, _, _ = await backend._run(_QUICK_CMD)
        assert stdout.strip() == "ok"

    @pytest.mark.asyncio
    async def test_calls_run_one_at_a_time(self, backend):
        active = peak = 0

        class FakeProcess:
            returncode = 0

            def __init__(self, *args, **kwargs):
                pass

            def communicate(self):  # runs in a worker thread, like the real one
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                time.sleep(0.05)
                active -= 1
                return b"", b""

        with patch("everything_mcp.backend.subprocess.Popen", FakeProcess):
            await asyncio.gather(*(backend._run(["es.exe"]) for _ in range(3)))
        assert peak == 1

    def test_command_line_keeps_query_quotes(self):
        """es.exe parses its own command line: path:"C:\\x y" must arrive as typed."""
        line = backend_mod._command_line(
            [r"C:\Program Files\Everything\es.exe", "-n", "5", r'path:"C:\Program Files\WSL"']
        )
        assert line == r'"C:\Program Files\Everything\es.exe" -n 5 path:"C:\Program Files\WSL"'

    @pytest.mark.asyncio
    @pytest.mark.parametrize("term", ["-exit", "/reindex", "-export-csv", "--"])
    async def test_query_terms_never_become_es_switches(self, backend, term):
        """es.exe runs -exit (quit Everything), -reindex, -export-csv <file> as options."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            await backend.search(f"ext:py {term}")
        assert mock_run.call_args.args[0][-1] == f'"{term}"'


# ── Disk-reading query guard (#18) ────────────────────────────────────────


def _es_calls(mock_run) -> list[list[str]]:
    return [call.args[0] for call in mock_run.call_args_list]


class TestDiskGuard:
    @pytest.mark.asyncio
    async def test_disk_term_runs_last_after_candidate_count(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [("12\n", "", 0), ("4096\n", "", 0), ("", "", 0)]
            await backend.search(r"content:TODO ext:py path:D:\proj")
        precount, presize, search = _es_calls(mock_run)
        assert "-get-result-count" in precount
        assert precount[-2:] == ["ext:py", r"path:D:\proj"]
        assert "-get-total-size" in presize
        assert search[-3:] == ["ext:py", r"path:D:\proj", "content:TODO"]

    @pytest.mark.asyncio
    async def test_content_refused_over_byte_budget(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [("3\n", "", 0), (f"{5 * 1024**3}\n", "", 0)]
            with pytest.raises(RuntimeError, match=r"matches 5.0 GB of files"):
                await backend.search("ext:iso content:x")

    @pytest.mark.asyncio
    async def test_precount_uses_the_query_match_flags(self, backend):
        """match_path makes Everything match whole paths: far more candidates (#18 review)."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("2590840\n", "", 0)
            with pytest.raises(RuntimeError, match="2,590,840 items"):
                await backend.search("src dc:today", match_path=True)
        assert "-p" in _es_calls(mock_run)[0]

    @pytest.mark.asyncio
    async def test_regex_mode_guards_terms_after_the_regex(self, backend):
        """es.exe -r takes only the next argument as the regex."""
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [("5\n", "", 0), ("10\n", "", 0), ("", "", 0)]
            await backend.search(r"test_\d+ content:x", match_regex=True)
        precount, _, search = _es_calls(mock_run)
        assert precount[-2:] == ["-r", r"test_\d+"]
        assert search[-3:] == ["-r", r"test_\d+", "content:x"]

    @pytest.mark.asyncio
    async def test_regex_token_with_or_is_guarded(self, backend):
        with (
            patch.object(backend, "_run", new_callable=AsyncMock) as mock_run,
            pytest.raises(RuntimeError, match="content: cannot be combined"),
        ):
            await backend.search("regex:x|content:y")
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_candidate_count_refused(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (f"{2**64 - 1}\n", "", 0)
            with pytest.raises(RuntimeError, match="could not count"):
                await backend.search("ext:py dc:today")

    @pytest.mark.asyncio
    async def test_too_many_candidates_refused(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("5000\n", "", 0)
            with pytest.raises(
                RuntimeError, match=r"matches 5,000 items; the limit for content: is 1,000"
            ):
                await backend.search("ext:py content:TODO")
        assert len(_es_calls(mock_run)) == 1  # only the cheap count ran

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query",
        [
            "content:TODO",
            "dc:today",
            "width:>100",
            "artist:abba",
            '"content":TODO',
            'con""tent:TODO',
        ],
    )
    async def test_unscoped_disk_term_refused_without_touching_everything(self, backend, query):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            with pytest.raises(RuntimeError, match="needs a narrowing filter"):
                await backend.search(query)
            with pytest.raises(RuntimeError, match="needs a narrowing filter"):
                await backend.count(query)
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_or_groups_with_disk_term_refused(self, backend):
        with (
            patch.object(backend, "_run", new_callable=AsyncMock) as mock_run,
            pytest.raises(RuntimeError, match="cannot be combined"),
        ):
            await backend.search("ext:py content:a | b.txt")
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query",
        [r"ext:md path:D:\content", r"regex:^content_\d+$", "ext:py dm:today", "ext:log size:>1mb"],
    )
    async def test_index_served_queries_run_directly(self, backend, query):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            await backend.search(query)
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_indexed_date_created_runs_directly(self, backend):
        backend.config.indexed["index_date_created"] = True
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            await backend.search("dc:today")
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_plain_regex_argument_not_guarded(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            await backend.search(r"^test_\d+\.py$", match_regex=True)
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("query", "regex"),
        [("regex:content:secret", False), ("case:regex:artist:x", False), ("content:x", True)],
    )
    async def test_function_after_regex_modifier_is_guarded(self, backend, query, regex):
        """es.exe -r sends "regex:" + the argument: regex:content:x is a content search."""
        with (
            patch.object(backend, "_run", new_callable=AsyncMock) as mock_run,
            pytest.raises(RuntimeError, match="needs a narrowing filter"),
        ):
            await backend.search(query, match_regex=regex)
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_byte_budget_applies_when_another_slow_term_comes_first(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [("10\n", "", 0), (f"{50 * 1024**3}\n", "", 0)]
            with pytest.raises(RuntimeError, match=r"the limit for content: is 100\.0 MB"):
                await backend.search(r"ext:jpg path:C:\x width:>100 content:foo")

    @pytest.mark.asyncio
    async def test_everything_15_not_guarded(self, config_15a):
        backend = EverythingBackend(config_15a)
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            await backend.search("content:TODO")
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_date_created_sort_refused_unless_indexed(self, backend):
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)
            with pytest.raises(RuntimeError, match="Index date created"):
                await backend.search("ext:py", sort="date-created-desc")
            mock_run.assert_not_called()
            backend.config.indexed["index_date_created"] = True
            await backend.search("ext:py", sort="date-created-desc")
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_size_sort_refused_when_size_not_indexed(self, backend):
        backend.config.indexed["index_size"] = False
        with pytest.raises(RuntimeError, match="Index size"):
            await backend.search("ext:py", sort="size-desc")


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
