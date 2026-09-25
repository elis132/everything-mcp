# Changelog

All notable changes to **everything-mcp** will be documented in this file.

## [1.0.9] - 2026-09-25

Root cause of #18, reproduced on Everything 1.4.1.1032: Everything answers IPC queries one at a time, and a query that arrives while a slow one runs blocks Everything completely (its window included) until the slow one finishes. Killing es.exe does not cancel a query inside Everything. Slow queries come from functions that read files from disk, which the search tool's own `content:TODO ext:py` example encouraged; with `content:` first, Everything 1.4 reads every file on every drive.

### Fixed

- **Disk-reading functions no longer freeze Everything (#18).** On Everything 1.4, `content:` (and its ANSI/UTF variants), `width:`/`height:`/`dimensions:`/`bitdepth:`/`orientation:`, music tags, and `dc:`/`da:`/`attrib:` (or `dm:`/`size:` if their indexing is turned off) are moved after the cheap terms and only run when those terms narrow the candidates to 1,000 files (content, image and tag functions) or 30,000 (dates, attributes, sizes), and for `content:` to 100 MB. The candidate check uses the same match options (`match_path`, `match_regex`, ...) as the real query. Otherwise the call is refused with the candidate count or size. Unscoped use and combinations with `|`, `< >`, `( )` or `AND`/`OR`/`NOT` are refused, and a function after a `regex:` modifier (`regex:content:x`, or `match_regex` with `content:x`) counts as that function. Index settings are read from `Everything.ini` (next to Everything.exe for portable installs, otherwise `%APPDATA%\Everything`). Everything 1.5, which reorders terms by cost itself, is not restricted.
- **Timed-out queries no longer pile up (#18).** es.exe is kept running (and drained) after a timeout or client cancellation instead of being killed, because it exits exactly when Everything finishes the query. Until then calls fail fast with a "busy" message instead of queueing a query that would freeze Everything. If Everything restarts, the orphaned es.exe (which waits for its reply forever) is detected via Everything's IPC window and dropped. The server can still exit while such an es.exe waits.
- **Multiple sessions no longer freeze Everything together.** A machine-wide lock file coordinates everything-mcp's calls to Everything across all its processes (searches, counts and the startup probes), so one session's slow query makes other sessions report busy instead of queueing behind it. A session releasing the lock pauses briefly before taking it again so waiting sessions get a turn, and calls queued in one session share a single 15 s wait. Known limit: if a server exits while its query still runs inside Everything, the lock goes with it.
- **A valid `EVERYTHING_ES_PATH` was rejected while Everything was busy (#18).** es.exe is now identified with `es.exe -h`, which works without Everything. The old fallback checked `es.exe -version` for the word "everything", which it never prints.
- **The server stayed broken if Everything was busy or stopped at startup.** Detection is now retried on later tool calls and by the status resource (at most every 10 s). Startup probes that talk to Everything time out after 4 s, the fallback search probe is gone, a busy Everything gets a single probe per detection, and a busy (timed-out) `EVERYTHING_INSTANCE` is no longer swapped for another running instance. The status resource reports `busy` instead of `error` while Everything or another session is busy.
- **`everything_find_recent` returned nothing for `1min`, `1hour`, `1day`, `1week`, `1month` and `1year`.** Everything 1.4 only parses plural units after a number (`last1hours`); the singular forms silently match nothing. The README's `dm:last1week` example had the same problem.
- **Sorting by an unindexed property returned an arbitrary order** (date created, which Everything does not index by default; also size or date modified if their indexing is off). Such sorts are now refused with a hint to enable the index, and the default sort falls back to name when date modified is not indexed, so `everything_find_recent` keeps working.
- **Paths containing spaces never matched.** `path:"C:\Program Files\X"` (and the `path` parameter of `everything_search_by_type` / `everything_find_recent`) returned no results: the quotes were stripped and Python's argument quoting wrapped the term in new quotes that es.exe passes on literally, so Everything searched for a phrase. es.exe now gets a command line that keeps Everything's own quotes, verified against es.exe 1.1.0.38.
- **A query term starting with `-` or `/` ran as an es.exe option.** For example a term `-exit` quit Everything and `-export-csv <file>` wrote a file. Such terms are now quoted and searched as text. Queries are split into terms by es.exe's own rules (space, tab, CR and LF separate terms; `"""` is a literal quote), so the checks here always see exactly the arguments es.exe will run. es.exe also gets no handle to the MCP server's stdin.
- **`match_regex` combined with `match_path` passed `-p` as the regex**, because `-r` takes the next argument. `-r` now comes last, right before the query.
- **`everything_count_stats` hid errors** behind "Count not available (es.exe may not support -get-result-count)" and kept sending queries after a failure. Errors are now reported as is, and the first failure ends the call.
- The timeout message no longer suggests "increase timeout" (there is no such setting) and tells agents to ask the user to restart Everything instead of doing it themselves.

## [1.0.8] - 2026-09-23

### Fixed

- **es.exe processes could pile up when calls timed out or were cancelled.** A tool call cancelled by the MCP client (for example when the client's own timeout fired first) left its `es.exe` running, and a timed-out call killed `es.exe` without waiting for it to exit. Both paths now kill and reap the process (#18).
- **Parallel tool calls no longer queue up inside Everything.** Everything answers IPC queries one at a time, so concurrent `es.exe` calls only waited inside Everything, where a query the server had already given up on kept it busy. The server now runs one `es.exe` at a time (#18).

## [1.0.7] - 2026-09-14

### Fixed

- **Fresh installs crashed on startup with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`.** mcp 2.0 renamed `FastMCP` to `MCPServer` and moved it to `mcp.server.mcpserver`; since the dependency was declared as an unbounded `mcp>=1.0.0`, every new `uvx everything-mcp` / `pip install everything-mcp` resolved mcp 2.x and failed immediately. `server.py` now imports through a compat shim, so the server runs on both mcp 1.x and 2.x (#13, diagnosed by @ina6ra and @aispecialist-dev).

### Added

- CI now runs the full suite against both mcp 1.x and mcp 2.x, so an SDK rename fails in CI instead of in published installs.
- Tests asserting all 5 tools register through the real MCP SDK and keep their read-only annotations.

## [1.0.6] - 2026-07-02

### Added

- `EVERYTHING_MAX_RESULTS_CAP` environment variable to lower the hard cap on results per search (default `1000`), for token-budget control.

### Changed

- README simplified from 539 to ~273 lines: collapsed duplicate per-client MCP config blocks into one shared block plus a table, moved the benchmark script into a collapsed section, dropped the redundant architecture diagram, and refreshed the competitor comparison table.

## [1.0.5] - 2026-07-02

### Fixed

- Multi-term AND queries (e.g. `dm:today ext:md`) now return results: the query is split into separate es.exe arguments in `search()` (#2, contributed by @Zouxd2004) and in `count()` / `get_total_size()` so `everything_count_stats` works too (#4).
- `total_size` no longer overflows to `18446744073709551615` ("16384.0 PB"): the es.exe unsigned `-1` error sentinel is now reported as "Total size not available" (#4).
- A wrong `EVERYTHING_INSTANCE` no longer breaks the server: the connection falls back to instance auto-detection with a warning, error messages explain when the variable is actually needed, and the README no longer suggests setting `EVERYTHING_INSTANCE=1.5a` for all 1.5 users (#5).

### Added

- Claude Code plugin marketplace support: `/plugin marketplace add elis132/everything-mcp`, then `/plugin install everything-mcp@everything-mcp`.
- Bundled `everything-search` skill for Claude Code: query syntax reference, tool selection guidance, and common pitfalls.
- CI workflow (pytest on Ubuntu/Windows for Python 3.10/3.13, ruff check/format) and `.gitattributes` line-ending normalization.
- Release pipeline triggered by version tags: builds, creates the GitHub release, publishes to PyPI (trusted publishing), and publishes to the official MCP registry (`io.github.elis132/everything-mcp`).
- Manual live smoke test workflow that runs the backend against a real Everything instance on a Windows runner.
- Dependabot updates for GitHub Actions and pip.

## [1.0.4] - 2026-02-04

### Changed

- Updated README badge URLs with cache-busting query params to force fresh badge values on GitHub and PyPI.

## [1.0.3] - 2026-02-04

### Changed

- Replaced em-dash punctuation with ASCII hyphens (`-`) across docs and source text.

## [1.0.2] - 2026-02-04

### Changed

- Updated package metadata author to `elis132` (removed author email from PyPI metadata).
- Updated LICENSE copyright holder name to `elis132`.

## [1.0.1] - 2026-02-04

### Fixed

- Fixed `everything_count_stats` reporting `0` for `total_count` and `total_size` on some systems.
- Updated backend aggregate queries to avoid incompatible `es.exe` flag combinations (`-n 0` with `-get-result-count` / `-get-total-size`).
- Added backend tests to verify aggregate command construction.

## [1.0.0] - 2026-02-04

### Added

- **5 AI-optimised tools**: `everything_search`, `everything_search_by_type`, `everything_find_recent`, `everything_file_details`, `everything_count_stats`
- **Zero-config auto-detection**: finds es.exe via PATH, common install locations, and Windows Registry
- **Everything 1.5 alpha** auto-detection (default → 1.5a instance probing)
- **Content preview**: read first N lines of source code and text files
- **10 file type categories**: audio, video, image, document, code, archive, executable, font, 3d, data
- **14 sort options**, **19 time period presets**
- **Extension breakdown analytics** in count_stats tool
- Comprehensive test suite with pytest
- PEP 561 `py.typed` marker
- Full documentation with configuration examples for Claude Code, Claude Desktop, Cursor, Windsurf, Codex, Gemini, Kimi, Qwen
- `everything://status` MCP resource for health checks
