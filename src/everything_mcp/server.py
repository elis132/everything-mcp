"""
Everything MCP Server - The definitive MCP server for voidtools Everything.

Provides 5 tools for AI agents to search and analyse files at lightning speed
using voidtools Everything's real-time NTFS index.

Compatible with: Claude Code, Codex, Gemini, Kimi, Qwen, Cursor, Windsurf,
and any MCP-compatible client using stdio transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

from everything_mcp.backend import (
    FILE_TYPES,
    SORT_MAP,
    TIME_PERIODS,
    EverythingBackend,
    build_recent_query,
    build_type_query,
    human_size,
)
from everything_mcp.config import EverythingConfig

# ── Logging (stderr - required for stdio MCP transport) ──────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("everything_mcp")

# ── Globals (initialised during lifespan) ─────────────────────────────────

_backend: EverythingBackend | None = None
_config: EverythingConfig | None = None


@asynccontextmanager
async def lifespan(server):
    """Initialise Everything backend on startup, cleanup on shutdown."""
    global _backend, _config

    logger.info("Everything MCP starting - auto-detecting Everything installation…")
    _config = EverythingConfig.auto_detect()

    if _config.is_valid:
        logger.info("Connected: %s  (es: %s)", _config.version_info, _config.es_path)
    else:
        for err in _config.errors:
            logger.error("  %s", err)
        for warn in _config.warnings:
            logger.warning("  %s", warn)

    _backend = EverythingBackend(_config)
    try:
        yield
    finally:
        logger.info("Everything MCP shutting down.")


# ── Server instance ───────────────────────────────────────────────────────

mcp = FastMCP("everything_mcp", lifespan=lifespan)


def _get_backend() -> EverythingBackend:
    """Return the backend or raise with a clear message."""
    if _backend is None:
        raise RuntimeError("Everything MCP not initialised")
    if not _config or not _config.is_valid:
        errors = _config.errors if _config else ["Not initialised"]
        raise RuntimeError("Everything is not available. " + " ".join(errors))
    return _backend


# ═══════════════════════════════════════════════════════════════════════════
# Tool 1: everything_search - The Workhorse
# ═══════════════════════════════════════════════════════════════════════════


class SearchInput(BaseModel):
    """Input schema for ``everything_search``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description=(
            "Search query using Everything syntax. Examples: "
            "'*.py' (all Python files), "
            "'ext:py;js path:C:\\Projects' (Python/JS in Projects), "
            "'size:>10mb ext:log' (large logs), "
            "'dm:today ext:py' (Python files modified today), "
            "'content:TODO ext:py' (files containing TODO - requires content indexing), "
            "'\"exact phrase\"' (exact filename match), "
            "'regex:test_\\d+\\.py$' (regex). "
            "Combine with space (AND) or | (OR). Prefix ! to exclude."
        ),
        min_length=1,
        max_length=2000,
    )
    max_results: int = Field(
        default=50,
        description="Maximum results to return (1-500)",
        ge=1, le=500,
    )
    sort: str = Field(
        default="date-modified-desc",
        description=(
            "Sort order. Options: "
            + ", ".join(sorted(SORT_MAP.keys()))
        ),
    )

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, v: str) -> str:
        if v not in SORT_MAP:
            raise ValueError(f"Invalid sort option '{v}'. Valid: {', '.join(sorted(SORT_MAP.keys()))}")
        return v

    match_case: bool = Field(default=False, description="Case-sensitive search")
    match_whole_word: bool = Field(default=False, description="Match whole words only")
    match_regex: bool = Field(default=False, description="Treat query as regex")
    match_path: bool = Field(default=False, description="Match against full path, not just filename")
    offset: int = Field(default=0, description="Skip N results (pagination)", ge=0)


@mcp.tool(
    name="everything_search",
    annotations={
        "title": "Search Files & Folders",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def everything_search(params: SearchInput) -> str:
    """Search for files and folders instantly using voidtools Everything.

    Leverages Everything's real-time NTFS index for sub-millisecond search
    across all local and mapped drives.  Supports wildcards, regex, size/date
    filters, extension filters, path restrictions, and content search.
    """
    try:
        backend = _get_backend()
        results = await backend.search(
            query=params.query,
            max_results=params.max_results,
            sort=params.sort,
            match_case=params.match_case,
            match_whole_word=params.match_whole_word,
            match_regex=params.match_regex,
            match_path=params.match_path,
            offset=params.offset,
        )
        return _format_search_results(results, params.query, params.max_results, params.offset)
    except Exception as exc:
        return f"Error: {exc}"


# ═══════════════════════════════════════════════════════════════════════════
# Tool 2: everything_search_by_type - Category Search
# ═══════════════════════════════════════════════════════════════════════════


class SearchByTypeInput(BaseModel):
    """Input schema for ``everything_search_by_type``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_type: str = Field(
        ...,
        description="File type category: " + ", ".join(sorted(FILE_TYPES.keys())),
    )
    query: str = Field(
        default="",
        description="Additional search filter (e.g. 'config' to narrow results)",
    )
    path: str = Field(
        default="",
        description="Restrict search to this directory (e.g. 'C:\\Projects')",
    )
    max_results: int = Field(default=50, ge=1, le=500)
    sort: str = Field(default="date-modified-desc")

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, v: str) -> str:
        if v not in SORT_MAP:
            raise ValueError(f"Invalid sort option '{v}'. Valid: {', '.join(sorted(SORT_MAP.keys()))}")
        return v


@mcp.tool(
    name="everything_search_by_type",
    annotations={
        "title": "Search by File Type Category",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def everything_search_by_type(params: SearchByTypeInput) -> str:
    """Search for files by type category.

    Categories: audio, video, image, document, code, archive, executable,
    font, 3d, data.  Each maps to a curated list of file extensions.
    """
    try:
        backend = _get_backend()
        query = build_type_query(params.file_type, params.query, params.path)
        results = await backend.search(
            query=query,
            max_results=params.max_results,
            sort=params.sort,
        )
        label = f"type:{params.file_type}" + (f" {params.query}" if params.query else "")
        return _format_search_results(results, label, params.max_results)
    except Exception as exc:
        return f"Error: {exc}"


# ═══════════════════════════════════════════════════════════════════════════
# Tool 3: everything_find_recent - What Changed?
# ═══════════════════════════════════════════════════════════════════════════


class FindRecentInput(BaseModel):
    """Input schema for ``everything_find_recent``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    period: str = Field(
        default="1hour",
        description=(
            "How recent.  Options: "
            + ", ".join(sorted(TIME_PERIODS.keys(), key=lambda k: list(TIME_PERIODS.keys()).index(k)))
            + ".  Or raw Everything syntax like 'last2hours'."
        ),
    )
    path: str = Field(default="", description="Restrict to this directory path")
    extensions: str = Field(
        default="",
        description="Filter by extensions, e.g. 'py,js,ts' or 'py;js;ts'",
    )
    query: str = Field(default="", description="Additional search filter")
    max_results: int = Field(default=50, ge=1, le=500)


@mcp.tool(
    name="everything_find_recent",
    annotations={
        "title": "Find Recently Modified Files",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def everything_find_recent(params: FindRecentInput) -> str:
    """Find files modified within a recent time period.

    Ideal for discovering what changed in a project, tracking recent
    downloads, finding today's log files, etc.  Sorted newest-first.
    """
    try:
        backend = _get_backend()

        query = build_recent_query(params.period, params.path, params.extensions)
        if params.query:
            query = f"{query} {params.query}"

        results = await backend.search(
            query=query,
            max_results=params.max_results,
            sort="date-modified-desc",
        )
        return _format_search_results(results, f"recent ({params.period})", params.max_results)
    except Exception as exc:
        return f"Error: {exc}"


# ═══════════════════════════════════════════════════════════════════════════
# Tool 4: everything_file_details - Deep Inspection
# ═══════════════════════════════════════════════════════════════════════════


class FileDetailsInput(BaseModel):
    """Input schema for ``everything_file_details``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    paths: list[str] = Field(
        ...,
        description="File/folder paths to inspect (1-20)",
        min_length=1,
        max_length=20,
    )
    preview_lines: int = Field(
        default=0,
        description="Lines of text content to preview (0 = none, max 200)",
        ge=0, le=200,
    )


@mcp.tool(
    name="everything_file_details",
    annotations={
        "title": "Get File Details & Content Preview",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def everything_file_details(params: FileDetailsInput) -> str:
    """Get detailed metadata and optional content preview for specific files.

    Returns: full path, size, dates, type, permissions, hidden status.
    For directories: item count, subdirectories, file listing.
    For text files with preview_lines > 0: first N lines of content.
    """
    # Run blocking file I/O in thread pool to not block the event loop
    return await asyncio.to_thread(
        _get_file_details_sync,
        params.paths,
        params.preview_lines,
    )


def _get_file_details_sync(paths: list[str], preview_lines: int) -> str:
    """Synchronous implementation of file details gathering."""
    output_parts: list[str] = []

    for filepath in paths:
        p = Path(filepath)
        info: dict = {"path": str(p)}

        if not p.exists():
            info["error"] = "File not found"
            output_parts.append(json.dumps(info, indent=2, ensure_ascii=False))
            continue

        try:
            stat = p.stat()
            info["name"] = p.name
            info["type"] = "folder" if p.is_dir() else "file"

            if not p.is_dir():
                info["size"] = stat.st_size
                info["size_human"] = human_size(stat.st_size)
                info["extension"] = p.suffix.lstrip(".").lower()

            info["date_modified"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            info["date_created"] = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            info["date_accessed"] = datetime.fromtimestamp(stat.st_atime).strftime("%Y-%m-%d %H:%M:%S")
            info["read_only"] = not os.access(filepath, os.W_OK)

            # Windows hidden attribute or Unix dotfile
            file_attrs = getattr(stat, "st_file_attributes", 0)
            info["hidden"] = bool(file_attrs & 0x2) if file_attrs else p.name.startswith(".")

            # Directory listing
            if p.is_dir():
                try:
                    info.update(_summarize_directory(p))
                except PermissionError:
                    info["listing_error"] = "Permission denied"
                except OSError as exc:
                    info["listing_error"] = str(exc)

            # Content preview for text files
            elif preview_lines > 0:
                preview = _read_preview(p, preview_lines)
                if preview is not None:
                    info["preview"] = preview

        except PermissionError:
            info["error"] = "Permission denied"
        except OSError as exc:
            info["error"] = str(exc)

        output_parts.append(json.dumps(info, indent=2, ensure_ascii=False))

    return "\n---\n".join(output_parts)


# ═══════════════════════════════════════════════════════════════════════════
# Tool 5: everything_count_stats - Quick Analytics
# ═══════════════════════════════════════════════════════════════════════════


class CountStatsInput(BaseModel):
    """Input schema for ``everything_count_stats``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description=(
            "Search query to count/measure.  Same syntax as everything_search. "
            "Examples: 'ext:py path:C:\\Projects', 'ext:log size:>1mb', '*.tmp'"
        ),
        min_length=1,
        max_length=2000,
    )
    include_size: bool = Field(
        default=True,
        description="Also calculate total size of all matching files",
    )
    breakdown_by_extension: bool = Field(
        default=False,
        description="Break down count and size by file extension (samples top 200 results)",
    )


@mcp.tool(
    name="everything_count_stats",
    annotations={
        "title": "Count & Size Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def everything_count_stats(params: CountStatsInput) -> str:
    """Get count and size statistics for files matching a query.

    Fast way to understand the scope of a query without listing every file.
    Optionally breaks down by extension for a high-level overview.
    """
    try:
        backend = _get_backend()
        output: dict = {"query": params.query}

        # Count
        try:
            output["total_count"] = await backend.count(params.query)
        except Exception:
            output["count_note"] = "Count not available (es.exe may not support -get-result-count)"

        # Total size
        if params.include_size:
            try:
                total_size = await backend.get_total_size(params.query)
                if total_size >= 0:
                    output["total_size"] = total_size
                    output["total_size_human"] = human_size(total_size)
            except Exception:
                output["size_note"] = "Total size not available"

        # Extension breakdown
        if params.breakdown_by_extension:
            try:
                sample_limit = 500
                results = await backend.search(
                    params.query,
                    max_results=sample_limit,
                    sort="name",
                )
                ext_stats: dict[str, dict] = {}
                sampled_files = 0
                for r in results:
                    if r.is_dir:
                        continue
                    sampled_files += 1
                    ext = r.extension or "(no extension)"
                    entry = ext_stats.setdefault(ext, {"count": 0, "total_size": 0})
                    entry["count"] += 1
                    if r.size >= 0:
                        entry["total_size"] += r.size

                sorted_exts = sorted(ext_stats.items(), key=lambda x: x[1]["count"], reverse=True)
                breakdown = {}
                for ext, stats in sorted_exts[:30]:
                    breakdown[ext] = {
                        "count": stats["count"],
                        "total_size": stats["total_size"],
                        "total_size_human": human_size(stats["total_size"]),
                    }
                output["extension_breakdown"] = breakdown
                output["breakdown_note"] = (
                    f"Based on {sampled_files} sampled files from first {len(results)} "
                    f"results (max sample {sample_limit}); directories excluded."
                )
            except Exception as exc:
                output["breakdown_error"] = str(exc)

        return json.dumps(output, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


# ═══════════════════════════════════════════════════════════════════════════
# Resource: Health Check
# ═══════════════════════════════════════════════════════════════════════════


@mcp.resource("everything://status")
async def get_status() -> str:
    """Get the current status of the Everything connection."""
    if _backend:
        status = await _backend.health_check()
    else:
        status = {"status": "not initialised"}
    return json.dumps(status, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _format_search_results(
    results: list,
    query_label: str,
    max_results: int,
    offset: int = 0,
) -> str:
    """Format search results into a clean, readable string for LLM consumption."""
    if not results:
        return f"No results found for: {query_label}"

    header = f"Found {len(results)} results for: {query_label}"
    if offset > 0:
        header += f" (offset: {offset})"
    lines = [header, ""]

    for r in results:
        d = r.to_dict() if hasattr(r, "to_dict") else r
        path = d.get("path", "?")
        ftype = d.get("type", "file")
        size_h = d.get("size_human", "")
        dm = d.get("date_modified", "")

        prefix = "[DIR]" if ftype == "folder" else "[FILE]"
        meta_parts: list[str] = []
        if size_h:
            meta_parts.append(size_h)
        if dm:
            meta_parts.append(dm)

        meta = f"  ({', '.join(meta_parts)})" if meta_parts else ""
        lines.append(f"  {prefix} {path}{meta}")

    if len(results) >= max_results:
        lines.append("")
        lines.append(
            f"Showing first {max_results} results.  "
            "Use 'offset' to paginate or refine the query."
        )

    return "\n".join(lines)


# ── Text file preview ─────────────────────────────────────────────────────

# Extensions we can safely read as text
_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    # Text & docs
    "txt", "md", "mdx", "rst", "adoc", "org",
    # Python
    "py", "pyi", "pyw", "pyx", "pxd",
    # JavaScript/TypeScript
    "js", "mjs", "cjs", "ts", "mts", "cts", "jsx", "tsx",
    # Web frameworks
    "vue", "svelte", "astro", "marko",
    # C family
    "c", "cpp", "cc", "cxx", "h", "hpp", "hxx", "cs", "java", "m", "mm",
    # Systems languages
    "go", "rs", "rb", "php", "swift", "kt", "kts", "scala", "r", "lua",
    # Shell
    "sh", "bash", "zsh", "fish", "ps1", "psm1", "psd1", "bat", "cmd",
    # Database & query
    "sql", "prisma", "graphql", "gql",
    # Web
    "html", "htm", "css", "scss", "sass", "less", "styl", "pcss",
    # Data formats
    "json", "jsonc", "json5", "jsonl", "ndjson",
    "xml", "xsl", "xslt", "xsd", "svg", "rss", "atom",
    "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "properties",
    "csv", "tsv", "log",
    # Config files (with extensions)
    "gitignore", "gitattributes", "gitmodules", "npmrc", "nvmrc", "yarnrc",
    "dockerignore", "editorconfig", "eslintrc", "prettierrc", "babelrc",
    "stylelintrc", "browserslistrc",
    # Build tools
    "makefile", "dockerfile", "cmake", "gradle", "sbt", "cabal", "bazel",
    # Academic
    "tex", "bib", "cls", "sty",
    # Hardware
    "asm", "s", "v", "sv", "vhd", "vhdl",
    # Modern languages
    "dart", "zig", "nim", "hx", "odin", "jai", "vlang",
    # Functional
    "ex", "exs", "erl", "hrl", "hs", "lhs", "ml", "mli", "fs", "fsi", "fsx",
    "clj", "cljs", "cljc", "edn", "lisp", "el", "rkt", "scm", "fnl",
    # Other
    "pro", "pri", "qml", "proto", "thrift", "capnp",
    "tf", "hcl", "nix", "dhall", "jsonnet", "cue",
    "http", "rest", "lock",
})

# Filenames (no extension) that are always text
_TEXT_FILENAMES: frozenset[str] = frozenset({
    "makefile", "dockerfile", "cmakelists.txt", "rakefile", "gemfile",
    "procfile", "vagrantfile", "brewfile", "justfile", "taskfile",
    "license", "licence", "readme", "authors", "contributors",
    "changelog", "changes", "history", "news", "todo",
})

_MAX_DIR_SCAN_ITEMS = 10_000
_MAX_SUBDIRECTORY_SAMPLE = 20
_MAX_FILE_SAMPLE = 30
_MAX_PREVIEW_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_PREVIEW_CHARS = 50_000


def _summarize_directory(path: Path) -> dict[str, object]:
    """Return bounded directory metadata without loading all entries in memory."""
    dirs: list[str] = []
    files: list[str] = []
    scanned = 0
    truncated = False

    with os.scandir(path) as entries:
        for entry in entries:
            if scanned >= _MAX_DIR_SCAN_ITEMS:
                truncated = True
                break
            scanned += 1
            try:
                if entry.is_dir(follow_symlinks=False):
                    if len(dirs) < _MAX_SUBDIRECTORY_SAMPLE:
                        dirs.append(entry.name)
                elif entry.is_file(follow_symlinks=False) and len(files) < _MAX_FILE_SAMPLE:
                    files.append(entry.name)
            except OSError:
                continue

    summary: dict[str, object] = {
        "item_count": scanned,
        "subdirectories": sorted(dirs),
        "files_sample": sorted(files),
    }
    if truncated:
        summary["note"] = (
            f"Directory scan capped at {_MAX_DIR_SCAN_ITEMS} entries; samples may be incomplete"
        )
    elif scanned > (_MAX_SUBDIRECTORY_SAMPLE + _MAX_FILE_SAMPLE):
        summary["note"] = f"Showing first items of {scanned} total"
    return summary


def _read_preview(path: Path, max_lines: int) -> str | None:
    """Read the first *max_lines* lines of a text file.

    Returns ``None`` for binary files or files that can't be read.
    """
    try:
        if path.stat().st_size > _MAX_PREVIEW_FILE_SIZE:
            return "(file too large for preview)"
    except OSError:
        return None

    ext = path.suffix.lstrip(".").lower()
    name_lower = path.name.lower()
    stem_lower = path.stem.lower()

    is_text = (
        ext in _TEXT_EXTENSIONS
        or name_lower in _TEXT_FILENAMES
        or stem_lower in _TEXT_FILENAMES
        or name_lower.startswith(".")  # dotfiles are usually text
    )

    if not is_text:
        # Sniff for binary content
        try:
            with open(path, "rb") as f:
                chunk = f.read(512)
                if b"\x00" in chunk:
                    return None  # binary
                is_text = True
        except (OSError, PermissionError):
            return None

    if not is_text:
        return None

    # Read lines with encoding fallback
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=encoding) as f:
                lines: list[str] = []
                total_chars = 0
                truncated = False
                for _ in range(max_lines):
                    remaining = _MAX_PREVIEW_CHARS - total_chars
                    if remaining <= 0:
                        truncated = True
                        break

                    # Bound each read to avoid huge single-line payloads.
                    line = f.readline(remaining + 1)
                    if not line:
                        break

                    if len(line) > remaining:
                        line = line[:remaining]
                        truncated = True

                    total_chars += len(line)
                    lines.append(line.rstrip("\n\r"))

                    if total_chars >= _MAX_PREVIEW_CHARS:
                        truncated = True
                        break

                if truncated:
                    lines.append("... [preview truncated]")
                return "\n".join(lines)
        except UnicodeDecodeError:
            continue
        except (OSError, PermissionError):
            return None

    return "(unable to decode file content)"
