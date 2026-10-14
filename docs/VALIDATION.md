# Compact index validation

Validated on 2026-10-12. This is the unreleased independent implementation in this checkout. It replaces the filename/path FTS design measured in the [historical baseline](VALIDATION-fts-baseline.md).

## Runtime verification

| Platform | Runtime | Full suite |
|---|---|---|
| Windows | CPython 3.14.2, SQLite 3.50.4 | 153 passed, 3 skipped |
| Ubuntu / WSL | CPython 3.12.3 | 153 passed, 3 skipped |
| Apple M1 mini, 8 GB, macOS 26.2 | CPython 3.14.3, SQLite 3.51.3 | 155 passed, 1 skipped |
| Intel i5 MacBook Pro, 16 GB, macOS 26.6.2 | CPython 3.13.2, SQLite 3.45.3 | 155 passed, 1 skipped |

Both Macs were tested over SSH on the user's physical machines. Linux was tested under WSL, not on a separate physical host. [M1 runtime](runtime-macos-arm64.json), [Intel runtime](runtime-macos-intel.json).

Actual MCP SDK stdio clients listed all six tools and searched isolated fixtures on all four platforms. The full suite includes a real MCP first-run test: it deliberately pauses the scanner after its first file, retrieves that file with a partial-coverage notice while the state is still building, resumes the scan, and then verifies complete results. Ruff check/format, lock validation, whitespace checks and the wheel/source build pass.

Intel macOS uses the platform-specific cryptography <49 constraint because [upstream removed Intel support](https://cryptography.io/en/49.0.0/changelog/). Both Macs use the same application source. Future maintenance of that older dependency series remains a limit on long-term Intel support.

## Million-entry compact benchmark

Each database contains 1,000,000 synthetic metadata rows with 100 shared parent directories. Queries return at most 50 results and run five times. These timings exclude filesystem crawling and MCP transport. They measure the current packed filename/SQLite engine, not original Everything. Row paths and host load differ, so the table is not a controlled hardware comparison.

| Measurement | Windows | M1 mini | Intel MacBook |
|---|---:|---:|---:|
| Database MB, including measured sidecars | 206.65 | 202.49 | 202.49 |
| Packed filename buffers MB | 33.00 | 33.00 | 33.00 |
| `o`, name sort, median ms | 2.519 | 0.851 | 1.955 |
| `o`, first query ms | 5.174 | 3.559 | 5.246 |
| `in`, name sort, median ms | 14.923 | 2.821 | 7.149 |
| `in`, modified descending, median ms | 13.471 | 3.275 | 8.674 |
| `invoice`, name sort, median ms | 12.026 | 2.900 | 5.739 |
| `ext:pdf`, name sort, median ms | 2.489 | 0.800 | 1.661 |
| First 5,000 rows searchable after seconds | 0.489 | 0.141 | 0.410 |
| Query against that first batch, ms | 7.087 | 1.068 | 2.353 |
| Complete synthetic insertion, seconds | 100.320 | 38.182 | 102.894 |

Every benchmark query trial returned complete sorted results without fallback on all three machines, including `o`. Exact aggregate checks passed for all entries, `invoice`, and `in`. The first full-index invoice query took 22.503 ms on Windows, 66.450 ms on M1 and 99.288 ms on Intel; medians do not describe cold latency. These are first-query measurements after insertion, not OS-cache-cold or fresh-process startup tests.

Raw reports include first-query times, candidate-generation timings, both adaptive query plans, partial flags, memory bytes and count checks: [Windows](benchmark-tuned-windows.json), [M1](benchmark-tuned-macos-arm64.json), [Intel](benchmark-tuned-macos-intel.json). The previous compact reports remain as the pre-tuning baseline: [Windows](benchmark-compact-windows.json), [M1](benchmark-compact-macos-arm64.json), [Intel](benchmark-compact-macos-intel.json).

Before tuning, `o` medians were 2,317.059 / 1,481.927 / 2,468.997 ms respectively, with a partial trial on Windows. The new adaptive name-order plan avoids materializing and sorting a million candidate IDs. A small revision-keyed candidate cache reduces repeated sparse-query scans; all reports retain first times so the cache's benefit is visible rather than conflated with uncached performance. In this fixture the candidate cache payload is 20,698 bytes per host, separate from packed buffers and cache-key/object overhead.

The earlier Windows first-query spike of 157.493 ms did not reproduce in the two subsequent million-row runs (22.350 and 22.503 ms). This is not proof that cold disk latency is fixed. Profiling attributes 12.210 ms of M1's 66.450 ms first invoice query to filename candidate generation, leaving most of the first-query delay outside the packed-name scan. Packed names are eagerly warmed ordinary bytes, not mmap allocations; applying madvise to those buffers would not address that remaining delay. The first-query SQL/page path, including the Intel 99.288 ms trial, remains a tuning target.

The M1 database fell from 4,195,959,784 to 202,493,953 bytes: about 95% smaller. Windows fell from 3,544,705,224 to 206,647,297 bytes. This is roughly 202-207 bytes per entry for this fixture. Directory deduplication benefits shared paths; real directory shapes, filename lengths and metadata vary. The 33 MB figure counts packed name bytes and offset/ID arrays, not total Python process RSS, temporary candidate lists or SQLite buffers.

## Bounded searches and partial coverage

One- and two-character terms use the same substring semantics as longer terms. `in` matches adjacent letters anywhere in a filename, including invoice, printing and minfil. Native bytes.find scans packed folded UTF-8 names; only matching row IDs feed exact SQL checks and global ordering. There is no filename FTS index and no prefix-only restriction. File contents are not indexed.

Single-letter filename queries sorted by name collect at most 4,097 candidate IDs. Up to 4,096 matches use candidate row lookups; broader terms walk the existing filename B-tree and stop at the requested page, preserving global path ties and ascending/descending pagination. This adaptive path reserves at most 250 ms for the primary query. Sparse letters such as `i` keep their fast candidate plan. A 16-entry LRU caches small candidate JSON lists (at most 64 KiB each) and broad-term markers; keys contain block IDs and transactional revisions. It retains neither large posting lists nor old filename buffers. Tests cover both plans, ties across 4,200 directories, quoted/case-folded letters, pagination, cache reuse across filters and invalidation after writes.

General sorted searches use 65% of the request's SQL budget. If the primary query cannot complete, the remaining budget runs a fallback over at most 50,000 index rows with a hard result limit. Its page explicitly labels partial coverage and that sorting/pagination apply within the subset. Compound single-letter queries, regex, path searches and non-name sort orders retain this general path. Counts and size aggregates remain exact-or-error and do not use the adaptive search cap.

First-run scans publish the first entry immediately, then batches of 256 entries or approximately 100 ms of traversal. The backend serves available rows even when no first batch exists yet. MCP output reports the searchable count and partial coverage while building. Missing roots, inaccessible subtrees and skipped names that cannot be represented as UTF-8 also mark coverage incomplete; prior cached entries under unavailable paths are retained. Searches include the coverage note and statistics include a freshness note. The synthetic first-batch timings above are not guarantees for real filesystem discovery latency.

## Persistence and correctness

Directory paths are normalized into shared location rows; filenames and metadata occupy compact file rows. Folded values and full paths use generated columns/views. Immutable packed name blocks use transactional SQLite revisions, so another MCP process's insertion, deletion or rename invalidates the correct block. Index batches warm changed blocks before continuing, avoiding a full filename load at the first search after a build.

Tests cover Unicode matching, grammar precedence, filters, globally stable complete pagination, bounded fallback labels, first-run MCP availability, cross-process cache invalidation, legacy-cache migration, persistent reopen/reconciliation, cache exclusion, scoped watcher updates, macOS case-only renames, physical path aliases, inaccessible-subtree retention, safe previews and bounded regex evaluation. All 12 previously observed original-runtime fixture counts were rechecked against the compact engine and match: [query parity](query-parity.json). This does not establish complete Everything query-language parity.

Old caches migrate transactionally, preserving IDs, metadata and scan generations; a one-time VACUUM reclaims obsolete FTS/index pages. Stop older server versions before upgrading a shared cache. Opening a large legacy cache can be delayed by that migration. Normal new-cache indexing and later reconciliations publish partial results as described above.

Startup still traverses configured roots to reconcile offline changes. Ordinary watcher updates affect only changed paths; they do not trigger repeated full scans. There is no durable OS-journal checkpoint replay. Watcher and permission failures are exposed through status, search coverage notes and statistics freshness notes.

The experimental read-only NTFS helper remains disabled by default: parser fixtures pass, but privileged volume reads were denied, metadata/hardlink coverage is incomplete and journal replay is unimplemented. See [research provenance](RESEARCH.md). Creation dates remain unknown on POSIX systems without birth times. Supplied original executables were not modified.

Synthetic benchmark temporary databases are removed by their context managers. The old Windows baseline benchmark directory was also verified absent. No Everything installation, executable, DLL or service is used by this MCP.
