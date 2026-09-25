---
name: everything-search
description: Find files and folders on Windows instantly using the Everything MCP tools (everything_search, everything_find_recent, everything_search_by_type, everything_file_details, everything_count_stats). Use whenever the user asks to locate, list, count, or size files anywhere on a Windows machine - dramatically faster than dir, Get-ChildItem, glob, or recursive directory walks.
---

# Everything File Search

The `everything_*` MCP tools query voidtools Everything's real-time NTFS index.
A search over millions of files returns in milliseconds, so prefer these tools
over shell commands (`dir /s`, `Get-ChildItem -Recurse`, glob) for ANY
filename-based lookup outside the current project directory.

## Picking the right tool

| Task | Tool |
|---|---|
| Find files/folders by name, extension, size, date | `everything_search` |
| "What changed in the last hour/day/week?" | `everything_find_recent` |
| All videos / documents / code / archives somewhere | `everything_search_by_type` |
| Metadata or first N lines of specific files | `everything_file_details` |
| "How many?" / "How much disk space?" | `everything_count_stats` |

Use `everything_count_stats` BEFORE listing when a query might match
thousands of files - check the scope first, then narrow.

## Query syntax essentials

Space between terms = AND. Key operators:

```
report.pdf                    name contains "report.pdf"
*.py                          extension wildcard
ext:py;js;ts                  multiple extensions
path:C:\Projects ext:py       restrict to a directory tree
size:>10mb  size:1kb..1mb     size filters
dm:today  dm:last7days        modified date (plural units: last2hours)
path:X dc:2024                created date (slow unless indexed: scope it)
"exact name.txt"              phrase with spaces (quote it)
a | b                         OR
!node_modules                 exclude
folder:                       folders only
file:                         files only
dupe:                         duplicate names
empty:                        empty folders
regex:^test_.*\.py$           regex (or pass match_regex=true)
```

## Patterns that work well

- Locate a project someone mentioned: `everything_search(query="folder: myproject")`
- Find a config file of unknown location: `everything_search(query="wg0.conf | wireguard ext:conf")`
- Recently downloaded file: `everything_find_recent(period="1hour", path="C:\\Users\\<user>\\Downloads")`
- Disk usage of build artifacts: `everything_count_stats(query="path:C:\\Projects node_modules folder:", include_size=true)`
- Then inspect what you found: `everything_file_details(paths=[...], preview_lines=30)`

## Pitfalls

- Paths with spaces must be quoted INSIDE the query: `path:"C:\My Documents"`.
- Results reflect the index, not content. `content:`, `width:`/`height:`,
  music tags and (unless indexed) `dc:`/`da:`/`attrib:` read files from disk
  and block Everything while they run, so the server only allows them after
  `path:`/`ext:`/name terms that narrow the candidates (1,000 files and 100 MB
  for content, 30,000 for dates). To search inside many files, find candidates
  by name first, then grep them.
- If a tool says Everything is busy, do not retry in a loop: wait, or ask the
  user to restart Everything. Never kill Everything yourself.
- Everything must be running; if tools return connection errors, tell the user
  to start Everything (system tray). Do not suggest setting
  `EVERYTHING_INSTANCE` unless they configured a named instance.
- Search is across ALL indexed drives by default - add `path:` to scope, and
  prefer `max_results`/`offset` paging over huge listings.
