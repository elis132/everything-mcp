"""Migration and shared-process packed filename snapshot correctness."""

import sqlite3
from types import SimpleNamespace

from everything_mcp.index import LocalIndex
from everything_mcp.names import NameBlock


async def test_candidate_cache_reuses_names_across_filters_and_invalidates_on_write(
    tmp_path, monkeypatch
):
    root = tmp_path / "files"
    root.mkdir()
    index = LocalIndex(tmp_path / "index.db", [root])
    try:
        stat = SimpleNamespace(st_size=1, st_mtime=1791800000, st_ctime=1791800000)
        index._upsert([index._row(str(root / "invoice.txt"), str(root), "x", stat, False)])
        assert len(index.search("in")) == 1
        original = NameBlock.matches

        def no_rescan(*args):
            raise AssertionError("An unchanged snapshot should reuse the bounded candidate cache")

        monkeypatch.setattr(NameBlock, "matches", no_rescan)
        page = index.search("in ext:txt")
        assert len(page) == 1
        assert not page.partial
        assert index.count("in") == 1
        monkeypatch.setattr(NameBlock, "matches", original)
        index._upsert([index._row(str(root / "printing.txt"), str(root), "x", stat, False)])
        assert len(index.search("in")) == 2
    finally:
        await index.close()


async def test_broad_letter_keeps_global_ties_pagination_and_narrow_letter_fast_path(
    tmp_path, monkeypatch
):
    root = tmp_path / "files"
    root.mkdir()
    index = LocalIndex(tmp_path / "index.db", [root])
    try:
        stat = SimpleNamespace(st_size=1, st_mtime=1791800000, st_ctime=1791800000)
        paths = [root / f"p-{number:05d}" / "common.txt" for number in range(4200)]
        paths += [root / "tail" / "invoice.txt"]
        index._upsert(
            [index._row(str(path), str(root), "x", stat, False) for path in reversed(paths)]
        )

        def no_full_expansion(*args):
            raise AssertionError("Single letters must not expand an unbounded candidate list")

        monkeypatch.setattr(index._names, "candidates", no_full_expansion)
        for descending in (False, True):
            expected = sorted(
                paths,
                key=lambda path: (path.name.casefold(), str(path).casefold()),
                reverse=descending,
            )
            page = index.search("o", max_results=3, offset=2, descending=descending)
            assert [row["full_path"] for row in page] == [str(path) for path in expected[2:5]]
            assert not page.partial
        page = index.search("i")
        assert [row["name"] for row in page] == ["invoice.txt"]
        assert not page.partial
        assert index.search("z") == []
    finally:
        await index.close()


async def test_legacy_cache_migrates_without_losing_rows_or_generations(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
        CREATE TABLE roots(root TEXT PRIMARY KEY,generation TEXT,scanned REAL);
        CREATE TABLE entries(id INTEGER PRIMARY KEY,root TEXT,path_key TEXT,full_path TEXT,
        full_path_fold TEXT,name TEXT,name_fold TEXT,parent TEXT,parent_fold TEXT,
        extension TEXT,extension_fold TEXT,is_directory INTEGER,size INTEGER,
        modified REAL,created REAL,generation TEXT);
        """)
        connection.execute("INSERT INTO roots VALUES(?,?,?)", (str(root), "original", 1234))
        stat = SimpleNamespace(st_size=42, st_mtime=1791800000, st_ctime=1791800000)
        row = LocalIndex._row(str(root / "PRINTING.txt"), str(root), "original", stat, False)
        connection.execute(
            "INSERT INTO entries VALUES(" + ",".join("?" for _ in range(16)) + ")", (17, *row)
        )
    index = LocalIndex(db, [root])
    try:
        assert index.search("in")[0]["name"] == "PRINTING.txt"
        assert index.sum_size("in") == 42
        assert index._writer.execute("SELECT id,generation FROM files").fetchone() == (
            17,
            "original",
        )
        assert index._writer.execute("SELECT scanned FROM roots").fetchone()[0] == 1234
        assert index._writer.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            index._writer.execute("SELECT type FROM sqlite_master WHERE name='entries'").fetchone()[
                0
            ]
            == "view"
        )
    finally:
        await index.close()


async def test_packed_names_follow_another_process_insert_update_and_delete(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    one, two = (
        LocalIndex(tmp_path / "shared.db", [root]),
        LocalIndex(tmp_path / "shared.db", [root]),
    )
    try:
        assert one.search("in") == []  # Warm an empty snapshot before another writer.
        stat = SimpleNamespace(st_size=1, st_mtime=1791800000, st_ctime=1791800000)
        for name in ["invoice.txt", "printing.txt", "minfil.py", "i-n.txt"]:
            two._upsert([two._row(str(root / name), str(root), "x", stat, False)])
        assert {row["name"] for row in one.search("in")} == {
            "invoice.txt",
            "printing.txt",
            "minfil.py",
        }
        two._delete_subtree(str(root), str(root / "printing.txt"))
        assert {row["name"] for row in one.search("in")} == {"invoice.txt", "minfil.py"}
        two._upsert([two._row(str(root / "printing.txt"), str(root), "x", stat, False)])
        assert one.count("in") == 3
        # A different configured root sharing the same DB must not leak entries.
        other = tmp_path / "other"
        other.mkdir()
        isolated = LocalIndex(tmp_path / "shared.db", [other])
        try:
            assert isolated.search("in") == []
        finally:
            await isolated.close()
    finally:
        await one.close()
        await two.close()


async def test_index_batches_publish_warm_filename_buffers(tmp_path):
    index = LocalIndex(tmp_path / "index.db", [tmp_path])
    try:
        stat = SimpleNamespace(st_size=1, st_mtime=1791800000, st_ctime=1791800000)
        index._upsert([index._row(str(tmp_path / "invoice.txt"), str(tmp_path), "x", stat, False)])
        assert index.status()["filename_memory_bytes"] > 0
    finally:
        await index.close()
