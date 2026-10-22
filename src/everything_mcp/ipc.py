"""Native Everything IPC transport (WM_COPYDATA QUERY2) - no es.exe, no SDK DLL.

Protocol (voidtools SDK ``ipc/everything_ipc.h``, same as es.exe uses internally):

- The IPC window class is ``EVERYTHING_TASKBAR_NOTIFICATION``; a named instance
  appends ``_(<name>)`` (es.c ``_es_get_window_classname``), e.g. ``..._(1.5a)``.
- A query is one ``WM_COPYDATA`` with ``dwData = EVERYTHING_IPC_COPYDATA_QUERY2W``
  and an ``EVERYTHING_IPC_QUERY2`` header followed by the UTF-16 search text.
- Everything replies with ``WM_COPYDATA`` to our window carrying
  ``EVERYTHING_IPC_LIST2`` + ``ITEM2[numitems]`` + packed per-item data.  Size and
  dates come back in the reply, so results need no ``os.stat()`` (which costs one
  SMB round trip per result on network shares).

Each call creates a private message-only window, so calls are independent and
thread-safe; :class:`IpcTransport` is used from ``asyncio.to_thread``.
"""

from __future__ import annotations

import ctypes
import itertools
import struct
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

__all__ = ["IpcError", "IpcResult", "IpcTransport", "SORT_IDS", "window_class"]

IPC_WNDCLASS = "EVERYTHING_TASKBAR_NOTIFICATION"
WM_USER = 0x0400
WM_COPYDATA = 0x004A
SMTO_ABORTIFHUNG = 0x0002
PM_REMOVE = 0x0001
COPYDATA_QUERY2W = 18

GET_MAJOR, GET_MINOR, GET_REVISION, GET_BUILD = 0, 1, 2, 3
IS_DB_LOADED = 401
IS_DB_BUSY = 402

MATCHCASE, MATCHWHOLEWORD, MATCHPATH, REGEX = 0x1, 0x2, 0x4, 0x8
ITEM_FOLDER = 0x1

REQ_NAME = 0x1
REQ_PATH = 0x2
REQ_FULL_PATH = 0x4
REQ_EXTENSION = 0x8
REQ_SIZE = 0x10
REQ_DATE_CREATED = 0x20
REQ_DATE_MODIFIED = 0x40
_FIELDS: list[tuple[int, str, str]] = [  # order of the packed per-item data (everything_ipc.h)
    (REQ_NAME, "name", "str"),
    (REQ_PATH, "path", "str"),
    (REQ_FULL_PATH, "full_path", "str"),
    (REQ_EXTENSION, "extension", "str"),
    (REQ_SIZE, "size", "i64"),
    (REQ_DATE_CREATED, "date_created", "ft"),
    (REQ_DATE_MODIFIED, "date_modified", "ft"),
]

# Friendly sort keys (backend.SORT_MAP keys) -> EVERYTHING_IPC_SORT_*.
SORT_IDS: dict[str, int] = {
    "name": 1,
    "name-desc": 2,
    "path": 3,
    "path-desc": 4,
    "size": 5,
    "size-asc": 5,
    "size-desc": 6,
    "extension": 7,
    "date-created": 11,
    "date-created-asc": 11,
    "date-created-desc": 12,
    "date-modified": 13,
    "date-modified-asc": 13,
    "date-modified-desc": 14,
}

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_class_ids = itertools.count()
_class_lock = threading.Lock()


class IpcError(RuntimeError):
    """Everything did not answer over IPC (absent, hung, or busy)."""


@dataclass(slots=True)
class IpcItem:
    full_path: str
    is_dir: bool
    size: int = -1
    date_modified: str = ""
    date_created: str = ""


@dataclass(slots=True)
class IpcResult:
    total: int
    items: list[IpcItem]


def window_class(instance: str) -> str:
    """IPC window class for *instance* ('' = default instance)."""
    return f"{IPC_WNDCLASS}_({instance})" if instance else IPC_WNDCLASS


def _filetime(raw: int) -> str:
    if raw in (0, 0xFFFFFFFFFFFFFFFF):
        return ""
    try:
        return (
            (_FILETIME_EPOCH + timedelta(microseconds=raw // 10))
            .astimezone()
            .isoformat(timespec="seconds")
        )
    except OverflowError:  # corrupt timestamps exist on real disks
        return ""


def parse_list2(buf: bytes) -> IpcResult:
    """Parse an EVERYTHING_IPC_LIST2 reply (pure, unit-testable on any OS)."""
    total, numitems, _offset, request_flags, _sort = struct.unpack_from("<5I", buf, 0)
    items: list[IpcItem] = []
    for i in range(numitems):
        flags, pos = struct.unpack_from("<2I", buf, 20 + i * 8)
        values: dict = {}
        for bit, key, kind in _FIELDS:
            if not request_flags & bit:
                continue
            if kind == "str":
                (length,) = struct.unpack_from("<I", buf, pos)
                pos += 4
                values[key] = buf[pos : pos + length * 2].decode("utf-16le")
                pos += (length + 1) * 2
            elif kind == "i64":
                (values[key],) = struct.unpack_from("<q", buf, pos)
                pos += 8
            else:
                (raw,) = struct.unpack_from("<Q", buf, pos)
                values[key] = _filetime(raw)
                pos += 8
        is_dir = bool(flags & ITEM_FOLDER)
        items.append(
            IpcItem(
                full_path=values.get("full_path", ""),
                is_dir=is_dir,
                size=-1 if is_dir else max(values.get("size", -1), -1),
                date_modified=values.get("date_modified", ""),
                date_created=values.get("date_created", ""),
            )
        )
    return IpcResult(total=total, items=items)


class IpcTransport:
    """Synchronous IPC client for one Everything instance (Windows only)."""

    def __init__(self, instance: str = "", timeout: float = 30.0) -> None:
        if sys.platform != "win32":
            raise IpcError("Everything IPC is only available on Windows")
        self.instance = instance
        self.timeout = timeout
        self._u32 = ctypes.WinDLL("user32", use_last_error=True)
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._setup()

    # ── public API ────────────────────────────────────────────────────

    def available(self) -> bool:
        return bool(self._u32.FindWindowW(window_class(self.instance), None))

    def version(self) -> str:
        parts = [self._command(c) for c in (GET_MAJOR, GET_MINOR, GET_REVISION, GET_BUILD)]
        return "Everything v" + ".".join(str(p) for p in parts)

    def db_loaded(self) -> bool:
        return bool(self._command(IS_DB_LOADED))

    def query(
        self,
        search: str,
        *,
        max_results: int,
        offset: int = 0,
        sort: str = "name",
        match_case: bool = False,
        match_whole_word: bool = False,
        match_path: bool = False,
        match_regex: bool = False,
        size: bool = True,
        date_modified: bool = True,
        date_created: bool = False,
    ) -> IpcResult:
        """Run one QUERY2.  Only request properties Everything has indexed:
        an unindexed one (date created by default) is read from disk for every
        result - measured 22 s for 500 results on an SMB share vs 0.9 s.
        """
        flags = (MATCHCASE if match_case else 0) | (MATCHWHOLEWORD if match_whole_word else 0)
        flags |= (MATCHPATH if match_path else 0) | (REGEX if match_regex else 0)
        request = REQ_FULL_PATH
        request |= REQ_SIZE if size else 0
        request |= REQ_DATE_MODIFIED if date_modified else 0
        request |= REQ_DATE_CREATED if date_created else 0
        return parse_list2(
            self._query2(search, flags, offset, max_results, request, SORT_IDS.get(sort, 1))
        )

    def count(self, search: str, *, match_path: bool = False) -> int:
        """Total matches without transferring rows (max_results = 0)."""
        flags = MATCHPATH if match_path else 0
        return parse_list2(self._query2(search, flags, 0, 0, REQ_FULL_PATH, 1)).total

    # ── internals ─────────────────────────────────────────────────────

    def _setup(self) -> None:
        from ctypes import wintypes as w

        self._w = w
        lresult = ctypes.c_ssize_t
        self._WNDPROC = ctypes.WINFUNCTYPE(lresult, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class COPYDATASTRUCT(ctypes.Structure):
            _fields_ = [
                ("dwData", ctypes.c_size_t),
                ("cbData", w.DWORD),
                ("lpData", ctypes.c_void_p),
            ]

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", w.UINT),
                ("style", w.UINT),
                ("lpfnWndProc", self._WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE),
                ("hIcon", w.HICON),
                ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR),
                ("hIconSm", w.HICON),
            ]

        self._COPYDATASTRUCT = COPYDATASTRUCT
        self._WNDCLASSEXW = WNDCLASSEXW
        u = self._u32
        u.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
        u.FindWindowW.restype = w.HWND
        u.SendMessageTimeoutW.argtypes = [
            w.HWND,
            w.UINT,
            w.WPARAM,
            w.LPARAM,
            w.UINT,
            w.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        u.SendMessageTimeoutW.restype = lresult
        u.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.DefWindowProcW.restype = lresult
        u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        u.RegisterClassExW.restype = w.ATOM
        u.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        u.CreateWindowExW.argtypes = [
            w.DWORD,
            w.LPCWSTR,
            w.LPCWSTR,
            w.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            w.HWND,
            w.HMENU,
            w.HINSTANCE,
            w.LPVOID,
        ]
        u.CreateWindowExW.restype = w.HWND
        u.DestroyWindow.argtypes = [w.HWND]
        u.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        u.MsgWaitForMultipleObjects.argtypes = [w.DWORD, ctypes.c_void_p, w.BOOL, w.DWORD, w.DWORD]
        self._k32.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self._k32.GetModuleHandleW.restype = w.HMODULE

    def _target(self) -> int:
        hwnd = self._u32.FindWindowW(window_class(self.instance), None)
        if not hwnd:
            raise IpcError(
                f"Everything IPC window not found (instance '{self.instance or 'default'}')"
            )
        return hwnd

    def _command(self, cmd: int, lparam: int = 0) -> int:
        result = ctypes.c_size_t()
        ok = self._u32.SendMessageTimeoutW(
            self._target(), WM_USER, cmd, lparam, SMTO_ABORTIFHUNG, 3000, ctypes.byref(result)
        )
        if not ok:
            raise IpcError("Everything did not answer an IPC command within 3 s (busy or hung)")
        return ctypes.c_int32(result.value & 0xFFFFFFFF).value

    def _query2(
        self, search: str, flags: int, offset: int, max_results: int, request: int, sort: int
    ) -> bytes:
        u, w = self._u32, self._w
        target = self._target()
        reply: dict[str, bytes] = {}
        reply_id = 0x454D4350  # echoed back in COPYDATASTRUCT.dwData
        cds_t = self._COPYDATASTRUCT

        @self._WNDPROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_COPYDATA:
                cds = ctypes.cast(lparam, ctypes.POINTER(cds_t)).contents
                if cds.dwData == reply_id and cds.lpData:
                    reply["data"] = ctypes.string_at(cds.lpData, cds.cbData)
                    return 1
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)

        hinst = self._k32.GetModuleHandleW(None)
        with _class_lock:
            cls_name = f"EverythingMcpIpc{next(_class_ids)}_{threading.get_ident()}"
        wc = self._WNDCLASSEXW(
            cbSize=ctypes.sizeof(self._WNDCLASSEXW),
            lpfnWndProc=wndproc,
            hInstance=hinst,
            lpszClassName=cls_name,
        )
        if not u.RegisterClassExW(ctypes.byref(wc)):
            raise IpcError(f"RegisterClassExW failed ({ctypes.get_last_error()})")
        hwnd = u.CreateWindowExW(
            0, cls_name, cls_name, 0, 0, 0, 0, 0, w.HWND(-3), None, hinst, None
        )
        try:
            if not hwnd:
                raise IpcError(f"CreateWindowExW failed ({ctypes.get_last_error()})")
            header = struct.pack(
                "<7I", hwnd & 0xFFFFFFFF, reply_id, flags, offset, max_results, request, sort
            )
            payload = header + search.encode("utf-16le") + b"\0\0"
            buf = ctypes.create_string_buffer(payload, len(payload))
            cds = cds_t(COPYDATA_QUERY2W, len(payload), ctypes.cast(buf, ctypes.c_void_p))
            sent = ctypes.c_size_t()
            started = time.monotonic()
            if not u.SendMessageTimeoutW(
                target,
                WM_COPYDATA,
                hwnd,
                ctypes.addressof(cds),
                SMTO_ABORTIFHUNG,
                int(self.timeout * 1000),
                ctypes.byref(sent),
            ):
                raise IpcError(
                    "Everything did not accept the query (busy, hung, or loading its database)"
                )
            if not sent.value:
                raise IpcError(
                    "Everything rejected the query (IPC disabled or database not loaded)"
                )
            msg = w.MSG()
            while "data" not in reply:
                remaining = self.timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise IpcError(f"No reply from Everything within {self.timeout:.0f}s")
                u.MsgWaitForMultipleObjects(
                    0, None, False, int(min(remaining, 0.25) * 1000), 0x04FF
                )
                while u.PeekMessageW(ctypes.byref(msg), hwnd, 0, 0, PM_REMOVE):
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
            return reply["data"]
        finally:
            if hwnd:
                u.DestroyWindow(hwnd)
            u.UnregisterClassW(cls_name, hinst)
