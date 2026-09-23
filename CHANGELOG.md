# Changelog

All notable changes to **everything-mcp** will be documented in this file.

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
