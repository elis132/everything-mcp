# Changelog

All notable changes to **everything-mcp** will be documented in this file.

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
