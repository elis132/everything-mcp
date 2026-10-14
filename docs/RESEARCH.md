# Research provenance and boundaries

The user's supplied Everything binaries remain under `D:\Dev\Everything`; the implementation in this repository is independently authored Python and SQL. It is not a translation of recovered source.

REA/Ghidra 12.1.4 analysis of Everything 1.4.1.1032 x86, SHA-256 `d38f48dd60de6b10f9a132718ccbfddc171a7a7d1cc88ef2e4ce73c0c43dcd5f`, succeeded after allowing a one-hour startup budget. The initial successful search took about 375 seconds wall time (372 seconds reported command duration), exceeding the previous 330-second default.

Observed native evidence:

- Defined diagnostic string at `0x570048` describes `FSCTL_QUERY_USN_JOURNAL` failure.
- References occur at `0x4ac463`, `0x4ad231`, `0x4b2675`, `0x4b2727`.
- Four containing procedures were successfully decompiled: `FUN_004ac400`, `FUN_004ad1e0`, `FUN_004b25e0`, `FUN_004b26a0`.
- Their pseudocode calls `DeviceIoControl` with control code `0x900f4` and a 56-byte output buffer. This directly supports USN-journal querying; it is stronger evidence than an imported API name alone.
- `FUN_004ad1e0` additionally contains a journal-delete control path (`0x900f8`) and corresponding diagnostic. Its presence does not establish when ordinary indexing invokes it. Our implementation never invokes journal deletion or creation.
- The latter two routines retry query failures and branch on journal-related error codes; one recovered routine bounds retries at ten.

Raw provider results, limitations and subject hashes are stored locally in `D:\Dev\Everything\reconstruction\evidence\rea-long-search.json`, `extended-xrefs.json` and `decompile-0x*.json`. Pseudocode is provider output, not original C source; inferred types and unnamed functions remain uncertain. This analysis did not recover Everything's complete search algorithm, query optimizer, memory layout, durable indexing protocol or performance profile.

Runtime fixture queries against an isolated copy also confirmed OR-before-AND behavior, including `invoice | photo ext:jpg`. The new grammar retains that behavior. Earlier native imports/string inventory and fixture evidence are in the sibling reconstruction directory.

The practical architecture follows the established separation of initial indexing, cached name search and incremental filesystem changes. The initial SQLite filename/path FTS design was replaced after measurements exposed a large footprint and slow two-character queries. The current independently chosen design uses normalized directory metadata and packed UTF-8 filename buffers searched with native substring scans. It does not claim to reproduce Everything's internal structures. Official technical references:

- [Everything indexing](https://www.voidtools.com/support/everything/)
- [Microsoft change-journal operations](https://learn.microsoft.com/en-us/windows/win32/fileio/change-journal-operations)
- [SQLite generated columns](https://www.sqlite.org/gencol.html)
- [SQLite views](https://www.sqlite.org/lang_createview.html)
- [watchfiles filesystem watcher](https://watchfiles.helpmanual.io/)

The optional native helper performs read-only journal query and enumeration operations. Local volume reads returned access denied without elevation. Its fixture tests establish parser behavior and fallback, not successful privileged volume scanning. It does not yet provide complete hardlink aliases, native size/date fields, or durable journal replay, and is not enabled for the production crawler.
