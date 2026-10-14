> Historical baseline before compact filename storage. Superseded by [current validation](VALIDATION.md).

# Local validation

Validated on 2026-10-12. This checkout is an unreleased independent implementation, not the package currently published to PyPI.

## Runtime evidence

- Full suite: **140 passed, 3 platform-specific skips** on both Windows and Linux/WSL. Ruff checks and formatting pass; `git diff --check` passes.
- Windows: CPython 3.14.2, SQLite 3.50.4, MCP SDK 1.30.0.
- Linux: Ubuntu under WSL, CPython 3.12.3. Linux tests and the real stdio MCP smoke passed; this is not a separate physical Linux host.
- macOS ARM: **142 passed, 1 skipped**, actual SDK stdio smoke and Ruff passed over SSH on an Apple M1 Mac mini with 8 GB RAM, macOS 26.2, Homebrew CPython 3.14.3 and SQLite 3.51.3. [Runtime details](runtime-macos-arm64.json).
- macOS Intel: **142 passed, 1 skipped**, actual SDK stdio smoke and Ruff passed on an Intel Core i5-1038NG7 MacBook Pro with 16 GB RAM, macOS 26.6.2, CPython 3.13.2 and SQLite 3.45.3. [Runtime details](runtime-macos-intel.json).
- Intel installation exposed an upstream compatibility limit: cryptography 49 removed Intel macOS support. A platform-specific `<49` dependency constraint now selects 48.0.1 on Intel; Apple silicon uses 50.0.2. See the [upstream changelog](https://cryptography.io/en/49.0.0/changelog/). Future maintenance of this older dependency series is a limit on long-term Intel support.
- Actual SDK stdio clients launched the server, listed all six tools, searched a fixture and queried index status on Windows, Linux and macOS. No Everything installation or process was used by the implementation.
- All 12 previously observed original-runtime fixture query counts match the independent engine; see [query-parity.json](query-parity.json). This verifies that fixture, not the entire Everything query language.
- The source distribution and wheel build successfully with `uv build`.
- Supplied original binary hashes were rechecked after REA analysis and remain unchanged.

## Million-row search benchmark

The database contains 1,000,000 synthetic filename/metadata rows. Each measured search requests at most 50 results, five repetitions, with its actual search query plan captured. This measures cached search, not filesystem discovery, end-to-end MCP transport, privileged NTFS enumeration or original Everything performance.

| Query | Sort | Median ms | Maximum ms |
|---|---|---:|---:|
| `invoice` | name | 22.476 | 62.318 |
| `invoice ext:pdf` | name | 22.302 | 23.644 |
| `ext:pdf` | name | 1.992 | 2.273 |
| `document-0999999` | name | 12.256 | 198.089 |
| `missing-needle` | name | 1.490 | 1.619 |
| `in` | name | 297.032 | 314.363 |
| `invoice` | modified descending | 23.544 | 24.005 |
| `in` | modified descending | 299.758 | 308.741 |

Raw results and query plans: [benchmark-windows.json](benchmark-windows.json).

The Apple M1 Mac mini also completed the million-row benchmark. Synthetic insertion took **523.435 seconds** and its cache occupied **4,195,959,784 bytes**. Median `invoice` search was **4.985 ms**, `invoice ext:pdf` **4.914 ms**, `ext:pdf` **0.460 ms**, exact `document-0999999` **4.549 ms**, and a missing term **0.273 ms**. The first `invoice` search reached **257.702 ms**, so the median does not describe cold-query latency. Both aggregate count checks passed: all rows in **3643.787 ms**, invoice rows in **173.147 ms**. However, **the two-character `in` query exceeded the five-second deadline**, for both name and modified-date ordering. This remains a measured performance limitation on that machine; trigram acceleration requires at least three characters and short terms can scan a large covering index. The benchmark removed its temporary database afterward. See [raw M1 results and plans](benchmark-macos-arm64.json).

The first benchmark exposed a real problem: broad short-name search hit the five-second deadline, and extension results incurred a large sort. Covering indexes and a bounded row-id selection now avoid loading full metadata rows before pagination. Trigram queries retain candidate-driven row-id lookups. These fixes were verified against the same million-row database.

The synthetic cache occupied **3,544,705,224 bytes**, including SQLite sidecars when measured. It contains long absolute temporary paths and several indexes. This is a substantial disk-space cost; it is not an Everything-like compact in-memory index. Initial synthetic insertion took roughly thirteen minutes while other work ran, and the original benchmark aborted on the short-query timeout before saving its exact insertion timer. The final run reused the completed database, so `insert_seconds` is intentionally null. No filesystem-indexing throughput is claimed.

The old Windows synthetic benchmark directory was subsequently verified absent. It is not used by the running MCP.

## Correctness coverage and limits

Tests cover persistent cache reopen/reconciliation, real watcher create/rename/delete updates, cache exclusion, ordinary directory-mtime events, atomic directory replacement, file/directory transitions, inaccessible-path retention, bounded regex/SQL deadlines, pagination, FTS and metadata query plans, quoted operators, OR precedence, Windows path separators, two-client generation locking, stale-cache labels and non-regular preview rejection.

Physical root/cache paths are resolved to match watcher notifications through macOS path aliases. Live APFS tests cover case-only file and directory renames: macOS reports both spellings as added, so the watcher resolves the current spelling with metadata-only `F_GETPATH` and removes the stale spelling. The scan count stays at one. FTS count and size aggregates use candidate row-id lookups, avoiding an expensive root scan chosen by newer SQLite planners.

For SSH discovery, a real local MCP client indexed only the Windows `.ssh` directory (13 entries), then found `config` and `known_hosts` in a 21.038 ms search round trip; process startup and initial readiness took 1.524 seconds. This is a scoped discovery measurement, not a whole-home benchmark.

On the Intel MacBook, the same actual MCP client indexed its seven `.ssh` entries and found `authorized_keys` in a **7.029 ms** round trip after **915.814 ms** to launch and become ready. The Intel synthetic benchmark used **100,000 rows**, inserted in **61.162 seconds**: name search `invoice` took **2.156 ms** median, `ext:pdf` **0.932 ms**, and the short term `in` **26.017 ms**. Both count checks passed. Its cache occupied 475,056,152 bytes and was removed by the benchmark's temporary-directory cleanup. See [raw results](benchmark-macos-intel.json). These different row counts do not constitute a direct comparison against the Windows million-row run.

Startup reconciliation still visits configured directories to catch offline changes. It does not replay a durable OS journal checkpoint. Cached queries remain available during reconciliation; watcher events update affected paths afterward. Permission failures and watcher failures are exposed through status and freshness notes. Notification availability and filesystem permissions still affect freshness.

The experimental NTFS helper has binary-parser tests and observed access-denied fallback only. It is not the default crawler, has incomplete hardlink/metadata coverage, and has no successful privileged-volume performance validation. See [RESEARCH.md](RESEARCH.md).

On POSIX systems without a file birth time, creation dates remain unknown rather than using metadata-change time. Creation-date sorting is rejected there.

The earlier C# prototype's repeated full-scan bug was separately fixed and its distribution rebuilt. Its 50-check harness passed. That GUI is not part of this MCP implementation.
