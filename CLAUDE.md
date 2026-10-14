# Developer guidance

This branch implements independent local file search over MCP. Explicit user direction supersedes the old es.exe design. Production code must not launch es.exe, connect to Everything, load its SDK or require its service.

- `index.py`: incremental scans, watchers, process locks and bounded search fallback.
- `storage.py`: normalized directory/filename metadata, generated columns and transactional legacy-cache migration.
- `names.py`: packed UTF-8 filename blocks, cross-process revision invalidation and partial search pages.
- `query.py`: supported Everything-style grammar compiled to parameterized SQL; reject unsupported functions.
- `ntfs.py`: experimental read-only metadata enumeration; not enabled as the authoritative crawler.
- `backend.py`: compatibility models and async adapter.
- `config.py`: explicit roots/cache environment configuration.
- `server.py`: MCP tools and lifecycle; diagnostics only on stderr.

Use `uv pip install -e '.[dev]'` in the project's virtual environment. Run pytest, Ruff check/format, and `python tests/live_smoke.py`. The latter exercises an actual stdio client/server pair. `scripts/benchmark_index.py` measures synthetic database searches, not real disk indexing.

Keep ordinary watcher updates scoped to affected paths. Exclude the cache and lock files. Preserve cached rows for inaccessible subtrees. Coordinate all processes sharing a cache during full scan generations. Never claim Everything-level performance from synthetic tests.

Publish first-run scan batches immediately and serve available rows while building. Preserve partial-result metadata through the backend and MCP output. Missing roots, inaccessible subtrees and unindexable filesystem names must mark coverage incomplete while preserving cached entries where safe. A bounded fallback's sort/pagination covers its subset; label it explicitly. Packed filename memory measurements exclude the rest of process RSS. Counts are exact for the current index snapshot or report an error; surface incomplete snapshot coverage in statistics freshness notes.

Do not add elevation, volume mutation, external ports or background Windows services implicitly. Directory traversal works without administrator rights; access-denied native reads must remain a normal fallback condition.
