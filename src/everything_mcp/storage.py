"""Compact metadata storage: directory paths once, filenames once, no filename FTS."""

from __future__ import annotations

import os
import sqlite3

BLOCK_SIZE = 4096


def register_functions(connection: sqlite3.Connection) -> None:
    connection.create_function("fold", 1, str.casefold, deterministic=True)
    connection.create_function(
        "path_key", 1, str.casefold if os.name == "nt" else str, deterministic=True
    )
    connection.create_function(
        "suffix", 1, lambda name: os.path.splitext(name)[1].lstrip("."), deterministic=True
    )


def schema() -> str:
    separator = os.sep.replace("'", "''")
    return f"""
CREATE TABLE IF NOT EXISTS roots(root TEXT PRIMARY KEY, generation TEXT, scanned REAL);
CREATE TABLE IF NOT EXISTS locations(
 id INTEGER PRIMARY KEY, root TEXT NOT NULL, parent TEXT NOT NULL,
 parent_key TEXT GENERATED ALWAYS AS (path_key(parent)) VIRTUAL,
 UNIQUE(root,parent_key));
CREATE TABLE IF NOT EXISTS files(
 id INTEGER PRIMARY KEY, location_id INTEGER NOT NULL REFERENCES locations(id),
 name TEXT NOT NULL, name_key TEXT GENERATED ALWAYS AS (path_key(name)) VIRTUAL,
 name_fold TEXT GENERATED ALWAYS AS (fold(name)) VIRTUAL,
 extension TEXT GENERATED ALWAYS AS (CASE WHEN is_directory THEN '' ELSE suffix(name) END) VIRTUAL,
 extension_fold TEXT GENERATED ALWAYS AS (fold(extension)) VIRTUAL,
 is_directory INTEGER NOT NULL, size INTEGER NOT NULL,
 modified REAL NOT NULL, created REAL NOT NULL, generation TEXT NOT NULL,
 UNIQUE(location_id,name_key));
CREATE INDEX IF NOT EXISTS files_name ON files(name_fold,id);
CREATE INDEX IF NOT EXISTS files_extension ON files(extension_fold,name_fold,id);
CREATE INDEX IF NOT EXISTS files_modified ON files(modified,id);
CREATE INDEX IF NOT EXISTS files_size ON files(size,id);
CREATE TABLE IF NOT EXISTS name_blocks(block INTEGER PRIMARY KEY, revision INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
 INSERT INTO name_blocks VALUES(new.id/{BLOCK_SIZE},1)
 ON CONFLICT(block) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
 INSERT INTO name_blocks VALUES(old.id/{BLOCK_SIZE},1)
 ON CONFLICT(block) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE OF name,location_id ON files
WHEN old.name IS NOT new.name OR old.location_id IS NOT new.location_id BEGIN
 INSERT INTO name_blocks VALUES(new.id/{BLOCK_SIZE},1)
 ON CONFLICT(block) DO UPDATE SET revision=revision+1;
END;
CREATE VIEW IF NOT EXISTS entries AS
 SELECT f.id,p.root,
 path_key(rtrim(p.parent,'/\\')||'{separator}'||f.name) AS path_key,
 rtrim(p.parent,'/\\')||'{separator}'||f.name AS full_path,
 fold(rtrim(p.parent,'/\\')||'{separator}'||f.name) AS full_path_fold,
 f.name,f.name_fold,p.parent,fold(p.parent) AS parent_fold,
 f.extension,f.extension_fold,f.is_directory,f.size,f.modified,f.created,f.generation
 FROM files f JOIN locations p ON p.id=f.location_id;
CREATE VIEW IF NOT EXISTS candidate_entries AS
 SELECT f.id,p.root,
 path_key(rtrim(p.parent,'/\\')||'{separator}'||f.name) AS path_key,
 rtrim(p.parent,'/\\')||'{separator}'||f.name AS full_path,
 fold(rtrim(p.parent,'/\\')||'{separator}'||f.name) AS full_path_fold,
 f.name,f.name_fold,p.parent,fold(p.parent) AS parent_fold,
 f.extension,f.extension_fold,f.is_directory,f.size,f.modified,f.created,f.generation
 FROM files f NOT INDEXED JOIN locations p ON p.id=f.location_id;
CREATE VIEW IF NOT EXISTS name_ordered_entries AS
 SELECT f.id,p.root,
 rtrim(p.parent,'/\\')||'{separator}'||f.name AS full_path,
 fold(rtrim(p.parent,'/\\')||'{separator}'||f.name) AS full_path_fold,
 f.name,f.name_fold,p.parent,f.extension,f.extension_fold,
 f.is_directory,f.size,f.modified,f.created
 FROM files f INDEXED BY files_name JOIN locations p ON p.id=f.location_id;
CREATE TRIGGER IF NOT EXISTS entries_delete INSTEAD OF DELETE ON entries BEGIN
 DELETE FROM files WHERE id=old.id;
END;
CREATE TRIGGER IF NOT EXISTS entries_generation INSTEAD OF UPDATE OF generation ON entries BEGIN
 UPDATE files SET generation=new.generation WHERE id=old.id;
END;
PRAGMA user_version=2;
"""


UPSERT = """INSERT INTO files(location_id,name,is_directory,size,modified,created,generation)
 VALUES(?,?,?,?,?,?,?) ON CONFLICT(location_id,name_key) DO UPDATE SET
 name=excluded.name,is_directory=excluded.is_directory,size=excluded.size,
 modified=excluded.modified,created=excluded.created,generation=excluded.generation"""


def initialize(connection: sqlite3.Connection) -> None:
    """Atomically migrate old caches, preserving ids, metadata and scan generations.

    VACUUM reclaims the old FTS/index pages only on this one-time migration.
    New caches never build that storage in the first place.
    """
    register_functions(connection)
    legacy = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries'"
    ).fetchone()
    if not legacy:
        connection.executescript(schema())
        return
    # A single explicit transaction prevents a half-migrated cache after failure.
    connection.executescript("BEGIN IMMEDIATE; ALTER TABLE entries RENAME TO legacy_entries;")
    try:
        for name in ("entries_ai", "entries_ad", "entries_au"):
            connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute("DROP TABLE IF EXISTS entries_fts")
        # executescript would commit the migration; execute individual statements.
        statements = schema().splitlines()
        buffer = ""
        for line in statements:
            buffer += line + "\n"
            if sqlite3.complete_statement(buffer):
                connection.execute(buffer)
                buffer = ""
        connection.execute(
            "INSERT OR IGNORE INTO locations(root,parent) SELECT DISTINCT root,parent FROM legacy_entries"
        )
        connection.execute("""INSERT INTO files(id,location_id,name,is_directory,size,modified,created,generation)
            SELECT e.id,p.id,e.name,e.is_directory,e.size,e.modified,e.created,e.generation
            FROM legacy_entries e JOIN locations p ON p.root=e.root AND p.parent_key=path_key(e.parent)""")
        connection.execute("DROP TABLE legacy_entries")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    connection.execute("VACUUM")
