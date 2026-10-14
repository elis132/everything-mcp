"""Packed UTF-8 filename snapshots, searched by native bytes.find, not Python objects.

Each immutable block is invalidated by transactional SQLite revisions. Separate
MCP processes see each other's changes; a query uses its own database snapshot.
No per-file Python dictionary and no repeated full paths or FTS postings.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from array import array
from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
from itertools import islice

from everything_mcp.storage import BLOCK_SIZE


@dataclass(frozen=True)
class NameBlock:
    block: int
    revision: int
    data: bytes
    offsets: array
    ids: array

    @classmethod
    def load(cls, connection: sqlite3.Connection, block: int, revision: int, roots: list[str]):
        data, offsets, ids = bytearray(), array("I"), array("Q")
        rows = connection.execute(
            "SELECT f.id,f.name FROM files f NOT INDEXED JOIN locations p ON p.id=f.location_id "
            "WHERE f.id>=? AND f.id<? AND p.root IN ("
            + ",".join("?" for _ in roots)
            + ") ORDER BY f.id",
            (block * BLOCK_SIZE, (block + 1) * BLOCK_SIZE, *roots),
        )
        for row_id, name in rows:
            offsets.append(len(data))
            ids.append(row_id)
            data.extend(name.casefold().encode("utf-8", errors="surrogatepass"))
            data.append(0)
        return cls(block, revision, bytes(data), offsets, ids)

    def matches(self, term: bytes):
        position = 0
        while (position := self.data.find(term, position)) >= 0:
            entry = bisect_right(self.offsets, position) - 1
            yield self.ids[entry]
            position = self.offsets[entry + 1] if entry + 1 < len(self.offsets) else len(self.data)

    @property
    def bytes_used(self):
        return (
            len(self.data)
            + len(self.offsets) * self.offsets.itemsize
            + len(self.ids) * self.ids.itemsize
        )


class CompactNames:
    def __init__(self, roots: list[str]):
        self.roots = roots
        self._blocks: dict[int, NameBlock] = {}
        self._lock = threading.RLock()
        self._candidates: OrderedDict[tuple, str | None] = OrderedDict()

    def snapshot(self, connection: sqlite3.Connection) -> tuple[NameBlock, ...]:
        with self._lock:
            revisions = connection.execute(
                "SELECT block,revision FROM name_blocks ORDER BY block"
            ).fetchall()
            snapshot = []
            for block, revision in revisions:
                cached = self._blocks.get(block)
                if cached is None or cached.revision != revision:
                    cached = self._blocks[block] = NameBlock.load(
                        connection, block, revision, self.roots
                    )
                snapshot.append(cached)
            return tuple(snapshot)

    def candidates(self, connection: sqlite3.Connection, term: str) -> str:
        if not term or "\0" in term:
            return "[]"
        limited = self.limited_candidates(connection, term)
        if limited is not None:
            return limited
        snapshot = self.snapshot(connection)
        needle = term.encode("utf-8", errors="surrogatepass")
        return json.dumps(
            [row_id for block in snapshot for row_id in block.matches(needle)],
            separators=(",", ":"),
        )

    def limited_candidates(self, connection: sqlite3.Connection, term: str, limit: int = 4096):
        """Return all IDs or None when broad, without expanding a large posting list."""
        snapshot = self.snapshot(connection)
        key = (term, limit, tuple((block.block, block.revision) for block in snapshot))
        with self._lock:
            if key in self._candidates:
                self._candidates.move_to_end(key)
                return self._candidates[key]
        needle = term.encode("utf-8", errors="surrogatepass")
        matches = (row_id for block in snapshot for row_id in block.matches(needle))
        ids = list(islice(matches, limit + 1))
        result = None if len(ids) > limit else json.dumps(ids, separators=(",", ":"))
        # Small LRU: never retain broad posting lists or old filename buffers.
        if result is None or len(result) <= 65536:
            with self._lock:
                self._candidates[key] = result
                self._candidates.move_to_end(key)
                while len(self._candidates) > 16:
                    self._candidates.popitem(last=False)
        return result

    @property
    def candidate_cache_bytes(self):
        with self._lock:
            return sum(len(value) for value in self._candidates.values() if value is not None)

    @property
    def bytes_used(self):
        with self._lock:
            return sum(block.bytes_used for block in self._blocks.values())


class SearchPage(list):
    def __init__(self, rows=(), *, partial: bool = False, note: str = ""):
        super().__init__(rows)
        self.partial = partial
        self.note = note
