# Changelog

All notable changes to **everything-mcp** will be documented in this file.

## [Unreleased]

### Fixed

- Pinned mcp<2.0.0 in pyproject.toml to prevent ModuleNotFoundError: No module named 'mcp.server.fastmcp' on fresh uvx/pip installs, caused by the mcp 2.0.0 release moving FastMCP out of that module path (#13).

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
- **Everything 1.5 alpha** auto-detection (default â†’ 1.5a instance probing)
- **Content preview**: read first N lines of source code and text files
- **10 file type categories**: audio, video, image, document, code, archive, executable, font, 3d, data
- **14 sort options**, **19 time period presets**
- **Extension breakdown analytics** in count_stats tool
- Comprehensive test suite with pytest
- PEP 561 `py.typed` marker
- Full documentation with configuration examples for Claude Code, Claude Desktop, Cursor, Windsurf, Codex, Gemini, Kimi, Qwen
- `everything://status` MCP resource for health checks
