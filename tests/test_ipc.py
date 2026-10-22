"""Tests for the native IPC transport (everything_mcp.ipc) and its integration.

The LIST2 parser runs on any OS (synthetic bytes); detection/backend routing are
mocked so no Everything is needed.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest

from everything_mcp.backend import SORT_MAP, EverythingBackend, _ipc_to_result, _join_terms
from everything_mcp.config import EverythingConfig
from everything_mcp.ipc import (
    REQ_DATE_CREATED,
    REQ_DATE_MODIFIED,
    REQ_FULL_PATH,
    REQ_SIZE,
    SORT_IDS,
    IpcItem,
    IpcResult,
    parse_list2,
    window_class,
)

# 2026-01-15 10:30:00 UTC as FILETIME
FT = int((1768473000 + 11644473600) * 10_000_000)


def _list2(
    items: list[tuple[str, bool, int]], total: int | None = None, request: int | None = None
) -> bytes:
    """Build an EVERYTHING_IPC_LIST2 reply the way Everything lays it out."""
    request = (
        REQ_FULL_PATH | REQ_SIZE | REQ_DATE_CREATED | REQ_DATE_MODIFIED
        if request is None
        else request
    )
    header = struct.pack(
        "<5I", total if total is not None else len(items), len(items), 0, request, 1
    )
    table_size = 8 * len(items)
    data = b""
    table = b""
    base = len(header) + table_size
    for path, is_dir, size in items:
        table += struct.pack("<2I", 1 if is_dir else 0, base + len(data))
        if request & REQ_FULL_PATH:
            data += struct.pack("<I", len(path)) + path.encode("utf-16le") + b"\0\0"
        if request & REQ_SIZE:
            data += struct.pack("<q", size)
        if request & REQ_DATE_CREATED:
            data += struct.pack("<Q", FT)
        if request & REQ_DATE_MODIFIED:
            data += struct.pack("<Q", FT)
    return header + table + data


class TestParseList2:
    def test_files_folders_unicode_unc(self):
        buf = _list2(
            [
                (r"\\192.168.0.141\공정기술팀_NAS\반사율 측정\result.xlsx", False, 2048),
                (r"C:\Projects\app\src", True, -1),
            ],
            total=7,
        )
        res = parse_list2(buf)
        assert res.total == 7
        assert [i.full_path for i in res.items] == [
            r"\\192.168.0.141\공정기술팀_NAS\반사율 측정\result.xlsx",
            r"C:\Projects\app\src",
        ]
        assert res.items[0].size == 2048 and not res.items[0].is_dir
        assert res.items[1].is_dir and res.items[1].size == -1
        assert res.items[0].date_modified.startswith("2026-01-15")

    def test_count_only_reply(self):
        res = parse_list2(_list2([], total=123456))
        assert res.total == 123456 and res.items == []

    def test_path_only_request(self):
        res = parse_list2(_list2([(r"C:\a.txt", False, 5)], request=REQ_FULL_PATH))
        assert res.items[0].full_path == r"C:\a.txt"
        assert res.items[0].size == -1 and res.items[0].date_modified == ""

    def test_invalid_filetime_is_empty(self):
        buf = bytearray(_list2([(r"C:\a.txt", False, 1)]))
        buf[-16:] = struct.pack("<QQ", 0xFFFFFFFFFFFFFFFF, 0)
        res = parse_list2(bytes(buf))
        assert res.items[0].date_created == "" and res.items[0].date_modified == ""


class TestHelpers:
    def test_window_class_default_and_named(self):
        assert window_class("") == "EVERYTHING_TASKBAR_NOTIFICATION"
        assert window_class("1.5a") == "EVERYTHING_TASKBAR_NOTIFICATION_(1.5a)"

    def test_every_sort_key_maps_to_ipc(self):
        assert set(SORT_MAP) <= set(SORT_IDS)

    def test_join_terms_regex_applies_to_first_term_only(self):
        assert _join_terms(["^test_.*", "ext:py"], ["-r"]) == "regex:^test_.* ext:py"
        assert _join_terms(['path:"C:\\My Docs"', "ext:md"]) == 'path:"C:\\My Docs" ext:md'

    def test_ipc_to_result_formats_like_stat_path(self):
        r = _ipc_to_result(IpcItem(r"C:\x\Report.PDF", False, 10, "2026-01-15T19:30:00+09:00", ""))
        assert r.name == "Report.PDF" and r.extension == "pdf" and r.size == 10
        assert r.date_modified == "2026-01-15 19:30:00"


@pytest.mark.ipc
class TestDetection:
    def _transport(self, available=True, version="Everything v1.4.1.1032", busy=False):
        t = MagicMock()
        t.available.return_value = available
        if busy:
            from everything_mcp.ipc import IpcError

            t.version.side_effect = IpcError("busy")
        else:
            t.version.return_value = version
        return t

    @pytest.fixture(autouse=True)
    def _windows(self, monkeypatch):
        import everything_mcp.config as config_mod

        monkeypatch.setattr(config_mod.sys, "platform", "win32")

    def test_ipc_without_es_exe_is_valid(self):
        with (
            patch("everything_mcp.config._find_es_exe", return_value=""),
            patch("everything_mcp.ipc.IpcTransport", return_value=self._transport()),
        ):
            config = EverythingConfig.auto_detect()
        assert config.is_valid and config.transport == "ipc" and config.es_path == ""
        assert config.version_info == "Everything v1.4.1.1032"

    def test_named_instance_fallback(self):
        transports = {
            "": self._transport(available=False),
            "1.5a": self._transport(version="Everything v1.5.0.1400"),
        }
        with (
            patch("everything_mcp.config._find_es_exe", return_value=""),
            patch(
                "everything_mcp.ipc.IpcTransport",
                side_effect=lambda inst, timeout: transports[inst],
            ),
        ):
            config = EverythingConfig.auto_detect()
        assert config.instance == "1.5a" and config.transport == "ipc"

    def test_busy_window_reports_busy_not_missing(self):
        with (
            patch("everything_mcp.config._find_es_exe", return_value=""),
            patch("everything_mcp.ipc.IpcTransport", return_value=self._transport(busy=True)),
        ):
            config = EverythingConfig.auto_detect()
        assert not config.is_valid and "busy" in config.errors[0]

    def test_force_es_transport(self, monkeypatch):
        monkeypatch.setenv("EVERYTHING_TRANSPORT", "es")
        with (
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(True, "Everything v1.4")),
            patch("everything_mcp.ipc.IpcTransport") as ipc,
        ):
            config = EverythingConfig.auto_detect()
        ipc.assert_not_called()
        assert config.transport == "es"

    def test_force_ipc_without_window_is_error(self, monkeypatch):
        monkeypatch.setenv("EVERYTHING_TRANSPORT", "ipc")
        with (
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.ipc.IpcTransport", return_value=self._transport(available=False)),
        ):
            config = EverythingConfig.auto_detect()
        assert not config.is_valid and "IPC window" in config.errors[0]


class TestBackendRouting:
    @pytest.fixture
    def ipc_backend(self):
        cfg = EverythingConfig(transport="ipc", version_info="Everything v1.4.1.1032")
        return EverythingBackend(cfg)

    async def test_search_uses_ipc_and_skips_stat(self, ipc_backend):
        t = MagicMock()
        t.query.return_value = IpcResult(
            2, [IpcItem(r"\\nas\share\a.img", False, 42, "2026-01-15T10:30:00+00:00", "")]
        )
        with (
            patch("everything_mcp.ipc.IpcTransport", return_value=t),
            patch("everything_mcp.backend._stat_to_result") as stat,
        ):
            res = await ipc_backend.search(
                "ext:img path:\\\\nas\\share", max_results=10, sort="size-desc"
            )
        stat.assert_not_called()
        assert res[0].size == 42 and res[0].path == r"\\nas\share\a.img"
        kwargs = t.query.call_args.kwargs
        assert kwargs["sort"] == "size-desc" and kwargs["max_results"] == 10
        assert t.query.call_args.args[0] == "ext:img path:\\\\nas\\share"
        # date created is not indexed by default: requesting it would make
        # Everything read every result from disk (22 s for 500 SMB files).
        assert kwargs["date_created"] is False
        assert kwargs["size"] is True and kwargs["date_modified"] is True

    async def test_search_requests_date_created_when_indexed(self, ipc_backend):
        ipc_backend.config.indexed["index_date_created"] = True
        t = MagicMock()
        t.query.return_value = IpcResult(0, [])
        with patch("everything_mcp.ipc.IpcTransport", return_value=t):
            await ipc_backend.search("x", max_results=1, sort="name")
        assert t.query.call_args.kwargs["date_created"] is True

    async def test_count_uses_ipc(self, ipc_backend):
        t = MagicMock()
        t.count.return_value = 6_115_529
        with patch("everything_mcp.ipc.IpcTransport", return_value=t):
            assert await ipc_backend.count("*") == 6_115_529

    async def test_total_size_without_es_is_unavailable(self, ipc_backend):
        with patch("everything_mcp.ipc.IpcTransport"):
            assert await ipc_backend.get_total_size("ext:log") == -1

    async def test_ipc_error_is_reported(self, ipc_backend):
        from everything_mcp.ipc import IpcError

        t = MagicMock()
        t.query.side_effect = IpcError("Everything rejected the query")
        with (
            patch("everything_mcp.ipc.IpcTransport", return_value=t),
            pytest.raises(RuntimeError, match="rejected"),
        ):
            await ipc_backend.search("x", max_results=1)

    async def test_health_reports_transport(self, ipc_backend):
        t = MagicMock()
        t.version.return_value = "Everything v1.4.1.1032"
        with patch("everything_mcp.ipc.IpcTransport", return_value=t):
            h = await ipc_backend.health_check()
        assert h["status"] == "ok" and h["transport"] == "ipc"


@pytest.mark.ipc
@pytest.mark.skipif(not __import__("sys").platform.startswith("win"), reason="Windows only")
def test_live_ipc_roundtrip():
    """Real Everything, if running: QUERY2 returns paths with metadata."""
    from everything_mcp.ipc import IpcTransport

    t = IpcTransport("", timeout=10)
    if not t.available():
        pytest.skip("Everything is not running")
    res = t.query("everything.exe", max_results=3)
    assert res.total >= 1
    assert any(i.full_path.lower().endswith("everything.exe") for i in res.items)
