"""Persistent/local/live-index correctness and indexed-query evidence."""

import asyncio
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from everything_mcp.index import LocalIndex, _creation_time
from everything_mcp.query import QueryError


@pytest.fixture
async def indexed(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    files = {
        "alpha report.txt": 1024,
        "beta.csv": 10,
        "ALPHA.md": 2048,
        "Straße.txt": 5,
        "OR": 1,
        "alphabet.txt": 8,
    }
    for name, size in files.items():
        (root / name).write_bytes(b"x" * size)
    (nested / "source.py").write_text("print('hello')", encoding="utf-8")
    old = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(root / "ALPHA.md", (old, old))
    index = LocalIndex(tmp_path / "cache" / "index.sqlite", [root])
    await index.start()
    await index.wait_ready()
    yield index, root
    await index.close()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("alpha", {"alpha report.txt", "ALPHA.md", "alphabet.txt"}),
        ('"alpha report"', {"alpha report.txt"}),
        ("alpha beta|report", {"alpha report.txt"}),
        ("alpha|beta ext:txt", {"alpha report.txt", "alphabet.txt"}),
        ("ext:md;csv", {"ALPHA.md", "beta.csv"}),
        ("folder:", {"nested"}),
        ("file:source", {"source.py"}),
        ("ext:txt !alphabet !Straße", {"alpha report.txt"}),
        ("size:>=1kb", {"alpha report.txt", "ALPHA.md"}),
        ("size:10..1024", {"alpha report.txt", "beta.csv", "source.py"}),
        ("*report*.txt", {"alpha report.txt"}),
        ("STRASSE", {"Straße.txt"}),
        ('"OR"', {"OR", "alpha report.txt"}),
        ("dm:last7days ext:md;csv", {"beta.csv"}),
        ("!<ext:txt|ext:md|folder:> !OR", {"beta.csv", "source.py"}),
    ],
)
async def test_queries(indexed, query, expected):
    index, _ = indexed
    assert {r["name"] for r in index.search(query, max_results=100)} == expected
    assert index.count(query) == len(expected)


async def test_flags_and_path_parent_filters(indexed):
    index, root = indexed
    assert {r["name"] for r in index.search("ALPHA", match_case=True)} == {"ALPHA.md"}
    assert {r["name"] for r in index.search("alpha", match_whole_word=True)} == {
        "alpha report.txt",
        "ALPHA.md",
    }
    assert index.count("nested", match_path=True) == 2
    assert index.count(f'parent:"{root / "nested"}"') == 1
    assert index.count(r"^source\.py$", regex=True) == 1


@pytest.mark.parametrize("descending", [False, True])
async def test_single_letter_uses_ordered_index_without_expanding_candidates(
    indexed, monkeypatch, descending
):
    index, _ = indexed
    expected = index.search("", max_results=100, descending=descending)
    expected = [row for row in expected if "o" in row["name"].casefold()]

    def no_expansion(*args):
        pytest.fail("Single-letter search expanded a potentially million-row candidate list")

    monkeypatch.setattr(index._names, "candidates", no_expansion)
    for query in ("o", '"o"', "O"):
        page = index.search(query, max_results=2, offset=1, descending=descending)
        assert page == expected[1:3]
        assert not page.partial


async def test_native_count_size_and_stable_global_order(indexed):
    index, _ = indexed
    assert index.sum_size("ext:txt") == 1037
    all_rows = index.search(sort="size-desc")
    first = index.search(sort="size-desc", max_results=2)
    second = index.search(sort="size-desc", offset=2, max_results=2)
    assert first + second == all_rows[:4]
    assert first[0]["name"] == "ALPHA.md"


async def test_two_character_search_uses_compact_name_candidates(indexed):
    index, root = indexed
    path = root / "invoice.txt"
    path.write_text("invoice")
    index.apply_changes({(1, str(path))})
    assert {row["name"] for row in index.search("in")} == {"invoice.txt"}
    assert "filename_candidates" in index._search_statement("in", "name", False, 50, 0)[0]
    assert not index.search("in").partial


async def test_search_deadline_returns_bounded_labeled_results(indexed, monkeypatch):
    index, _ = indexed

    def interrupted(*args, **kwargs):
        raise QueryError("Query exceeded its time limit")

    monkeypatch.setattr(index, "_execute", interrupted)
    page = index.search("alpha", max_results=2)
    assert len(page) == 2
    assert all("alpha" in row["name"].casefold() for row in page)
    assert page.partial
    assert "subset" in page.note


async def test_unavailable_root_marks_results_partial_and_preserves_cache(indexed, monkeypatch):
    import everything_mcp.index as module

    index, root = indexed
    real_isdir = module.os.path.isdir
    monkeypatch.setattr(
        module.os.path, "isdir", lambda path: False if str(path) == str(root) else real_isdir(path)
    )

    index.rescan()

    status = index.status()
    page = index.search("alpha")
    assert status["state"] == "ready"
    assert status["partial"]
    assert len(page) > 0
    assert page.partial
    assert "unavailable" in page.note


async def test_unreadable_subtree_marks_search_partial_and_retains_cached_rows(
    indexed, monkeypatch
):
    import everything_mcp.index as module

    index, root = indexed
    private = root / "nested"
    real_scandir = module.os.scandir

    def guarded_scandir(path):
        if str(path) == str(private):
            raise PermissionError("access denied")
        return real_scandir(path)

    monkeypatch.setattr(module.os, "scandir", guarded_scandir)

    index.rescan()

    status = index.status()
    page = index.search("source.py")
    assert status["partial"]
    assert len(page) == 1
    assert page.partial
    assert "unavailable" in page.note


async def test_surrogate_escaped_child_is_skipped_without_aborting_scan(tmp_path, monkeypatch):
    import everything_mcp.index as module

    root = tmp_path / "root"
    root.mkdir()
    invalid_path = str(root / "bad-\udcff-name.txt")

    class Entry:
        path = invalid_path

        def stat(self, *, follow_symlinks=False):
            return SimpleNamespace(st_size=1, st_mtime=time.time(), st_ctime=time.time())

        def is_dir(self, *, follow_symlinks=False):
            return False

    class Scan:
        def __enter__(self):
            return iter([Entry()])

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(module.os, "scandir", lambda path: Scan())
    index = LocalIndex(tmp_path / "index.db", [root])
    try:
        index.rescan()
        assert index.status()["partial"]
        assert index.status()["skipped_entries"] == 1
        assert index.count("") == 0
    finally:
        await index.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits undecodable filename bytes")
async def test_undecodable_filename_does_not_abort_scan_and_marks_coverage_partial(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    invalid = os.fsencode(root) + b"/bad-\xff-name.txt"
    fd = os.open(invalid, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    index = LocalIndex(tmp_path / "index.db", [root])
    try:
        index.rescan()
        status = index.status()
        assert status["state"] == "not_started"
        assert status["partial"]
        assert status["skipped_entries"] >= 1
        assert index.count("") == 0
    finally:
        await index.close()


async def test_first_scan_publishes_first_entry_before_completion(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    index = LocalIndex(tmp_path / "index.db", [root])
    paused, release = threading.Event(), threading.Event()

    def slow_walk(path, root, generation, failed):
        yield index._row(
            str(tmp_path / "root" / "invoice-first.txt"),
            root,
            generation,
            SimpleNamespace(st_size=1, st_mtime=time.time(), st_ctime=time.time()),
            False,
        )
        paused.set()
        release.wait(5)

    monkeypatch.setattr(index, "_walk", slow_walk)
    try:
        await index.start()
        assert await asyncio.to_thread(paused.wait, 3)
        assert index.status()["state"] == "building"
        assert index.status()["indexed_entries"] == 1
        assert index.search("in")[0]["name"] == "invoice-first.txt"
    finally:
        release.set()
        await index.close()


async def test_shared_directory_storage_stays_below_500_bytes_per_entry(tmp_path):
    root = tmp_path / "a-long-shared-directory-name" / "files"
    root.mkdir(parents=True)
    index = LocalIndex(tmp_path / "compact.db", [root])
    try:
        stat = SimpleNamespace(st_size=1024, st_mtime=1791800000, st_ctime=1791800000)
        index._upsert(
            [
                index._row(
                    str(root / f"project-{i % 10}" / f"document-{i:07d}.txt"),
                    str(root),
                    "synthetic",
                    stat,
                    False,
                )
                for i in range(10000)
            ]
        )
        index._writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert os.path.getsize(index.db_path) / 10000 < 500
        assert index.count("document") == 10000
    finally:
        await index.close()


async def test_query_plans_use_packed_candidates_and_property_index(indexed):
    index, _ = indexed
    plan = "\n".join(index.explain("report"))
    assert "json_each" in plan and "SEARCH f USING INTEGER PRIMARY KEY (rowid=?)" in plan
    extension = "\n".join(index.explain("ext:txt"))
    assert "files_extension" in extension
    assert any("INDEX" in p for p in index.explain("size:>100", sort="size"))


async def test_incremental_create_rename_and_subtree_delete_without_rescan(indexed):
    index, root = indexed
    initial_scans = index.status()["scan_count"]
    new = root / "created.txt"
    new.write_text("new", encoding="utf-8")
    index.apply_changes({(1, str(new))})
    assert index.count("created.txt") == 1
    renamed = root / "renamed.txt"
    new.rename(renamed)
    index.apply_changes({(3, str(new)), (1, str(renamed))})
    assert index.count("created.txt") == 0
    assert index.count("renamed.txt") == 1
    nested = root / "nested"
    (nested / "source.py").unlink()
    nested.rmdir()
    index.apply_changes({(3, str(nested))})
    assert index.count("nested", match_path=True) == 0
    assert index.status()["scan_count"] == initial_scans


async def test_live_watchfiles_update_without_explicit_apply(indexed):
    index, root = indexed
    path = root / "live-notification.txt"
    path.write_text("live", encoding="utf-8")
    for _ in range(60):
        if index.count("live-notification") == 1:
            break
        await asyncio.sleep(0.05)
    assert index.count("live-notification") == 1
    assert index.status()["scan_count"] == 1
    path.unlink()
    for _ in range(60):
        if not index.count("live-notification"):
            break
        await asyncio.sleep(0.05)
    assert not index.count("live-notification")


async def test_persistence_and_startup_reconciliation(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    old = root / "old.txt"
    old.write_text("old")
    db = tmp_path / "index.sqlite"
    index = LocalIndex(db, [root])
    await index.start()
    await index.wait_ready()
    await index.close()
    old.unlink()
    (root / "new.txt").write_text("new")
    reopened = LocalIndex(db, [root])
    assert reopened.count("old") == 1  # cached snapshot, before startup reconciliation
    await reopened.start()
    await reopened.wait_ready()
    assert reopened.count("old") == 0
    assert reopened.count("new") == 1
    await reopened.close()


async def test_cache_is_excluded_even_inside_root(tmp_path):
    index = LocalIndex(tmp_path / "index.sqlite", [tmp_path])
    await index.start()
    await index.wait_ready()
    assert index.count("index.sqlite") == 0
    assert index.count("scan.lock") == 0
    await index.close()


async def test_existing_directory_metadata_event_does_not_walk_root(indexed, monkeypatch):
    index, root = indexed

    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary parent-directory event must not scan root")

    monkeypatch.setattr(index, "_walk", forbidden)
    index.apply_changes({(2, str(root)), (2, str(root / "nested"))})


async def test_symlink_cycle_is_not_followed(indexed):
    index, root = indexed
    try:
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    index.apply_changes({(1, str(root / "loop"))})
    assert index.count("loop") == 1
    assert index.count("alpha") == 3


async def test_bounded_regex_and_short_query_deadline(indexed):
    index, root = indexed
    now = time.time()
    huge_name = "a" * 50000 + "!"
    index._upsert(
        [
            index._row(
                str(root / huge_name),
                str(root),
                "test",
                SimpleNamespace(st_size=1, st_mtime=now, st_ctime=now),
                False,
            )
        ]
    )
    started = time.monotonic()
    with pytest.raises(QueryError, match="time limit"):
        index.count("(a|aa)+$", regex=True, timeout=0.05)
    assert time.monotonic() - started < 1


async def test_two_instances_share_wal_without_lost_generation(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for i in range(100):
        (root / f"entry-{i}.txt").write_text(str(i))
    one = LocalIndex(tmp_path / "shared.sqlite", [root])
    two = LocalIndex(tmp_path / "shared.sqlite", [root])
    await asyncio.gather(one.start(), two.start())
    await asyncio.gather(one.wait_ready(), two.wait_ready())
    assert one.count("entry-") == two.count("entry-") == 100
    assert len(one.search("entry-", max_results=100)) == 100
    await asyncio.gather(one.close(), two.close())


async def test_shared_scan_and_incremental_update_do_not_lose_new_metadata(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    file = root / "entry.txt"
    file.write_bytes(b"old")
    one = LocalIndex(tmp_path / "shared.sqlite", [root])
    two = LocalIndex(tmp_path / "shared.sqlite", [root])
    one.rescan()
    collected, release = threading.Event(), threading.Event()
    original_walk = two._walk

    def delayed(*args):
        rows = list(original_walk(*args))
        collected.set()
        assert release.wait(3)
        yield from rows

    monkeypatch.setattr(two, "_walk", delayed)
    scanner = threading.Thread(target=two.rescan)
    scanner.start()
    assert collected.wait(3)
    file.write_bytes(b"new content after scanner read old metadata")
    updater = threading.Thread(target=one.apply_changes, args=({(2, str(file))},))
    updater.start()
    await asyncio.sleep(0.1)
    assert updater.is_alive()  # blocked until the competing scan commits its generation
    release.set()
    await asyncio.to_thread(scanner.join, 3)
    await asyncio.to_thread(updater.join, 3)
    assert not scanner.is_alive() and not updater.is_alive()
    assert one.count("entry.txt") == 1
    assert one.sum_size("entry.txt") == file.stat().st_size
    await asyncio.gather(one.close(), two.close())


async def test_second_concurrent_client_reuses_completed_reconciliation(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "entry.txt").write_text("test")
    one = LocalIndex(tmp_path / "shared.sqlite", [root])
    two = LocalIndex(tmp_path / "shared.sqlite", [root])
    scanning, release = threading.Event(), threading.Event()
    original_walk = one._walk

    def delayed(*args):
        scanning.set()
        assert release.wait(3)
        yield from original_walk(*args)

    monkeypatch.setattr(one, "_walk", delayed)
    first = threading.Thread(target=one.rescan)
    first.start()
    assert scanning.wait(3)
    second = threading.Thread(target=two.rescan)
    second.start()
    await asyncio.sleep(0.1)
    release.set()
    await asyncio.to_thread(first.join, 3)
    await asyncio.to_thread(second.join, 3)
    assert one.status()["scan_count"] == 1
    assert two.status()["scan_count"] == 0
    assert two.count("entry.txt") == 1
    await asyncio.gather(one.close(), two.close())


def test_metadata_change_time_is_not_mislabeled_as_linux_creation_time():
    metadata = SimpleNamespace(st_ctime=12345)
    assert _creation_time(metadata, windows=False) == 0
    assert _creation_time(metadata, windows=True) == 12345
    birth_metadata = SimpleNamespace(st_ctime=12345, st_birthtime=6789)
    assert _creation_time(birth_metadata, windows=False) == 6789


async def test_unknown_creation_time_is_omitted_from_search_rows(indexed, monkeypatch):
    import everything_mcp.index as module

    index, root = indexed
    monkeypatch.setattr(module, "_creation_time", lambda stat: 0)
    path = root / "unknown-birth.txt"
    path.write_text("test")
    index.apply_changes({(1, str(path))})
    assert "date_created" not in index.search("unknown-birth")[0]


async def test_atomic_directory_replacement_reconciles_same_path(indexed):
    index, root = indexed
    same = root / "same"
    same.mkdir()
    (same / "old.txt").write_text("old")
    index.apply_changes({(1, str(same))})
    (same / "old.txt").unlink()
    same.rmdir()
    replacement = root / "replacement"
    replacement.mkdir()
    (replacement / "new.txt").write_text("new")
    replacement.rename(same)
    index.apply_changes({(3, str(same)), (1, str(same))})
    assert index.count("old.txt") == 0
    assert index.count("new.txt") == 1
    assert index.status()["scan_count"] == 1


async def test_file_directory_type_changes_reconcile_children(indexed):
    index, root = indexed
    same = root / "changing"
    same.write_text("file")
    index.apply_changes({(1, str(same))})
    same.unlink()
    same.mkdir()
    (same / "child.txt").write_text("child")
    index.apply_changes({(2, str(same))})
    assert index.count("child.txt") == 1
    (same / "child.txt").unlink()
    same.rmdir()
    same.write_text("file again")
    index.apply_changes({(2, str(same))})
    assert index.count("child.txt") == 0
    assert index.search("changing")[0]["is_directory"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows separator equivalence")
async def test_windows_paths_accept_forward_slashes_and_bare_drive(indexed):
    index, root = indexed
    assert index.count(f'path:"{root.as_posix()}"') == index.count(f'path:"{root}"')
    assert index.count("nested/source.py") == 1
    assert index.count(root.drive) == index.count()


async def test_metadata_only_upsert_does_not_invalidate_name_blocks(indexed):
    index, root = indexed
    file = root / "alpha report.txt"
    generation = index._writer.execute(
        "SELECT generation FROM roots WHERE root=?", (str(root),)
    ).fetchone()[0]
    row = index._row(str(file), str(root), generation, file.stat(), False)
    with index._write_lock:
        before = index._writer.total_changes
        index._upsert([row])
        assert index._writer.total_changes - before == 1
    assert index.count("alpha report") == 1


async def test_short_query_and_extension_plans_fetch_bounded_metadata(indexed):
    index, _ = indexed
    for query, sort in [("al", "name"), ("ext:txt", "name"), ("al", "date-modified-desc")]:
        plan = index.explain(query, sort=sort)
        assert "MATERIALIZE selected" in plan
        assert any("INTEGER PRIMARY KEY (rowid=?)" in line for line in plan)
    for query in ["a", "al", "alpha"]:
        plan = index.explain(query)
        assert any("json_each VIRTUAL TABLE INDEX" in line for line in plan)
        assert any("SEARCH f USING INTEGER PRIMARY KEY (rowid=?)" in line for line in plan)
        assert not any("SEARCH f USING INDEX sqlite_autoindex_files" in line for line in plan)


async def test_packed_aggregates_use_candidate_rowids(indexed, monkeypatch):
    index, _ = indexed
    execute = index._execute
    plans = []

    def capture(sql, parameters, timeout=5.0):
        plans.append([row[3] for row in execute("EXPLAIN QUERY PLAN " + sql, parameters)])
        return execute(sql, parameters, timeout)

    monkeypatch.setattr(index, "_execute", capture)
    rows = index.search("alpha")
    plans.clear()
    assert index.count("alpha") == len(rows)
    assert index.sum_size("alpha") == sum(row["size"] for row in rows)
    for plan in plans:
        assert any("json_each VIRTUAL TABLE INDEX" in line for line in plan)
        assert any("SEARCH f USING INTEGER PRIMARY KEY (rowid=?)" in line for line in plan)


def test_old_sqlite_is_rejected_before_creating_cache(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))
    database = tmp_path / "unsupported" / "index.sqlite"
    with pytest.raises(RuntimeError, match=r"SQLite >= 3\.35"):
        LocalIndex(database, [tmp_path])
    assert not database.parent.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS filesystem spelling")
@pytest.mark.parametrize("directory", [False, True])
async def test_macos_live_case_only_rename_preserves_one_current_path(tmp_path, directory):
    root = tmp_path / "files"
    root.mkdir()
    old = root / "mixed-name"
    if directory:
        old.mkdir()
        (old / "child.txt").write_text("child")
    else:
        old.write_text("file")
    index = LocalIndex(tmp_path / "index.db", [root])
    try:
        await index.start()
        await index.wait_ready()
        new = root / "MIXED-NAME"
        old.rename(new)
        for _ in range(60):
            rows = index.search("mixed-name", match_path=True)
            if len(rows) == (2 if directory else 1) and all(
                "MIXED-NAME" in row["full_path"] for row in rows
            ):
                break
            await asyncio.sleep(0.1)
        assert len(rows) == (2 if directory else 1)
        assert all("MIXED-NAME" in row["full_path"] for row in rows)
        assert index.status()["scan_count"] == 1
    finally:
        await index.close()


async def test_symlink_root_and_cache_use_physical_watcher_paths(tmp_path):
    root = tmp_path / "physical"
    root.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    index = LocalIndex(alias / "index.db", [alias])
    try:
        await index.start()
        await index.wait_ready()
        assert index.count() == 0  # Cache and lock files remain excluded.
        (root / "new-file.txt").write_text("live")
        for _ in range(60):
            if index.count("new-file") == 1:
                break
            await asyncio.sleep(0.1)
        assert index.count("new-file") == 1
        assert index.status()["scan_count"] == 1
    finally:
        await index.close()
