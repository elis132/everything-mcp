# Everything MCP: independent local search

<!-- mcp-name: io.github.elis132/everything-mcp -->

This branch replaces the Everything CLI integration with an owned, persistent file index. It requires **no es.exe, Everything installation, SDK DLL, Windows service, or network API**. The package name and existing five agent tools remain, with an additional index-status tool.

This implementation is local and unreleased. Installing `uvx everything-mcp` from PyPI still selects the published version; use the checkout below to run this branch.

## Run this checkout

Python 3.10+ and SQLite 3.35+ with JSON functions are required. Standard Python distributions normally include them. Filename search does not require FTS5.

```powershell
cd D:\Dev\Everything\everything-mcp
uv venv
uv pip install -e '.[dev]'
$env:EVERYTHING_MCP_ROOTS='["D:\\Dev","C:\\Users\\elish\\Documents"]'
.venv\Scripts\everything-mcp.exe
```

The process speaks MCP over stdin/stdout, so a terminal launch waits for a client. Logs go to stderr. There is no GUI.

Example agent MCP configuration for this local checkout:

```json
{
  "mcpServers": {
    "everything": {
      "command": "D:\\Dev\\Everything\\everything-mcp\\.venv\\Scripts\\everything-mcp.exe",
      "env": {
        "EVERYTHING_MCP_ROOTS": "[\"D:\\\\Dev\", \"C:\\\\Users\\\\elish\\\\Documents\"]",
        "EVERYTHING_MCP_DB": "C:\\Users\\elish\\AppData\\Local\\everything-mcp\\index.sqlite3"
      }
    }
  }
}
```

On Linux/macOS, install the same package and use the virtual environment's `bin/everything-mcp` executable with roots such as `["/home/alice/projects"]` or `["/Users/alice/Documents"]`. The Windows native metadata helper is optional experimental code; normal indexing uses portable directory traversal. On systems without a filesystem birth time, creation dates remain unknown and sorting by creation date is rejected. Platform runtime verification is recorded in `docs/VALIDATION.md`.

Both Apple silicon and Intel Macs use the same Python code. Intel macOS installations constrain the MCP SDK's transitive `cryptography` dependency to `<49`, because upstream removed Intel macOS support in version 49. See the [upstream changelog](https://cryptography.io/en/49.0.0/changelog/). Use Python 3.10 or newer with SQLite 3.35 or newer; Apple's bundled Python 3.9 is too old.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `EVERYTHING_MCP_ROOTS` | JSON array of directories to index recursively | Current user's home directory |
| `EVERYTHING_MCP_DB` | Persistent SQLite cache file | LocalAppData/everything-mcp on Windows; XDG_CACHE_HOME or ~/.cache elsewhere |
| `EVERYTHING_MCP_TIMEOUT` | Query budget in seconds; searches fall back to labeled partial results | 10 |
| `EVERYTHING_MCP_MAX_RESULTS` | Backend result cap | 1000; search tools additionally cap requests at 500 |

Select roots deliberately. Files outside them will not appear. Symlinks/reparse points are recorded but not traversed, preventing loops. If a configured root or subtree is unavailable, cached entries are retained and status, search, and statistics report incomplete coverage. POSIX names that cannot be represented as UTF-8 text are skipped without aborting the scan and also mark coverage incomplete. No administrator privileges are required for normal indexing.

## Tools

| Tool | Purpose |
|---|---|
| `everything_search` | Filename/path search, filters, stable sorting, pagination |
| `everything_search_by_type` | Existing document/code/image/video/archive categories |
| `everything_find_recent` | Modified-date filters |
| `everything_file_details` | Current metadata and optional text preview for explicit paths |
| `everything_count_stats` | Counts and aggregate sizes from the owned index |
| `everything_index_status` | Roots, readiness, searchable entry count, packed filename bytes, watcher state and errors |

Search is filename/metadata search. File contents are read only for explicit previews. Unsupported Everything functions are rejected rather than silently producing incomplete results. Existing clients retain the tool names and Pydantic input structure.

## Query examples

```text
invoice !draft
"invoice alpha"
ext:pdf;txt
path:"D:\Dev" ext:py
parent:"D:\Dev\project"
folder: project
file: size:>10mb
dm:today
dm:last7days
invoice | photo ext:jpg
regex:^test_.*\.py$
```

Spaces mean AND; OR (`|`) binds before AND, matching the tested Everything behavior. The last mixed expression means `(invoice OR photo) AND ext:jpg`. `<...>` groups expressions. Wildcards `*` and `?`, negation `!`, case and whole-word options are supported. Advanced content, duplicate, empty-folder, media-tag and attribute functions are not implemented.

## How it stays fast

Directory paths are stored once in a shared directory table. Per-file rows store a filename and metadata; folded names and full paths are derived rather than stored repeatedly. Filename FTS and the large path-covering indexes have been removed.

Filename search scans packed UTF-8 buffers in memory with native `bytes.find`, including one- and two-character substrings. `in` matches `invoice.pdf`, `printing.txt` and `minfil.py`; it is not restricted to word prefixes. Candidate row IDs feed exact SQL checks, metadata filters and global sorting. The packed buffers use roughly 33 MB per million benchmark filenames, separate from SQLite and Python process overhead. Transactional block revisions invalidate only changed filename blocks, including changes from another MCP process.

Single-letter filename queries sorted by name adapt before expanding a large candidate list: at most 4,097 IDs are collected to distinguish sparse from broad matches. Sparse terms keep the candidate plan; broad terms walk the existing filename B-tree in order and stop at the requested page. The primary query budget for this path is capped at 250 ms before the labeled fallback. Complete pages retain global path tie-breaking, ascending/descending order and pagination. Compound queries, regex, path searches and other sort orders retain the general query path.

A 16-entry candidate cache reuses small posting lists across repeated searches and metadata filters. Keys include transactional block revisions, so changes from another process invalidate results correctly. Each cached JSON list is capped at 64 KiB; broad lists are never retained. Status exposes their payload bytes separately as `candidate_cache_bytes`. The packed filename buffers are eagerly warmed ordinary Python bytes, not mmap allocations; `madvise` does not apply to them.

Searches have a hard result limit. If a full sorted query exceeds its reserved budget, a bounded fallback inspects at most 50,000 index rows and returns available matches. Its MCP response explicitly labels partial coverage and sorting/pagination within the subset. Counts and size aggregates are exact for the current index snapshot or return an error; a separate freshness note warns when that snapshot does not cover every configured path. Broad path searches and regex can still require scans.

The first discovered entry is committed immediately; further batches publish every 256 entries or about 100 ms of traversal. Searches run even before the first batch and return currently available results. While building, tool responses include the searchable entry count and state that coverage is partial. There is no wait for the entire first scan to finish.

The database persists across restarts, so cached searches can run while startup reconciles changes missed while offline. Native filesystem notifications update affected paths while running. Cache writes are excluded from watchers; ordinary file changes do not schedule full rescans. SQLite WAL supports independent reader connections; process locks coordinate scan generations across MCP processes sharing a database.

Old caches migrate transactionally to the compact schema and are vacuumed once to reclaim the old FTS pages. Stop older server versions before upgrading a shared cache. This one-time migration may delay opening a large existing cache; first-run incremental indexing applies to new caches and later filesystem scans.

This is an independent implementation informed by observed behavior and documented filesystem techniques. It is not recovered Everything source and does not claim identical performance. The default cold scan visits directories and stats files; it is slower than Everything's specialized NTFS metadata indexing. Experimental read-only NTFS enumeration cannot yet replace that path faithfully: privileged volume access was denied locally, metadata enrichment is needed, hardlink aliases can be missing, and durable USN-journal resume is not implemented.

## Verification

```powershell
.venv\Scripts\pytest.exe -q
.venv\Scripts\ruff.exe check src tests scripts
.venv\Scripts\ruff.exe format --check src tests scripts
.venv\Scripts\python.exe tests\live_smoke.py
.venv\Scripts\python.exe scripts\benchmark_index.py --entries 1000000
```

The stdio smoke starts the real MCP process and searches an isolated fixture without Everything. The benchmark inserts synthetic rows to measure search, not filesystem indexing throughput. See `docs/VALIDATION.md` for actual results and limitations.

MIT licensed; see [LICENSE](LICENSE).
