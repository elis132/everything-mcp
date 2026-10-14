"""Async MCP adapter around our own persistent, watched local index."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from everything_mcp.config import EverythingConfig
from everything_mcp.index import LocalIndex
from everything_mcp.names import SearchPage

SORT_MAP = {
    name: name
    for name in (
        "name",
        "name-desc",
        "path",
        "path-desc",
        "size",
        "size-asc",
        "size-desc",
        "date-modified",
        "date-modified-asc",
        "date-modified-desc",
        "date-created",
        "date-created-asc",
        "date-created-desc",
        "extension",
    )
}

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


@dataclass(slots=True)
class SearchResult:
    path: str
    name: str
    is_dir: bool = False
    size: int = -1
    date_modified: str = ""
    date_created: str = ""
    extension: str = ""

    def to_dict(self) -> dict:
        result = {"path": self.path, "name": self.name, "type": "folder" if self.is_dir else "file"}
        if not self.is_dir and self.size >= 0:
            result.update(size=self.size, size_human=human_size(self.size))
        for key in ("date_modified", "date_created", "extension"):
            if value := getattr(self, key):
                result[key] = value
        return result


class EverythingBackend:
    """Own-index lifecycle and bounded worker-thread queries; no external executable."""

    def __init__(self, config: EverythingConfig) -> None:
        self.config = config
        self.index: LocalIndex | None = None
        self._start_lock = asyncio.Lock()
        self._queries = asyncio.Semaphore(4)

    async def start(self) -> None:
        if not self.config.is_valid:
            raise RuntimeError("Invalid index configuration: " + "; ".join(self.config.errors))
        async with self._start_lock:
            if self.index is None:
                index = await asyncio.to_thread(LocalIndex, self.config.db_path, self.config.roots)
                try:
                    await index.start()
                except BaseException:
                    await index.close()
                    raise
                self.index = index

    async def close(self) -> None:
        if self.index is not None:
            await self.index.close()
            self.index = None

    async def wait_ready(self, timeout: float | None = None) -> None:
        await self.start()
        await self.index.wait_ready(timeout=self.config.timeout if timeout is None else timeout)

    async def _ready_index(self) -> LocalIndex:
        await self.start()
        # The empty index and each committed first-run batch are queryable.
        # Tool output labels building/partial coverage; readiness is never a gate.
        return self.index

    async def _query(self, method: str, query: str, **kwargs):
        index = await self._ready_index()
        try:
            await asyncio.wait_for(self._queries.acquire(), self.config.timeout)
        except TimeoutError as exc:
            raise RuntimeError("Local index query queue timed out; retry shortly.") from exc
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    getattr(index, method), query, timeout=self.config.timeout, **kwargs
                ),
                self.config.timeout + 0.25,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"Local index query exceeded {self.config.timeout:g} seconds; narrow the query."
            ) from exc
        finally:
            self._queries.release()

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
        if sort not in SORT_MAP:
            raise ValueError("Unsupported sort: " + sort)
        if offset < 0 or max_results < 1:
            raise ValueError("Offset must be nonnegative and maximum results positive")
        rows = await self._query(
            "search",
            query,
            max_results=min(max_results, self.config.max_results_cap),
            sort=sort,
            offset=offset,
            match_case=match_case,
            match_whole_word=match_whole_word,
            regex=match_regex,
            match_path=match_path,
        )
        return SearchPage(
            [
                SearchResult(
                    path=row["full_path"],
                    name=row["name"],
                    is_dir=bool(row["is_directory"]),
                    size=row.get("size", -1),
                    date_modified=row.get("date_modified", ""),
                    date_created=row.get("date_created", ""),
                    extension=row.get("extension", Path(row["name"]).suffix.lstrip(".")),
                )
                for row in rows
            ],
            partial=getattr(rows, "partial", False),
            note=getattr(rows, "note", ""),
        )

    async def count(self, query: str) -> int:
        return await self._query("count", query)

    async def get_total_size(self, query: str) -> int:
        return await self._query("sum_size", query)

    def default_sort(self) -> str:
        return "date-modified-desc"

    async def health_check(self) -> dict:
        if not self.config.is_valid:
            return {"state": "error", "errors": self.config.errors, "independent": True}
        if self.index is None:
            return {
                "state": "not_started",
                "roots": [str(p) for p in self.config.roots],
                "independent": True,
            }
        status = await asyncio.wait_for(asyncio.to_thread(self.index.status), self.config.timeout)
        return {**status, "independent": True}


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
