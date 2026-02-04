"""
Backend for communicating with voidtools Everything via es.exe.

Handles query execution, result parsing, and metadata enrichment.

Design decision: es.exe is invoked *without* ``-size -dm -dc`` flags.
This produces clean one-path-per-line output that is trivially parseable
regardless of es.exe version, locale, or output encoding. Metadata is
then enriched via ``os.stat()`` — fast, reliable, and cross-version.
"""

from __future__ import annotations

import asyncio
import contextlib
import locale
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from everything_mcp.config import EverythingConfig

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

# Time period shortcuts → Everything dm: values
TIME_PERIODS: dict[str, str] = {
    "1min": "last1min",
    "5min": "last5mins",
    "10min": "last10mins",
    "15min": "last15mins",
    "30min": "last30mins",
    "1hour": "last1hour",
    "2hours": "last2hours",
    "6hours": "last6hours",
    "12hours": "last12hours",
    "today": "today",
    "yesterday": "yesterday",
    "1day": "last1day",
    "3days": "last3days",
    "1week": "last1week",
    "2weeks": "last2weeks",
    "1month": "last1month",
    "3months": "last3months",
    "6months": "last6months",
    "1year": "last1year",
}


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
        cmd = self._base_cmd()

        # Result count & offset
        cmd.extend(["-n", str(min(max_results, self.config.max_results_cap))])
        if offset > 0:
            cmd.extend(["-o", str(offset)])

        # Sort
        sort_value = SORT_MAP.get(sort, sort)
        cmd.extend(["-sort", sort_value])

        # Match modifiers
        if match_case:
            cmd.append("-case")
        if match_whole_word:
            cmd.append("-w")
        if match_regex:
            cmd.append("-r")
        if match_path:
            cmd.append("-p")

        # NOTE: We intentionally omit -size / -dm / -dc.  Keeping es.exe
        # output as plain one-path-per-line makes parsing trivial and
        # version-independent.  Metadata comes from os.stat() below.
        cmd.append(query)

        stdout, stderr, rc = await self._run(cmd)

        if rc != 0:
            msg = stderr.strip() or stdout.strip() or f"es.exe exited with code {rc}"
            raise RuntimeError(f"Everything search failed: {msg}")

        # Parse/stat can be expensive for large result sets; keep event loop responsive.
        return await asyncio.to_thread(_parse_paths_and_stat, stdout)

    # ── Aggregate queries ─────────────────────────────────────────────

    async def count(self, query: str) -> int:
        """Return the number of results for *query* without listing them."""
        cmd = self._base_cmd()
        # Important: do not combine with "-n 0" because es.exe then reports 0.
        cmd.extend(["-get-result-count", query])
        stdout, stderr, rc = await self._run(cmd)

        if rc != 0:
            raise RuntimeError(f"Count failed: {stderr.strip() or stdout.strip()}")

        try:
            return int(stdout.strip())
        except ValueError:
            return -1

    async def get_total_size(self, query: str) -> int:
        """Return the total size in bytes of all files matching *query*."""
        cmd = self._base_cmd()
        # Important: do not combine with "-n 0" because es.exe then reports 0.
        cmd.extend(["-get-total-size", query])
        stdout, stderr, rc = await self._run(cmd)

        if rc != 0:
            raise RuntimeError(f"Total size failed: {stderr.strip() or stdout.strip()}")

        try:
            return int(stdout.strip())
        except ValueError:
            return -1

    # ── Health check ──────────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Check if Everything is accessible and return status info."""
        if not self.config.is_valid:
            return {
                "status": "error",
                "errors": self.config.errors,
                "es_path": self.config.es_path or "not found",
            }

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
            return {"status": "error", "message": str(exc)}

    # ── Internals ─────────────────────────────────────────────────────

    def _base_cmd(self) -> list[str]:
        """Build the base es.exe command with optional instance flag."""
        cmd = [self.config.es_path]
        if self.config.instance:
            cmd.extend(["-instance", self.config.instance])
        return cmd

    async def _run(self, cmd: list[str]) -> tuple[str, str, int]:
        """Run es.exe asynchronously.  Returns ``(stdout, stderr, returncode)``."""
        try:
            kwargs: dict = dict(
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # CREATE_NO_WINDOW only exists on Windows
            create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if create_no_window:
                kwargs["creationflags"] = create_no_window

            process = await asyncio.create_subprocess_exec(*cmd, **kwargs)

            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(),
                timeout=self.config.timeout,
            )

            return (
                _decode_output(stdout_raw),
                _decode_output(stderr_raw),
                process.returncode or 0,
            )

        except asyncio.TimeoutError as exc:
            with contextlib.suppress(Exception):
                process.kill()  # type: ignore[possibly-undefined]
            raise RuntimeError(
                f"Search timed out after {self.config.timeout}s. "
                "Try a more specific query or increase timeout."
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"es.exe not found at: {self.config.es_path}. "
                "Verify Everything is installed."
            ) from exc


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
            pass  # Inaccessible — still return the path

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
