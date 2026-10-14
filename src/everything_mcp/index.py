"""Independent, persistent SQLite filesystem index. No Everything runtime dependencies.

Startup reconciles each configured root once. Ordinary watcher events update only
the affected path/subtree. WAL readers use separate connections; generation scans
are serialized across processes with an OS file lock, not a process-local flag.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import regex as regex_lib

from everything_mcp.names import CompactNames, SearchPage
from everything_mcp.query import QueryError, QueryOptions, compile_query
from everything_mcp.storage import UPSERT, initialize, register_functions


def _canonical(path: str | Path) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _has_surrogates(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _event_path(path: str) -> str:
    """Ask macOS for the current on-disk spelling without opening file contents.

    On case-insensitive APFS, stat(old_name) still succeeds after a case-only
    rename. F_GETPATH distinguishes the current spelling and preserves symlinks
    with O_SYMLINK. Constants are from the macOS sys/fcntl.h SDK header; older
    Python versions do not export both open flags.
    """
    if sys.platform != "darwin":
        return path
    import fcntl

    try:
        descriptor = os.open(
            path, getattr(os, "O_EVTONLY", 0x8000) | getattr(os, "O_SYMLINK", 0x200000)
        )
        try:
            actual = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
            return _canonical(os.fsdecode(actual.split(b"\0", 1)[0]))
        finally:
            os.close(descriptor)
    except OSError:
        return path  # Deleted/inaccessible paths retain their notification spelling.


def _key(path: str) -> str:
    return path.casefold() if os.name == "nt" else path


def _creation_time(stat, *, windows: bool | None = None) -> float:
    if hasattr(stat, "st_birthtime"):
        return stat.st_birthtime
    if windows if windows is not None else os.name == "nt":
        return stat.st_ctime  # Windows compatibility before Python exposed st_birthtime.
    return 0.0  # POSIX ctime is metadata change, never claim that it is birth time.


def _inside(path: str, root: str) -> bool:
    candidate, parent = _key(path), _key(root)
    return candidate == parent or candidate.startswith(parent.rstrip(os.sep) + os.sep)


@contextlib.contextmanager
def _process_lock(path: str, stop: threading.Event):
    """Serialize scan generations across all MCP processes sharing this cache."""
    handle = open(path, "a+b")  # noqa: SIM115 - finally releases OS lock before closing handle
    acquired = False
    try:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        while not stop.is_set():
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (OSError, BlockingIOError):
                stop.wait(0.05)
        if not acquired:
            raise InterruptedError("Index closed while waiting for scan lock")
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class LocalIndex:
    def __init__(self, db_path: str | Path, roots: list[str | Path]):
        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise RuntimeError("SQLite >= 3.35 is required")
        self.db_path = _canonical(Path(db_path).resolve())
        # Watchers report physical paths (e.g. /private/var rather than /var on
        # macOS). Resolve roots/cache only; recorded child symlinks stay unfollowed.
        unique = sorted({_canonical(Path(r).resolve()) for r in roots}, key=len)
        self.roots: list[str] = []
        for root in unique:
            if not any(_inside(root, existing) for existing in self.roots):
                self.roots.append(root)
        if not self.roots:
            raise ValueError("At least one indexed root is required")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._watch_ready = threading.Event()
        self._changes: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._watcher: threading.Thread | None = None
        self._state = "not_started"
        self._last_error = ""
        self._coverage_incomplete = False
        self._coverage_note = ""
        self._watching = False
        self._scan_count = 0
        self._event_count = 0
        self._skipped = 0
        self._backend = "packed filename memory index + compact SQLite metadata"
        self._names = CompactNames(self.roots)
        self._parent_ids = {}
        self._indexed_entries = 0
        self._last_count_refresh = 0.0
        self._writer = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        self._writer.execute("PRAGMA journal_mode=WAL")
        self._writer.execute("PRAGMA synchronous=NORMAL")
        self._writer.execute("PRAGMA busy_timeout=30000")
        try:
            with _process_lock(self.db_path + ".scan.lock", self._stop):
                initialize(self._writer)
        except BaseException:
            self._writer.close()
            raise
        self._writer.commit()
        self._refresh_count()

    async def start(self) -> None:
        if self._stop.is_set():
            raise RuntimeError("Index is closed")
        if self._worker is not None:
            return
        self._state = "building"
        self._watcher = threading.Thread(
            target=self._watch_loop, name="filesystem-watch", daemon=True
        )
        self._watcher.start()
        self._worker = threading.Thread(
            target=self._work_loop, name="filesystem-index", daemon=True
        )
        self._worker.start()

    async def wait_ready(self, timeout: float = 60) -> None:
        if not await asyncio.to_thread(self._ready.wait, timeout):
            raise TimeoutError("Initial filesystem indexing is still in progress")
        if self._state == "error":
            raise RuntimeError(self._last_error)

    async def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        for worker in (self._watcher, self._worker):
            if worker is not None:
                await asyncio.to_thread(worker.join)
        with self._write_lock:
            self._writer.close()
        self._state = "closed"
        self._watching = False

    def _excluded(self, path: str) -> bool:
        # Never index cache writes and feed them back into the watcher.
        candidate, cache = _key(_canonical(path)), _key(self.db_path)
        return (
            candidate == cache
            or candidate.startswith(cache + "-")
            or candidate.startswith(cache + ".scan")
        )

    def _watch_loop(self) -> None:
        try:
            from watchfiles import watch

            existing = [root for root in self.roots if os.path.isdir(root)]
            if not existing:
                return
            self._watching = True
            for changes in watch(
                *existing,
                watch_filter=lambda change, path: not self._excluded(path),
                stop_event=self._stop,
                debounce=100,
                step=25,
                rust_timeout=100,
                yield_on_timeout=True,
            ):
                self._watch_ready.set()
                if changes:
                    self._changes.put(changes)
        except Exception as exc:
            if not self._stop.is_set():
                self._last_error = "Filesystem watching unavailable: " + str(exc)
        finally:
            self._watching = False
            self._watch_ready.set()

    def _work_loop(self) -> None:
        try:
            self._watch_ready.wait(2)
            self.rescan()
            if not self._stop.is_set():
                self._state = "ready"
            self._ready.set()
            while not self._stop.is_set():
                try:
                    changes = self._changes.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    self.apply_changes(changes)
                except Exception as exc:
                    self._last_error = "Incremental update failed: " + str(exc)
        except InterruptedError:
            self._ready.set()
        except Exception as exc:
            self._state = "error"
            self._last_error = str(exc)
            self._ready.set()

    def rescan(self) -> None:
        """Explicit reconciliation; never called per ordinary watcher event."""
        requested_at = time.time()
        with _process_lock(self.db_path + ".scan.lock", self._stop):
            performed = False
            coverage_incomplete = False
            coverage_messages = []
            for root in self.roots:
                if self._stop.is_set():
                    raise InterruptedError("Index stopped")
                if not os.path.isdir(root):
                    self._last_error = (
                        f"Indexed root is unavailable: {root} (retaining cached entries)"
                    )
                    coverage_incomplete = True
                    coverage_messages.append(
                        "A configured root is unavailable; cached entries were retained."
                    )
                    continue
                with self._write_lock:
                    prior = self._writer.execute(
                        "SELECT scanned FROM roots WHERE root=?", (root,)
                    ).fetchone()
                if prior and prior[0] is not None and prior[0] >= requested_at:
                    continue  # Another client reconciled this root while we waited.
                if not performed:
                    self._scan_count += 1
                    performed = True
                generation = uuid.uuid4().hex
                with self._write_lock, self._writer:
                    self._writer.execute(
                        "INSERT INTO roots(root,generation) VALUES(?,?) ON CONFLICT(root) DO UPDATE SET generation=excluded.generation",
                        (root, generation),
                    )
                batch = []
                published_at = time.monotonic()
                failed_subtrees: list[str] = []
                for row in self._walk(root, root, generation, failed_subtrees):
                    batch.append(row)
                    if (
                        not self._indexed_entries
                        or len(batch) >= 256
                        or time.monotonic() - published_at >= 0.1
                    ):
                        self._upsert(batch)
                        batch.clear()
                        published_at = time.monotonic()
                self._upsert(batch)
                # Inaccessible subtrees retain their previous snapshot rather than
                # masquerading as deletions. Retry reconciliation at next startup.
                with self._write_lock, self._writer:
                    for subtree in failed_subtrees:
                        prefix = _key(subtree).rstrip(os.sep) + os.sep
                        self._writer.execute(
                            "UPDATE entries SET generation=? WHERE root=? AND (path_key=? OR (path_key>=? AND path_key<?))",
                            (generation, root, _key(subtree), prefix, prefix + chr(0x10FFFF)),
                        )
                    self._writer.execute(
                        "DELETE FROM entries WHERE root=? AND generation<>?", (root, generation)
                    )
                    self._writer.execute(
                        "UPDATE roots SET scanned=? WHERE root=?", (time.time(), root)
                    )
                if failed_subtrees:
                    coverage_incomplete = True
                    coverage_messages.append(
                        "Some paths were unavailable; cached entries under them were retained."
                    )
            if performed or coverage_incomplete:
                self._coverage_incomplete = coverage_incomplete
                self._coverage_note = " ".join(dict.fromkeys(coverage_messages))
                if not coverage_incomplete and self._last_error.startswith(
                    "Indexed root is unavailable:"
                ):
                    self._last_error = ""
            self._refresh_count()

    def _walk(self, path: str, root: str, generation: str, failed: list[str]):
        stack = [path]
        while stack:
            if self._stop.is_set():
                raise InterruptedError("Index stopped")
            parent = stack.pop()
            try:
                with os.scandir(parent) as children:
                    for child in children:
                        if self._stop.is_set():
                            raise InterruptedError("Index stopped")
                        if self._excluded(child.path):
                            continue
                        try:
                            data = child.stat(follow_symlinks=False)
                            directory = child.is_dir(follow_symlinks=False)
                            yield self._row(child.path, root, generation, data, directory)
                            # Reparse points and symlinks are recorded, never followed.
                            if directory and not getattr(data, "st_file_attributes", 0) & 0x400:
                                stack.append(child.path)
                        except UnicodeEncodeError:
                            self._skipped += 1
                            # The parent remains SQLite-safe and its previous
                            # snapshot is retained instead of deleting entries.
                            failed.append(parent)
                        except (OSError, ValueError):
                            self._skipped += 1
                            failed.append(parent if _has_surrogates(child.path) else child.path)
            except OSError:
                self._skipped += 1
                failed.append(parent)

    @staticmethod
    def _row(path: str, root: str, generation: str, stat, directory: bool) -> tuple:
        path = _canonical(path)
        path.encode("utf-8")  # SQLite TEXT cannot bind surrogate-escaped POSIX names.
        name, parent = os.path.basename(path), os.path.dirname(path)
        extension = "" if directory else os.path.splitext(name)[1].lstrip(".")
        return (
            root,
            _key(path),
            path,
            path.casefold(),
            name,
            name.casefold(),
            parent,
            parent.casefold(),
            extension,
            extension.casefold(),
            int(directory),
            0 if directory else stat.st_size,
            stat.st_mtime,
            _creation_time(stat),
            generation,
        )

    def _upsert(self, rows: list[tuple]) -> None:
        if rows:
            with self._write_lock, self._writer:
                compact = []
                for row in rows:
                    key = (row[0], row[6])
                    location_id = self._parent_ids.get(key)
                    if location_id is None:
                        self._writer.execute(
                            "INSERT INTO locations(root,parent) VALUES(?,?) "
                            "ON CONFLICT(root,parent_key) DO UPDATE SET parent=excluded.parent",
                            key,
                        )
                        location_id = self._writer.execute(
                            "SELECT id FROM locations WHERE root=? AND parent_key=?",
                            (row[0], _key(row[6])),
                        ).fetchone()[0]
                        self._parent_ids[key] = location_id
                    compact.append((location_id, row[4], *row[10:]))
                self._writer.executemany(UPSERT, compact)
            # Publish the same committed batch to packed memory before moving
            # on. First queries do not have to load a million cold filenames.
            if not self._stop.is_set():
                connection = self._connect(30)
                try:
                    connection.execute("BEGIN")
                    self._names.snapshot(connection)
                finally:
                    connection.close()
            if not self._indexed_entries or time.monotonic() - self._last_count_refresh > 0.1:
                self._refresh_count()

    def _refresh_count(self):
        with self._write_lock:
            self._indexed_entries = self._writer.execute(
                "SELECT COUNT(*) FROM entries WHERE root IN ("
                + ",".join("?" for _ in self.roots)
                + ")",
                self.roots,
            ).fetchone()[0]
            self._last_count_refresh = time.monotonic()

    def apply_changes(self, changes) -> None:
        """Apply watchfiles (Change,path) batches; rename is delete+create."""
        with _process_lock(self.db_path + ".scan.lock", self._stop):
            self._apply_changes_unlocked(changes)
            self._refresh_count()

    def _apply_changes_unlocked(self, changes) -> None:
        events: dict[str, set[int]] = {}
        for kind, path in changes:
            original = _canonical(path)
            path = _event_path(original)
            if not self._excluded(path):
                # FSEvents reports both spellings as added on a case-only rename.
                if original != path:
                    for root in self.roots:
                        if _inside(original, root):
                            self._delete_subtree(root, original)
                events.setdefault(path, set()).add(int(kind))
        paths = sorted(events, key=len)
        # Coalesce directory events so a subtree is traversed only once per burst.
        handled: list[str] = []
        for path in paths:
            if any(_inside(path, parent) for parent in handled):
                continue
            roots = [root for root in self.roots if _inside(path, root)]
            if not roots:
                continue
            self._event_count += 1
            for root in roots:
                with self._write_lock:
                    row = self._writer.execute(
                        "SELECT generation FROM roots WHERE root=?", (root,)
                    ).fetchone()
                generation = row[0] if row else "live"
                try:
                    stat = os.stat(path, follow_symlinks=False)
                except FileNotFoundError:
                    self._delete_subtree(root, path)
                    handled.append(path)
                    continue
                except OSError as exc:
                    self._last_error = f"Cannot refresh {path}: {exc}"
                    continue
                directory = os.path.isdir(path) and not os.path.islink(path)
                with self._write_lock:
                    prior = self._writer.execute(
                        "SELECT is_directory FROM entries WHERE root=? AND path_key=?",
                        (root, _key(path)),
                    ).fetchone()
                indexed = prior is not None
                replaced = 3 in events[path] or (indexed and bool(prior[0]) != directory)
                # An add at an already indexed directory can represent an atomic
                # replacement/rename, even when the OS coalesces its delete event.
                if indexed and directory and 1 in events[path]:
                    replaced = True
                if replaced:
                    self._delete_subtree(root, path)
                if path != root:
                    self._upsert([self._row(path, root, generation, stat, directory)])
                # Existing directories receive metadata updates only: a parent
                # mtime notification must never trigger a root-wide rescan.
                if (
                    directory
                    and (not indexed or replaced)
                    and (path != root or 1 in events[path] or 3 in events[path])
                    and not getattr(stat, "st_file_attributes", 0) & 0x400
                ):
                    batch = []
                    for descendant in self._walk(path, root, generation, []):
                        batch.append(descendant)
                        if len(batch) >= 1000:
                            self._upsert(batch)
                            batch.clear()
                    self._upsert(batch)
                    handled.append(path)

    def _delete_subtree(self, root: str, path: str) -> None:
        prefix = _key(path).rstrip(os.sep) + os.sep
        parent, name = os.path.split(path)
        with self._write_lock, self._writer:
            self._writer.execute(
                """DELETE FROM files WHERE
                (location_id IN (SELECT id FROM locations WHERE root=? AND parent_key=?) AND name_key=?)
                OR location_id IN (SELECT id FROM locations WHERE root=? AND
                    (parent_key=? OR (parent_key>=? AND parent_key<?)))""",
                (root, _key(parent), _key(name), root, _key(path), prefix, prefix + chr(0x10FFFF)),
            )

    def _connect(self, timeout: float):
        if self._stop.is_set():
            raise RuntimeError("Index is closed")
        connection = sqlite3.connect(self.db_path, timeout=max(0.01, min(timeout, 30)))
        connection.row_factory = sqlite3.Row
        register_functions(connection)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA case_sensitive_like=ON")
        deadline = time.monotonic() + max(0.001, timeout)
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        patterns = {}

        def safe_regex(pattern: str, value: str, flags: int) -> int:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Query time limit exceeded")
            key = (pattern, flags)
            compiled = patterns.get(key)
            if compiled is None:
                compiled = patterns[key] = regex_lib.compile(pattern, flags)
            return int(compiled.search(value, timeout=min(0.02, remaining)) is not None)

        connection.create_function("safe_regex", 3, safe_regex)
        connection.create_function(
            "filename_candidates", 1, lambda term: self._names.candidates(connection, term)
        )
        return connection

    def _where(self, query: str, *, include_candidates: bool = False, **flags):
        options = QueryOptions(
            **{
                key: flags[key]
                for key in (
                    "match_case",
                    "match_path",
                    "match_whole_word",
                    "regex",
                    "use_candidates",
                )
                if key in flags
            }
        )
        compiled = compile_query(query, options)
        root_clause = "e.root IN (" + ",".join("?" for _ in self.roots) + ")"
        result = (f"{root_clause} AND ({compiled.sql})", tuple(self.roots) + compiled.parameters)
        return result + (compiled.uses_candidates,) if include_candidates else result

    def _execute(
        self,
        sql: str,
        parameters: tuple,
        timeout: float = 5.0,
        *,
        ordered_sql=None,
        ordered_parameters=None,
        candidate_term=None,
    ):
        connection = self._connect(timeout)
        try:
            connection.execute("BEGIN")
            if ordered_sql is not None:
                candidates = self._names.limited_candidates(connection, candidate_term)
                if candidates is None:
                    sql = ordered_sql
                    parameters = ordered_parameters
                else:
                    connection.create_function("filename_candidates", 1, lambda term: candidates)
            return connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc) or "user-defined function" in str(exc):
                raise QueryError(
                    "Query exceeded its time limit (including bounded regex evaluation)"
                ) from exc
            raise
        finally:
            connection.close()

    def _search_statement(
        self, query: str, sort: str, descending: bool, max_results: int, offset: int, **flags
    ):
        # Common one-letter searches must not materialize millions of row IDs.
        # Walk the filename B-tree in order and stop at the requested page instead.
        literal = query.strip()
        if len(literal) == 3 and literal[0] == literal[-1] == '"':
            literal = literal[1:-1]
        single_letter = (
            len(literal) == 1
            and literal not in "*?"
            and not flags.get("regex")
            and not flags.get("match_path")
            and sort in ("name", "name-asc", "name-desc")
        )
        if single_letter and flags.pop("ordered_short_query", False):
            flags = {**flags, "use_candidates": False}
        where, parameters, uses_candidates = self._where(query, include_candidates=True, **flags)
        if sort.endswith("-desc"):
            descending, sort = True, sort[:-5]
        elif sort.endswith("-asc"):
            sort = sort[:-4]
        columns = {
            "name": "name_fold",
            "path": "full_path_fold",
            "size": "size",
            "date-modified": "modified",
            "date-created": "created",
            "extension": "extension_fold",
        }
        if sort not in columns:
            raise QueryError(f"Unsupported sort {sort!r}")
        if (
            sort == "date-created"
            and os.name != "nt"
            and not hasattr(os.stat_result, "st_birthtime")
        ):
            raise QueryError(
                "date-created sorting is unavailable: this platform does not expose file birth time"
            )
        direction = "DESC" if descending else "ASC"
        table = "candidate_entries" if uses_candidates else "entries"
        if single_letter and not uses_candidates:
            table = "name_ordered_entries"
        sql = f"""WITH selected AS MATERIALIZED (
            SELECT e.id,e.{columns[sort]} AS sort_key,e.full_path_fold AS path_key
            FROM {table} e WHERE {where}
            ORDER BY e.{columns[sort]} {direction},e.full_path_fold {direction},e.id {direction}
            LIMIT ? OFFSET ?)
            SELECT e.* FROM selected JOIN entries e ON e.id=selected.id
            ORDER BY selected.sort_key {direction},selected.path_key {direction},selected.id {direction}"""
        return sql, parameters + (max_results, offset)

    def search(
        self,
        query: str = "",
        *,
        offset: int = 0,
        max_results: int = 100,
        sort: str = "name",
        descending: bool = False,
        timeout: float = 5.0,
        **flags,
    ) -> list[dict]:
        if offset < 0 or max_results < 0 or max_results > 100_000:
            raise QueryError("offset must be nonnegative and max_results between 0 and 100000")
        sql, parameters = self._search_statement(
            query, sort, descending, max_results, offset, **flags
        )
        if not max_results:
            return SearchPage(
                partial=self._coverage_incomplete,
                note=self._coverage_note if self._coverage_incomplete else "",
            )
        partial = self._coverage_incomplete
        budget_partial = False
        ordered_sql, ordered_parameters = self._search_statement(
            query, sort, descending, max_results, offset, ordered_short_query=True, **flags
        )
        adaptive = "FROM name_ordered_entries e" in ordered_sql
        try:
            rows = self._execute(
                sql,
                parameters,
                min(timeout * 0.65, 0.25) if adaptive else timeout * 0.65,
                **(
                    {
                        "ordered_sql": ordered_sql,
                        "ordered_parameters": ordered_parameters,
                        "candidate_term": parameters[len(self.roots)],
                    }
                    if adaptive
                    else {}
                ),
            )
        except QueryError as exc:
            if "time limit" not in str(exc):
                raise
            # Reserve part of the request budget for useful bounded results.
            # This page is explicitly marked: ordering/pagination cover only the
            # inspected subset, never pretend it is the global sorted result.
            partial = True
            budget_partial = True
            rows = self._bounded_search(
                query, sort, descending, max_results, offset, timeout * 0.35, **flags
            )
        results = [
            dict(
                name=row["name"],
                path=row["parent"],
                full_path=row["full_path"],
                size=row["size"],
                date_modified=datetime.fromtimestamp(row["modified"]).isoformat(),
                is_directory=bool(row["is_directory"]),
                extension=row["extension"],
            )
            for row in rows
        ]
        for result, row in zip(results, rows, strict=False):
            if row["created"]:
                result["date_created"] = datetime.fromtimestamp(row["created"]).isoformat()
        notes = []
        if self._coverage_incomplete and self._coverage_note:
            notes.append(self._coverage_note)
        if budget_partial:
            notes.append(
                "Search budget reached; results cover a bounded subset (at most 50000 index rows). "
                "Ordering and pagination apply only within that subset. Narrow the query for complete results."
            )
        return SearchPage(results, partial=partial, note=" ".join(notes))

    def _bounded_search(self, query, sort, descending, max_results, offset, timeout, **flags):
        where, parameters = self._where(query, **{**flags, "use_candidates": False})
        connection = self._connect(timeout)
        rows = []
        try:
            connection.execute("BEGIN")
            # No ORDER BY before LIMIT: a global sort would reproduce the stall.
            cursor = connection.execute(
                f"SELECT e.* FROM entries e WHERE e.id IN (SELECT id FROM files ORDER BY id LIMIT 50000) "
                f"AND {where} LIMIT ?",
                parameters + (min(offset + max_results, 100000),),
            )
            for row in cursor:
                rows.append(row)
        except sqlite3.OperationalError as exc:
            if "interrupted" not in str(exc) and "user-defined function" not in str(exc):
                raise
        finally:
            connection.close()
        if sort.endswith("-desc"):
            descending, sort = True, sort[:-5]
        elif sort.endswith("-asc"):
            sort = sort[:-4]
        column = {
            "name": "name_fold",
            "path": "full_path_fold",
            "size": "size",
            "date-modified": "modified",
            "date-created": "created",
            "extension": "extension_fold",
        }[sort]
        rows.sort(
            key=lambda row: (row[column], row["full_path_fold"], row["id"]), reverse=descending
        )
        return rows[offset : offset + max_results]

    def count(self, query: str = "", *, timeout: float = 5.0, **flags) -> int:
        where, parameters, uses_candidates = self._where(query, include_candidates=True, **flags)
        table = "candidate_entries" if uses_candidates else "entries"
        return self._execute(f"SELECT COUNT(*) FROM {table} e WHERE {where}", parameters, timeout)[
            0
        ][0]

    def sum_size(self, query: str = "", *, timeout: float = 5.0, **flags) -> int:
        where, parameters, uses_candidates = self._where(query, include_candidates=True, **flags)
        table = "candidate_entries" if uses_candidates else "entries"
        return self._execute(
            f"SELECT COALESCE(SUM(e.size),0) FROM {table} e WHERE e.is_directory=0 AND {where}",
            parameters,
            timeout,
        )[0][0]

    def explain(
        self,
        query: str = "",
        *,
        sort: str = "name",
        descending: bool = False,
        max_results: int = 50,
        offset: int = 0,
        **flags,
    ) -> list[str]:
        sql, parameters = self._search_statement(
            query, sort, descending, max_results, offset, **flags
        )
        plan = [row[3] for row in self._execute("EXPLAIN QUERY PLAN " + sql, parameters)]
        ordered_sql, ordered_parameters = self._search_statement(
            query, sort, descending, max_results, offset, ordered_short_query=True, **flags
        )
        if "FROM name_ordered_entries e" in ordered_sql:
            plan += ["ADAPTIVE: over 4096 matches uses filename order (250ms primary budget)"]
            plan += [
                row[3]
                for row in self._execute("EXPLAIN QUERY PLAN " + ordered_sql, ordered_parameters)
            ]
        return plan

    def status(self) -> dict:
        return dict(
            state=self._state,
            indexed_entries=self._indexed_entries,
            roots=self.roots,
            db_path=self.db_path,
            backend=self._backend,
            last_error=self._last_error,
            watching=self._watching,
            scan_count=self._scan_count,
            event_count=self._event_count,
            skipped_entries=self._skipped,
            filename_memory_bytes=self._names.bytes_used,
            candidate_cache_bytes=self._names.candidate_cache_bytes,
            partial=self._state != "ready" or self._coverage_incomplete,
            coverage_note=self._coverage_note,
        )
