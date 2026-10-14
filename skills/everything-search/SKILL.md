---
name: everything-search
description: Search the configured local filesystem index through the independent everything_* MCP tools. Use for finding, listing, counting or sizing files in indexed roots on Windows, Linux or macOS.
---

# Independent local file search

Use `everything_index_status` to inspect roots, readiness and watcher health. Search covers only those roots and reflects the cache; cached results may be stale during startup reconciliation or when watching is unavailable.

While the first scan builds, search available entries immediately. Responses report searchable counts and partial coverage; retry later for a complete inventory. A budget-limited search also labels partial results: its ordering and pagination apply only to the inspected subset. Narrow the query before treating a miss as definitive or combining pages into a complete listing.

- `everything_search`: names, paths, extensions, sizes and modified dates.
- `everything_search_by_type`: category filtering.
- `everything_find_recent`: modified-time filtering.
- `everything_count_stats`: counts and aggregate sizes before large listings.
- `everything_file_details`: current metadata and optional preview for explicit paths.

Examples: `invoice !draft`, `ext:py;ts`, `path:"D:\Dev" ext:pdf`, `size:>10mb`, `dm:last7days`, `folder: project`, `regex:^test_.*\.py$`.

Quote paths with spaces inside the query. Spaces mean AND. OR binds before AND: `invoice | photo ext:jpg` means `(invoice OR photo) AND ext:jpg`. Paginate with max_results/offset.

Single- and two-character terms match adjacent letters anywhere in the filename: `in` finds `invoice`, `printing` and `minfil`. Everything does not need to be installed or running. Unsupported functions (including content, dupe and media metadata) return errors; use filename filters to find candidates, then a separate content-search tool. Broad path and regex queries can be slower. Never promise instant results across unindexed paths.
