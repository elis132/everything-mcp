# Everything MCP — Developer Guide

## Overview

MCP server for voidtools Everything file search. Provides 5 tools for AI agents
to search files at lightning speed via Everything's real-time NTFS index.

## Project Structure

```
src/everything_mcp/
├── __init__.py     # Package init, version, main() entry
├── __main__.py     # python -m everything_mcp
├── py.typed        # PEP 561 type checking marker
├── server.py       # MCP server, tool definitions (the main file)
├── backend.py      # Everything communication via es.exe
└── config.py       # Auto-detection of Everything installation

tests/
├── conftest.py     # Shared fixtures
├── test_backend.py # Backend + query builders + parsing
├── test_config.py  # Auto-detection logic
└── test_server.py  # Tool functions + output formatting
```

## Key Design Decisions

- **es.exe CLI** (not SDK DLL) — maximum compatibility across versions
- **No metadata flags** (`-size -dm -dc`) — clean path-per-line output + `os.stat()` enrichment is simpler and more reliable than parsing version-dependent columnar output
- **5 tools** (not 1, not 16) — balanced for AI agent efficiency
- **All tools read-only + idempotent** — annotated for MCP clients
- **Every tool wrapped in try/except** — returns error strings, never raw exceptions
- **Async subprocess** — non-blocking for MCP server event loop
- **`from __future__ import annotations`** — full PEP 604 union syntax on Python 3.10+

## Running

```bash
pip install -e ".[dev]"
everything-mcp
```

## Testing

```bash
pytest                  # all tests
pytest -v               # verbose
pytest tests/test_backend.py  # single module
pytest -x               # stop on first failure
```

## Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Adding a New Tool

1. Define a Pydantic input model in `server.py` (with `ConfigDict`)
2. Add `@mcp.tool` decorator with MCP annotations
3. Implement async function using `_get_backend()`
4. Wrap body in `try/except`, return error strings on failure
5. Add tests in `tests/test_server.py`
6. Document in `README.md`
