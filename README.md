<div align="center">
  <h1>⚡ Everything MCP</h1>
  <p>
    <strong>MCP server for <a href="https://www.voidtools.com/">voidtools Everything</a> - search millions of Windows files in milliseconds from any AI agent.</strong>
  </p>
  <p>
    <a href="https://pypi.org/project/everything-mcp/"><img alt="PyPI" src="https://img.shields.io/pypi/v/everything-mcp.svg?cacheSeconds=300&v=20260204"></a>
    <a href="https://pypi.org/project/everything-mcp/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/everything-mcp.svg?cacheSeconds=300&v=20260204"></a>
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/elis132/everything-mcp.svg?cacheSeconds=300&v=20260204"></a>
  </p>
</div>

---

## Quick start

```
/plugin marketplace add elis132/everything-mcp
/plugin install everything-mcp@everything-mcp
```

That's it for Claude Code - the plugin bundles the MCP server and a skill that teaches the query syntax. For every other client, see [Installation](#installation) below.

## Why this one

|  | **everything-mcp** (this) | [mamertofabian](https://github.com/mamertofabian/mcp-everything-search) (342⭐) | [Josephur](https://github.com/Josephur/everything-mcp) (26⭐) | essovius |
|---|---|---|---|---|
| Tools | 5 | 1 | 1 | 16 |
| Setup | Auto-detects es.exe | Manual SDK DLL path | Manual HTTP server + host/port | Manual es.exe in PATH |
| Everything 1.5 | Auto-detects instance | Not supported | Untested | Manual flag |
| Talks to Everything via | `es.exe` subprocess | Everything SDK (DLL) | Everything's HTTP server plugin (unauthenticated) | `es.exe` subprocess |
| Tests / CI | pytest, GitHub Actions | None visible | None visible | None visible |

## Performance

`es.exe` (Everything's real-time NTFS index) vs. a naive filesystem walk, same query:

- **everything-mcp**: 220 ms avg (5 runs)
- **Naive walk of `C:\`**: 66,539 ms
- **~300x faster**

<details>
<summary>Reproduce this benchmark</summary>

```powershell
@'
import os, subprocess, time, statistics

ES = os.path.expandvars(r"%LOCALAPPDATA%\Everything\es.exe")
QUERY = "everything.exe"

es_runs = []
for _ in range(5):
    t0 = time.perf_counter()
    subprocess.run([ES, "-n", "100", QUERY], capture_output=True, text=True)
    es_runs.append((time.perf_counter() - t0) * 1000)

t0 = time.perf_counter()
matches = []
for dirpath, _, filenames in os.walk(r"C:\\"):
    for name in filenames:
        if name.lower() == QUERY:
            matches.append(os.path.join(dirpath, name))
walk_ms = (time.perf_counter() - t0) * 1000

es_avg = statistics.mean(es_runs)
print("ES avg ms:", round(es_avg, 2))
print("Walk ms:", round(walk_ms, 2))
print("Speedup x:", round(walk_ms / es_avg, 1))
print("Matches:", len(matches))
'@ | python -
```
</details>

---

## Installation

### Prerequisites

1. **Windows** with [Everything](https://www.voidtools.com/) installed and **running**
2. **es.exe** (Everything's command-line interface) - included with Everything 1.5 alpha, or install separately:
   - `winget install voidtools.Everything.Cli`
   - `scoop install everything-cli`
   - `choco install es`
   - or download from [github.com/voidtools/es](https://github.com/voidtools/es/releases) and place it in your PATH
3. **Python 3.10+** or **uv**

### Run the server

```bash
uvx everything-mcp          # recommended, no install needed
pip install everything-mcp  # or via pip
```

From source:

```bash
git clone https://github.com/elis132/everything-mcp.git
cd everything-mcp && pip install -e ".[dev]"
```

### Add it to your client

Every client below uses the same MCP server definition:

```json
{
  "mcpServers": {
    "everything": {
      "command": "uvx",
      "args": ["everything-mcp"]
    }
  }
}
```

| Client | How to add it |
|---|---|
| **Claude Code** | `/plugin install everything-mcp@everything-mcp` (see [Quick start](#quick-start)), or `claude mcp add everything -- uvx everything-mcp` |
| **Claude Desktop** | Paste the JSON above into `%APPDATA%\Claude\claude_desktop_config.json` |
| **Codex CLI** | `codex mcp add everything -- uvx everything-mcp` |
| **Gemini CLI** | `gemini mcp add -s user everything uvx everything-mcp` |
| **Kimi CLI** | `kimi mcp add --transport stdio everything -- uvx everything-mcp` |
| **Qwen CLI** | `qwen mcp add -s user everything uvx everything-mcp` |
| **Cursor** | Paste the JSON above into Cursor's MCP settings UI |
| **Windsurf** | Paste the JSON above into `%USERPROFILE%\.codeium\windsurf\mcp_config.json` |
| **Any other MCP client** | Use the JSON above verbatim |

<details>
<summary>Using pip instead of uvx</summary>

```json
{ "mcpServers": { "everything": { "command": "everything-mcp" } } }
```

Or with explicit Python: `{"command": "python", "args": ["-m", "everything_mcp"]}`
</details>

### Environment variables (optional)

Everything MCP auto-detects your setup, but you can override:

| Variable | Description | Example |
|---|---|---|
| `EVERYTHING_ES_PATH` | Path to es.exe | `C:\Program Files\Everything\es.exe` |
| `EVERYTHING_INSTANCE` | Named Everything instance | `1.5a` |
| `EVERYTHING_MAX_RESULTS_CAP` | Hard cap on results per search (default `1000`) | `200` |

> Only set `EVERYTHING_INSTANCE` if you explicitly configured a named instance
> in Everything (Tools → Options → General → Instance). Most installs -
> including most Everything 1.5 installs - run on the **default** instance;
> setting this unnecessarily breaks the connection. If in doubt, leave it out.

```json
{
  "mcpServers": {
    "everything": {
      "command": "uvx",
      "args": ["everything-mcp"],
      "env": { "EVERYTHING_INSTANCE": "1.5a" }
    }
  }
}
```

---

## Tools

### 1. `everything_search` - the workhorse

| Parameter | Default | Description |
|---|---|---|
| `query` | *(required)* | Everything search query |
| `max_results` | 50 | 1-500 |
| `sort` | `date-modified-desc` | name, path, size, date-modified, date-created, extension (+ `-desc` variants). date-created needs 'Index date created' in Everything |
| `match_case` / `match_whole_word` / `match_regex` / `match_path` | false | Match modifiers |
| `offset` | 0 | Pagination offset |

**Query syntax:**

```
*.py                          all Python files
ext:py;js;ts                  multiple extensions
ext:py path:C:\Projects       Python files under a path
size:>10mb                    larger than 10 MB
size:1kb..1mb                 between 1 KB and 1 MB
dm:today / dm:last7days       modified today / in the last 7 days
path:D:\Photos dc:2024        created in 2024 (slow function, see below)
"exact name.txt"              exact filename match
project1 | project2           OR search
!node_modules                 exclude a term
ext:py path:C:\proj content:TODO   files containing TODO (slow function, see below)
regex:^test_.*\.py$           regex search
parent:src ext:py             files directly inside 'src' folders
dupe:  /  empty:               duplicate filenames / empty folders
```

**Slow functions.** `content:`, `width:`/`height:`, music tags (`artist:`, `album:`, ...) and, unless
their property is indexed (Everything > Tools > Options > Indexes), `dc:`, `da:` and `attrib:` (and
`dm:`/`size:` if you turned their indexing off) make Everything read every candidate file from disk -
and Everything answers no other search until it is done. On Everything 1.4 the server therefore runs
these terms last and only when the rest of the query narrows the candidates enough (1,000 files and
100 MB for `content:`; 1,000 files for image and tag functions; 30,000 for dates, attributes and
sizes). Otherwise it refuses and reports the candidate count, so add `path:`, `ext:` or a name.

### 2. `everything_search_by_type` - category search

Categories: `audio`, `video`, `image`, `document`, `code`, `archive`, `executable`, `font`, `3d`, `data`

Parameters: `file_type` *(required)*, `query`, `path`, `max_results`, `sort`

### 3. `everything_find_recent` - what changed?

Periods: `1min` … `12hours`, `today`, `yesterday`, `1day` … `1year`

Parameters: `period` (default `1hour`), `path`, `extensions`, `query`, `max_results`

### 4. `everything_file_details` - deep inspection

Parameters: `paths` *(required, 1-20)*, `preview_lines` (0-200)

Returns full metadata; for directories, item count and listing; for text files with a preview, the first N lines.

### 5. `everything_count_stats` - quick analytics

Parameters: `query` *(required)*, `include_size` (default true), `breakdown_by_extension`

Count and size stats without listing every file - check scope before a big search.

---

## Examples

| Ask | Call |
|---|---|
| Python files modified today in my project | `everything_find_recent(period="today", extensions="py", path="C:\Projects\myapp")` |
| How much space do my log files use? | `everything_count_stats(query="ext:log", include_size=true, breakdown_by_extension=true)` |
| First 50 lines of a config file | `everything_file_details(paths=["C:\Projects\app\config.yaml"], preview_lines=50)` |
| Duplicate filenames in Documents | `everything_search(query='dupe: path:"C:\Users\me\Documents"')` |
| Images larger than 5MB | `everything_search(query="ext:jpg;png;gif size:>5mb")` |

---

## Troubleshooting

**"es.exe not found"** - Install [Everything](https://www.voidtools.com/) and [es.exe](https://github.com/voidtools/es/releases), or set `EVERYTHING_ES_PATH`.

**"Everything IPC window not found"** - Make sure Everything is running (check the system tray). If you set `EVERYTHING_INSTANCE`, try removing it - most installs don't need it. Everything Lite doesn't support IPC.

**No results for valid queries** - Confirm Everything's index has finished building, try the same query in Everything's GUI, and check the drive/path is included in Everything's index settings.

**Debugging:**

```bash
everything-mcp 2>everything-mcp.log                        # server logs
npx @modelcontextprotocol/inspector uvx everything-mcp      # MCP Inspector
```

---

## Development

```bash
pip install -e ".[dev]"   # install with dev dependencies
pytest                    # run tests
ruff check src/ tests/    # lint
```

Contributions welcome - see [CLAUDE.md](CLAUDE.md) for the architecture and design decisions. Areas of interest: direct named-pipe IPC, Everything SDK3 for 1.5, content search, file-watching, bookmark/tag support.

## License

MIT - see [LICENSE](LICENSE)

## Acknowledgments

[voidtools](https://www.voidtools.com/) for Everything, [Anthropic](https://anthropic.com/) for the Model Context Protocol, and the MCP community.

<!-- mcp-name: io.github.elis132/everything-mcp -->
