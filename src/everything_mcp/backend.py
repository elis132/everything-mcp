"""
Backend for communicating with voidtools Everything via es.exe.

Handles query execution, result parsing, and metadata enrichment.

Design decision: es.exe is invoked *without* ``-size -dm -dc`` flags.
This produces clean one-path-per-line output that is trivially parseable
regardless of es.exe version, locale, or output encoding. Metadata is
then enriched via ``os.stat()`` - fast, reliable, and cross-version.
"""

from __future__ import annotations

import asyncio
import contextlib
import locale
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from everything_mcp.config import (
    BUSY_OTHER_SESSION,
    EverythingConfig,
    _release_machine_lock,
    _try_machine_lock,
)

__all__ = [
    "EverythingBackend",
    "SearchResult",
    "build_type_query",
    "build_recent_query",
    "human_size",
    "FILE_TYPES",
    "SORT_MAP",
    "TIME_PERIODS",
]

logger = logging.getLogger("everything_mcp")

# ── Constants ─────────────────────────────────────────────────────────────

# Friendly sort names → es.exe -sort values
SORT_MAP: dict[str, str] = {
    "name": "name",
    "name-desc": "name-descending",
    "path": "path",
    "path-desc": "path-descending",
    "size": "size",
    "size-asc": "size",
    "size-desc": "size-descending",
    "date-modified": "date-modified",
    "date-modified-asc": "date-modified",
    "date-modified-desc": "date-modified-descending",
    "date-created": "date-created",
    "date-created-asc": "date-created",
    "date-created-desc": "date-created-descending",
    "extension": "extension",
}

# File type categories → Everything ext: queries
FILE_TYPES: dict[str, str] = {
    "audio": "ext:mp3;wav;flac;aac;ogg;wma;m4a;opus;aiff;alac",
    "video": "ext:mp4;avi;mkv;mov;wmv;flv;webm;m4v;mpeg;mpg;3gp;ts",
    "image": "ext:jpg;jpeg;png;gif;bmp;svg;webp;tiff;tif;ico;raw;heic;heif;avif;psd",
    "document": "ext:pdf;doc;docx;xls;xlsx;ppt;pptx;odt;ods;odp;rtf;txt;md;epub;pages;numbers;key",
    "code": (
        "ext:py;js;ts;jsx;tsx;c;cpp;h;hpp;cs;java;go;rs;rb;php;swift;kt;scala;r;"
        "lua;sh;bash;ps1;bat;cmd;sql;html;css;scss;sass;less;vue;svelte;dart;zig;"
        "nim;hx;ex;exs;erl;hs;ml;fs;clj;lisp;asm;toml;yaml;yml;json;xml;ini;cfg;"
        "conf;env;dockerfile;makefile;cmake;gradle;sbt;proto;graphql;tf;hcl"
    ),
    "archive": "ext:zip;rar;7z;tar;gz;bz2;xz;tgz;zst;lz4;cab;iso;dmg",
    "executable": "ext:exe;msi;dll;sys;com;scr;appx;msix",
    "font": "ext:ttf;otf;woff;woff2;eot;fon",
    "3d": "ext:obj;fbx;stl;blend;dae;3ds;gltf;glb;usd;usda;usdz;step;iges",
    "data": "ext:csv;tsv;json;jsonl;ndjson;xml;sqlite;db;mdb;accdb;parquet;arrow;avro;hdf5;feather",
}

# Time period shortcuts → Everything dm: values.  Everything 1.4 only parses
# plural units after a number: "last1hours" is a rolling hour, "last1hour"
# silently matches nothing.
TIME_PERIODS: dict[str, str] = {
    "1min": "last1mins",
    "5min": "last5mins",
    "10min": "last10mins",
    "15min": "last15mins",
    "30min": "last30mins",
    "1hour": "last1hours",
    "2hours": "last2hours",
    "6hours": "last6hours",
    "12hours": "last12hours",
    "today": "today",
    "yesterday": "yesterday",
    "1day": "last1days",
    "3days": "last3days",
    "1week": "last7days",
    "2weeks": "last2weeks",
    "1month": "last1months",
    "3months": "last3months",
    "6months": "last6months",
    "1year": "last1years",
}


def _functions(*names: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w)(?:{'|'.join(names)}):", re.IGNORECASE)


# Query functions that make Everything 1.4 read every candidate file from
# disk.  1.4 evaluates terms left to right and answers no other IPC call
# until a query finishes, so one such term over a large candidate set freezes
# Everything for minutes (#18).  Each rule: pattern, label, max candidates,
# max candidate bytes (None = not size-bound), and the Everything.ini switch
# that makes it index-served (None = never).  Measured cold cost per
# candidate: content ~10 ms plus reading the file, image/tag headers ~8 ms,
# unindexed dates/attributes ~0.1-0.2 ms.
# ponytail: fixed budgets sized for SSDs; make them configurable if HDD users hit them.
_DISK_RULES: list[tuple[re.Pattern[str], str, int, int | None, str | None]] = [
    (
        _functions("content", "ansicontent", "utf8content", "utf16content", "utf16becontent"),
        "content:",
        1_000,
        100 * 1024**2,
        None,
    ),
    (
        _functions("width", "height", "dimension", "dimensions", "bitdepth", "orientation"),
        "image properties (width:, height:, ...)",
        1_000,
        None,
        None,
    ),
    (
        _functions("album", "artist", "comment", "genre", "title", "track", "year"),
        "music tags (artist:, album:, ...)",
        1_000,
        None,
        None,
    ),
    (_functions("dc", "datecreated"), "dc:", 30_000, None, "index_date_created"),
    (_functions("da", "dateaccessed"), "da:", 30_000, None, "index_date_accessed"),
    (_functions("attrib", "attributes"), "attrib:", 30_000, None, "index_attributes"),
    (_functions("dm", "datemodified"), "dm:", 30_000, None, "index_date_modified"),
    (_functions("size"), "size:", 30_000, None, "index_size"),
]

# Characters that end an unquoted es.exe argument (es.c unicode_is_ascii_ws).
_ES_WHITESPACE = " \t\r\n"

# Standalone tokens that combine terms instead of narrowing them: with
# Everything's "allow literal operators" option on, AND/OR/NOT are operators.
_GROUPING_WORDS = {"AND", "OR", "NOT"}

# A leading chain of Everything search modifiers, e.g. "regex:" or "case:regex:".
_MODIFIER_CHAIN = re.compile(r"^[!<]*(?:[a-z0-9-]+:)+", re.IGNORECASE)

# Sorts on a property Everything 1.4 has not indexed come back in arbitrary
# order (verified), so they are refused.
_SORT_INDEX: dict[str, str] = {
    "date-created": "index_date_created",
    "date-modified": "index_date_modified",
    "size": "index_size",
}

_MACHINE_LOCK_WAIT = 15.0  # seconds to wait for another session's query
_LOCK_YIELD = 0.06  # pause after our own release (> the 50 ms poll) so waiters get a turn
_WATCH_INTERVAL = 2.0  # seconds between checks for an Everything restart
_last_release = 0.0  # monotonic time this process last released the machine lock

BUSY_THIS_SESSION = (
    "Everything is still running an earlier query that this session stopped "
    "waiting for (timed out or cancelled), and it answers no other search until "
    "that finishes. Wait a minute and retry; if this persists, ask the user to "
    "restart Everything."
)


# ── Result dataclass ──────────────────────────────────────────────────────


@dataclass(slots=True)
class SearchResult:
    """A single file/folder search result with optional metadata."""

    path: str
    name: str
    is_dir: bool = False
    size: int = -1
    date_modified: str = ""
    date_created: str = ""
    extension: str = ""

    def to_dict(self) -> dict:
        """Serialize to a dictionary, omitting empty/unknown fields."""
        d: dict = {
            "path": self.path,
            "name": self.name,
            "type": "folder" if self.is_dir else "file",
        }
        if not self.is_dir and self.size >= 0:
            d["size"] = self.size
            d["size_human"] = human_size(self.size)
        if self.extension:
            d["extension"] = self.extension
        if self.date_modified:
            d["date_modified"] = self.date_modified
        if self.date_created:
            d["date_created"] = self.date_created
        return d


# ── Backend ───────────────────────────────────────────────────────────────


class EverythingBackend:
    """Async backend for executing searches via es.exe subprocess calls."""

    def __init__(self, config: EverythingConfig) -> None:
        self.config = config
        self._lock = asyncio.Lock()
        # communicate() of an es.exe we stopped waiting for; Everything is
        # busy until it finishes (see _run).
        self._pending: asyncio.Future | None = None
        self._watcher: asyncio.Future | None = None

    # ── Primary search ────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        max_results: int = 100,
        sort: str = "name",
        match_case: bool = False,
        match_whole_word: bool = False,
        match_regex: bool = False,
        match_path: bool = False,
        offset: int = 0,
    ) -> list[SearchResult]:
        """Execute a search query and return enriched results.

        Returns a list of :class:`SearchResult` objects with metadata
        populated via ``os.stat()``.
        """
        sort_key = next((k for k in _SORT_INDEX if sort.startswith(k)), None)
        if (
            sort_key
            and not self.config.indexed.get(_SORT_INDEX[sort_key], False)
            and _everything_version(self.config.version_info) < (1, 5)
        ):
            prop = sort_key.replace("-", " ")
            raise RuntimeError(
                f"Sorting by {prop} needs 'Index {prop}' enabled in Everything "
                "(Tools > Options > Indexes); without it Everything returns the "
                "results in arbitrary order. Sort by name or path instead."
            )

        cmd = self._base_cmd()

        # Result count & offset
        cmd.extend(["-n", str(min(max_results, self.config.max_results_cap))])
        if offset > 0:
            cmd.extend(["-o", str(offset)])

        # Sort
        sort_value = SORT_MAP.get(sort, sort)
        cmd.extend(["-sort", sort_value])

        # Match modifiers.  -r makes only the NEXT argument a regex.
        flags = []
        if match_case:
            flags.append("-case")
        if match_whole_word:
            flags.append("-w")
        if match_path:
            flags.append("-p")
        if match_regex:
            flags.append("-r")
        cmd.extend(flags)

        # NOTE: We intentionally omit -size / -dm / -dc.  Keeping es.exe
        # output as plain one-path-per-line makes parsing trivial and
        # version-independent.  Metadata comes from os.stat() below.
        #
        # Split query into separate args so es.exe treats spaces as AND
        # operators.  A single quoted arg like "dm:today ext:md" would be
        # searched as a literal string and return 0 results.
        # Must preserve quoted sections (e.g. "exact name.txt",
        # path:"C:\My Documents") as single tokens.
        cmd.extend(await self._query_terms(query, flags))

        stdout, stderr, rc = await self._run(cmd)

        if rc != 0:
            msg = stderr.strip() or stdout.strip() or f"es.exe exited with code {rc}"
            raise RuntimeError(f"Everything search failed: {msg}")

        # Parse/stat can be expensive for large result sets; keep event loop responsive.
        return await asyncio.to_thread(_parse_paths_and_stat, stdout)

    # ── Aggregate queries ─────────────────────────────────────────────

    async def count(self, query: str) -> int:
        """Return the number of results for *query* without listing them."""
        # Same argv handling as search(): multi-term queries need separate
        # args for AND logic (see _split_query_terms).
        return await self._aggregate("-get-result-count", await self._query_terms(query))

    async def get_total_size(self, query: str) -> int:
        """Return the total size in bytes of all files matching *query*."""
        return await self._aggregate("-get-total-size", await self._query_terms(query))

    async def _aggregate(self, option: str, terms: list[str], flags: list[str] = ()) -> int:
        """Run ``-get-result-count`` or ``-get-total-size`` over *terms*."""
        # Important: do not combine with "-n 0" because es.exe then reports 0.
        cmd = [*self._base_cmd(), option, *flags, *terms]
        stdout, stderr, rc = await self._run(cmd)

        if rc != 0:
            what = "Count" if option == "-get-result-count" else "Total size"
            raise RuntimeError(f"{what} failed: {stderr.strip() or stdout.strip()}")

        return _parse_es_number(stdout)

    def default_sort(self) -> str:
        """Newest first when date modified is indexed (Everything's default), else by name."""
        if self.config.indexed.get("index_date_modified", True) or (
            _everything_version(self.config.version_info) >= (1, 5)
        ):
            return "date-modified-desc"
        return "name"

    # ── Health check ──────────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Check if Everything is accessible and return status info."""
        if not self.config.is_valid:
            if any(e == BUSY_OTHER_SESSION or "probably busy" in e for e in self.config.errors):
                return {"status": "busy", "message": " ".join(self.config.errors)}
            return {
                "status": "error",
                "errors": self.config.errors,
                "es_path": self.config.es_path or "not found",
            }
        if self._pending is not None and not self._pending.done():
            # A version probe would queue behind the slow query and freeze Everything.
            return {"status": "busy", "message": BUSY_THIS_SESSION}

        try:
            cmd = self._base_cmd()
            cmd.append("-get-everything-version")
            stdout, _, rc = await self._run(cmd)
            if rc == 0 and stdout.strip():
                return {
                    "status": "ok",
                    "everything_version": stdout.strip(),
                    "es_path": self.config.es_path,
                    "instance": self.config.instance or "default",
                }
            return {
                "status": "error",
                "message": "Unexpected response from Everything",
                "es_path": self.config.es_path,
            }
        except Exception as exc:
            busy = (BUSY_OTHER_SESSION, BUSY_THIS_SESSION)
            status = "busy" if str(exc) in busy or "timed out" in str(exc) else "error"
            return {"status": status, "message": str(exc)}

    # ── Internals ─────────────────────────────────────────────────────

    def _base_cmd(self) -> list[str]:
        """Build the base es.exe command with optional instance flag."""
        cmd = [self.config.es_path]
        if self.config.instance:
            cmd.extend(["-instance", self.config.instance])
        return cmd

    async def _query_terms(self, query: str, flags: list[str] = ()) -> list[str]:
        """Split *query* into es.exe args, keeping disk-reading terms cheap.

        On Everything 1.4, terms that read from disk (see ``_DISK_RULES``)
        go last, so the cheap terms narrow the candidates first, and only
        when those candidates fit the rule's budget.  The budget is checked
        with the same match *flags* as the real query.  1.5 reorders terms
        by cost itself.
        """
        # With -r, es.exe takes the next argument as the regex (it sends
        # "regex:" + that argument); the rest are ordinary search terms.
        terms = _split_query_terms(query)
        head = terms[:1] if "-r" in flags else []
        rest = [_not_a_switch(term) for term in terms[len(head) :]]
        terms = head + rest
        if _everything_version(self.config.version_info) >= (1, 5):
            return terms

        slow = [
            (i, rule)
            for i, term in enumerate(terms)
            if (rule := self._disk_rule("regex:" + term if i < len(head) else term))
        ]
        if not slow:
            return terms

        rules = [rule for _, rule in slow]
        label, budget, _, index_key = min(rules, key=lambda r: r[1])
        how = (
            " or enable indexing of that property in Everything (Tools > Options > Indexes)"
            if index_key
            else ""
        )
        if any(_is_grouping(t) for t in terms):
            raise RuntimeError(
                f"{label} cannot be combined with |, < >, ( ) or AND/OR/NOT on Everything "
                "1.4, because it reads every file from disk; use space-separated (AND) terms."
            )
        slow_idx = {i for i, _ in slow}
        fast = [t for i, t in enumerate(terms) if i not in slow_idx]
        if not fast:
            raise RuntimeError(
                f"{label} needs a narrowing filter (path:, ext: or part of a name) on "
                "Everything 1.4: on its own it reads every file on disk and Everything "
                f"stops answering all other searches meanwhile. Add a filter{how}."
            )
        if head and 0 in slow_idx:
            # Moving the head would make -r apply to a different term.
            raise RuntimeError(
                f"With match_regex, the regex must not use {label} on Everything 1.4. "
                "Use match_regex=false and put regex:<pattern> after narrowing terms."
            )

        candidates = await self._aggregate("-get-result-count", fast, flags)
        if candidates < 0:
            raise RuntimeError(
                f"{label} was refused: Everything could not count the files it would "
                "read from disk, so the query cannot be checked for size. Narrow it with "
                f"path:, ext: or a name{how}."
            )
        if candidates > budget:
            raise RuntimeError(
                f"{label} makes Everything 1.4 read each candidate file from disk, and "
                "it answers no other search meanwhile. The rest of this query matches "
                f"{candidates:,} items; the limit for {label} is {budget:,}. Narrow it "
                f"with path:, ext: or a name{how}."
            )
        byte_rules = [r for r in rules if r[2] is not None]
        if byte_rules:
            byte_label, _, byte_budget, _ = min(byte_rules, key=lambda r: r[2])
            size = await self._aggregate("-get-total-size", fast, flags)
            if not 0 <= size <= byte_budget:
                size_hint = ", size:<" if self.config.indexed.get("index_size", True) else ""
                raise RuntimeError(
                    f"{byte_label} makes Everything 1.4 read the files from disk, and it "
                    "answers no other search meanwhile. The rest of this query matches "
                    f"{human_size(size)} of files; the limit for {byte_label} is "
                    f"{human_size(byte_budget)}. Narrow it with path:, ext:{size_hint} or a name."
                )
        return fast + [terms[i] for i in sorted(slow_idx)]

    def _disk_rule(self, term: str) -> tuple[str, int, int | None, str | None] | None:
        """(label, budget, byte budget, index key) of a term that reads from disk."""
        term = term.replace('"', "")  # es.exe passes quotes on; judge the bare text
        # After a regex: modifier the rest of the term is the regex, unless a
        # function follows the modifier chain (regex:content:x is a content
        # search) or | < > make Everything split the term.
        chain = _MODIFIER_CHAIN.match(term)
        if chain and "regex" in chain.group(0).lower() and not any(c in term for c in "|<>"):
            term = chain.group(0)
        for pattern, label, budget, byte_budget, index_key in _DISK_RULES:
            if pattern.search(term) and not (index_key and self.config.indexed.get(index_key)):
                return label, budget, byte_budget, index_key
        return None

    async def _run(self, cmd: list[str]) -> tuple[str, str, int]:
        """Run es.exe asynchronously.  Returns ``(stdout, stderr, returncode)``.

        Everything answers IPC queries one at a time, and a query that
        arrives while another runs blocks Everything entirely, window
        included, until the first finishes (#18).  Killing es.exe does not
        cancel its query inside Everything.  So:

        - one es.exe at a time per process (``self._lock``) and per machine
          (``_MACHINE_LOCK``, shared by every everything-mcp session);
        - on timeout or cancellation es.exe keeps running, drained in the
          background: it exits exactly when Everything is done, which makes
          it the busy signal.  Until then calls fail fast instead of queueing.

        ponytail: the lock lives in this process, so if the server exits while
        its query still runs inside Everything, the next session is not warned;
        a lock held by es.exe itself would need a wrapper process per call.
        """
        kwargs: dict = dict(
            stdin=subprocess.DEVNULL,  # never hand es.exe the MCP stdio pipe
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        args: str | list[str] = cmd
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            args = _command_line(cmd)

        # One deadline for the whole call, so calls queued on self._lock do
        # not each wait the full _MACHINE_LOCK_WAIT for another session.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _MACHINE_LOCK_WAIT
        async with self._lock:
            if self._pending is not None:
                if not self._pending.done():
                    raise RuntimeError(BUSY_THIS_SESSION)
                self._pending = None

            lock_fd = await _acquire_machine_lock(deadline)
            window = _ipc_window(self.config.instance)
            try:
                process = subprocess.Popen(args, **kwargs)
            except BaseException as exc:
                _release_machine_lock(lock_fd)
                if isinstance(exc, FileNotFoundError):
                    raise RuntimeError(
                        f"es.exe not found at: {self.config.es_path}. "
                        "Verify Everything is installed."
                    ) from exc
                raise

            task = _communicate_in_thread(process)
            task.add_done_callback(lambda t: _es_finished(t, lock_fd))
            try:
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    asyncio.shield(task), timeout=self.config.timeout
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                self._pending = task
                self._watcher = asyncio.ensure_future(self._watch(task, process, window))
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise RuntimeError(
                    f"Search timed out after {self.config.timeout}s. Everything is still "
                    "running this query and answers no other search until it finishes; "
                    "calls report busy until then. Retry later with a narrower query "
                    "(add path: or ext:); if Everything stays busy, ask the user to "
                    "restart it."
                ) from exc

        return (
            _decode_output(stdout_raw),
            _decode_output(stderr_raw),
            process.returncode or 0,
        )

    async def _watch(self, task: asyncio.Future, process, window: int | None) -> None:
        """Drop a pending es.exe if Everything restarts.

        es.exe waits for its reply with no timeout, and a restarted
        Everything never sends it, so the busy signal would otherwise stick.
        """
        while not task.done():
            await asyncio.wait({task}, timeout=_WATCH_INTERVAL)
            if not task.done() and _ipc_window(self.config.instance) != window:
                logger.warning("Everything restarted; dropping the es.exe still waiting on it")
                with contextlib.suppress(OSError):
                    process.kill()
                return


def _communicate_in_thread(process: subprocess.Popen) -> asyncio.Future:
    """``process.communicate()`` in a daemon thread, as a future of this loop.

    Not ``asyncio.to_thread``: a pending es.exe can wait on Everything for
    minutes, and the default executor is joined at exit, so the server could
    not shut down.  A daemon thread is abandoned at exit instead.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def deliver(outcome: tuple | BaseException) -> None:
        if future.done():
            return
        if isinstance(outcome, BaseException):
            future.set_exception(outcome)
        else:
            future.set_result(outcome)

    def work() -> None:
        try:
            outcome = process.communicate()
        except BaseException as exc:  # handed to the awaiting caller
            outcome = exc
        with contextlib.suppress(RuntimeError):  # loop already closed at exit
            loop.call_soon_threadsafe(deliver, outcome)

    threading.Thread(target=work, name="es.exe", daemon=True).start()
    return future


def _is_grouping(term: str) -> bool:
    """True for a token that ORs or groups terms instead of narrowing them."""
    term = term.lstrip("!")
    return (
        not term  # a lone "!" negates whatever term ends up after it
        or "|" in term
        or term.startswith(("<", "("))
        or term.endswith((">", ")"))
        or term in _GROUPING_WORDS
    )


def _not_a_switch(term: str) -> str:
    """Quote a search term that es.exe would otherwise run as a switch.

    es.exe treats any argument starting with - or / as an option, e.g.
    ``-exit`` (quit Everything), ``-reindex`` or ``-export-csv <file>``
    (write a file).  Quoted, it is plain search text.

    ponytail: relies on es.exe's default argument mode.  es 1.1.0.37+ can be
    switched to CommandLineToArgvW parsing with ``es -argv -save-settings``;
    pinning ``-default-argv`` would break every older es.exe (unknown switch).
    """
    return f'"{term.replace(chr(34), "")}"' if term[:1] in "-/" else term


def _command_line(cmd: list[str]) -> str:
    """Windows command line for es.exe that passes query quotes through.

    es.exe parses its own command line and keeps quotes as part of the
    search, which is Everything syntax (``path:"C:\\My Docs"``).
    ``subprocess.list2cmdline`` would wrap such arguments in quotes and
    backslash-escape the inner ones, which es.exe does not undo.
    """

    def arg(a: str) -> str:
        return f'"{a}"' if (" " in a or "\t" in a) and '"' not in a else a

    return " ".join([f'"{cmd[0]}"', *(arg(a) for a in cmd[1:])])


# ── Cross-session coordination ───────────────────────────────────────────


async def _acquire_machine_lock(deadline: float) -> int:
    """Take the machine-wide es.exe lock, waiting for other sessions until *deadline*."""
    # Right after our own release, let a session polling for the lock take its
    # turn; otherwise back-to-back calls here starve it into a false "busy".
    # ponytail: yield-based fairness; a FIFO ticket file if many sessions contend.
    loop = asyncio.get_running_loop()
    pause = _LOCK_YIELD - (time.monotonic() - _last_release)
    if pause > 0:
        await asyncio.sleep(pause)
        # Time spent queued behind our own query is not time lost to other
        # sessions: after yielding to them, still get a short fair try.
        deadline = max(deadline, loop.time() + 1.0)
    while (fd := _try_machine_lock()) is None:
        if loop.time() >= deadline:
            raise RuntimeError(BUSY_OTHER_SESSION)
        await asyncio.sleep(0.05)
    return fd


def _es_finished(task: asyncio.Future, lock_fd: int) -> None:
    global _last_release
    if not task.cancelled():
        task.exception()  # retrieved: a caller that still waits sees it itself
    _release_machine_lock(lock_fd)
    _last_release = time.monotonic()


def _ipc_window(instance: str) -> int | None:
    """Handle of Everything's IPC window (0 if absent, None off Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes

    name = "EVERYTHING_TASKBAR_NOTIFICATION" + (f"_({instance})" if instance else "")
    return ctypes.windll.user32.FindWindowW(name, None)


# ── Parsing & enrichment ──────────────────────────────────────────────────


def _parse_paths_and_stat(stdout: str) -> list[SearchResult]:
    """Parse es.exe plain output (one path per line) and enrich via os.stat().

    Robustly handles:
    - Blank lines (skipped)
    - Paths with spaces or unicode characters
    - Inaccessible paths (returns result with size=-1)
    """
    results: list[SearchResult] = []

    for raw_line in stdout.splitlines():
        # Preserve significant whitespace in file names; only trim line endings.
        filepath = raw_line.rstrip("\r\n")
        if not filepath.strip():
            continue

        # Validate that this looks like a real path (drive letter or UNC)
        if not _looks_like_path(filepath):
            logger.debug("Skipping non-path line: %r", filepath[:120])
            continue

        result = _stat_to_result(filepath)
        if result is not None:
            results.append(result)

    return results


def _looks_like_path(s: str) -> bool:
    """Quick heuristic: does *s* look like a Windows or UNC path?"""
    # Drive letter: C:\...
    if len(s) >= 3 and s[0].isalpha() and s[1] == ":" and s[2] in ("/", "\\"):
        return True
    # UNC: \\server\share
    if s.startswith("\\\\"):
        return True
    # Unix-style (for testing or WSL)
    return s.startswith("/")


def _stat_to_result(filepath: str) -> SearchResult | None:
    """Create a :class:`SearchResult` from a filepath, enriching with os.stat()."""
    try:
        p = Path(filepath)
        name = p.name or filepath  # root drives have empty name
        is_dir = p.is_dir()
        ext = p.suffix.lstrip(".").lower() if not is_dir else ""

        size = -1
        dm = ""
        dc = ""
        try:
            stat = p.stat()
            size = stat.st_size if not is_dir else -1
            dm = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            dc = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass  # Inaccessible - still return the path

        return SearchResult(
            path=str(p),
            name=name,
            is_dir=is_dir,
            size=size,
            date_modified=dm,
            date_created=dc,
            extension=ext,
        )
    except Exception as exc:
        logger.debug("Failed to stat '%s': %s", filepath, exc)
        # Return a bare result so we at least report the path
        return SearchResult(path=filepath, name=Path(filepath).name or filepath)


# es.exe prints unsigned -1 when -get-result-count / -get-total-size cannot
# produce a value (e.g. unsupported by the connected Everything version).
_ES_UINT64_ERROR = 2**64 - 1


def _parse_es_number(stdout: str) -> int:
    """Parse a numeric es.exe aggregate result; -1 when unavailable."""
    try:
        value = int(stdout.strip())
    except ValueError:
        return -1
    return -1 if value == _ES_UINT64_ERROR else value


# ── Query splitting ────────────────────────────────────────────────────────


def _everything_version(version_info: str) -> tuple[int, int]:
    """(major, minor) from e.g. ``Everything v1.4.1.1032``; (0, 0) if unknown."""
    m = re.search(r"v(\d+)\.(\d+)", version_info)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _split_query_terms(query: str) -> list[str]:
    """Split an Everything query into separate terms for es.exe argv.

    es.exe requires separate arguments for AND logic.  ``es.exe dm:today
    ext:md`` works but ``es.exe "dm:today ext:md"`` searches the literal
    string.

    Quoted sections (Everything syntax for grouping) are kept together as
    single tokens, quotes included: es.exe passes them on to Everything,
    where ``path:"C:\\My Docs"`` needs them (see ``_command_line``).

    The rules mirror es.exe's own parser (``_es_get_argv``, default mode),
    so every token checked here is exactly one argument es.exe sees: tokens
    end at unquoted space, tab, CR or LF; ``\"\"\"`` is a literal quote that
    does not toggle quoting; an unclosed quote is closed at the token's end.
    """
    tokens: list[str] = []
    i = 0
    n = len(query)
    while i < n:
        if query[i] in _ES_WHITESPACE:
            i += 1
            continue
        start = i
        in_quote = False
        while i < n and (in_quote or query[i] not in _ES_WHITESPACE):
            if query.startswith('"""', i):
                i += 3
                continue
            if query[i] == '"':
                in_quote = not in_quote
            i += 1
        tokens.append(query[start:i] + ('"' if in_quote else ""))
    return tokens


# ── Query builders ────────────────────────────────────────────────────────


def build_type_query(file_type: str, additional_query: str = "", path_filter: str = "") -> str:
    """Build a search query for a specific file type category.

    Raises :class:`ValueError` if *file_type* is not a known category.
    """
    key = file_type.lower().strip()
    if key not in FILE_TYPES:
        available = ", ".join(sorted(FILE_TYPES.keys()))
        raise ValueError(f"Unknown file type '{file_type}'. Available: {available}")

    parts = [FILE_TYPES[key]]
    if path_filter:
        parts.append(f'path:"{path_filter}"')
    if additional_query:
        parts.append(additional_query)
    return " ".join(parts)


def build_recent_query(
    period: str = "1hour",
    path_filter: str = "",
    extensions: str = "",
) -> str:
    """Build a search query for recently modified files."""
    time_value = TIME_PERIODS.get(period, period)
    parts = [f"dm:{time_value}"]

    if path_filter:
        parts.append(f'path:"{path_filter}"')
    if extensions:
        # Normalize "py,js" or ".py,.js" or "py;js" → "py;js"
        exts = extensions.replace(".", "").replace(",", ";").replace(" ", ";")
        exts = ";".join(e for e in exts.split(";") if e)  # remove empties
        if exts:
            parts.append(f"ext:{exts}")

    return " ".join(parts)


# ── Utility functions ─────────────────────────────────────────────────────


def human_size(size: int) -> str:
    """Convert bytes to a human-readable size string (e.g. ``1.5 MB``)."""
    if size < 0:
        return "unknown"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def _decode_output(data: bytes) -> str:
    """Decode subprocess output, trying UTF-8 first then system encoding."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    encoding = locale.getpreferredencoding(False)
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return data.decode("utf-8", errors="replace")
