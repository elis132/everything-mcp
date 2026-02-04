<div align="center">
  <h1>⚡ Everything MCP</h1>
  <p>
    <strong>The definitive MCP server for <a href="https://www.voidtools.com/">voidtools Everything</a> — lightning-fast file search for AI agents.</strong>
  </p>
  <p>
    <a href="https://pypi.org/project/everything-mcp/"><img alt="PyPI" src="https://img.shields.io/pypi/v/everything-mcp"></a>
    <a href="https://pypi.org/project/everything-mcp/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/everything-mcp"></a>
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/elis132/everything-mcp"></a>
  </p>
  <p>Search millions of files in milliseconds. Built for <strong>Claude Code</strong>, <strong>Codex</strong>, <strong>Gemini</strong>, <strong>Kimi</strong>, <strong>Qwen</strong>, <strong>Cursor</strong>, <strong>Windsurf</strong>, and any MCP-compatible client.</p>
</div>

---

## Why This One?

| Feature | everything-mcp (this) | mamertofabian (271⭐) | essovius (0⭐) |
|---|---|---|---|
| **Tools** | 5 well-designed | 1 generic | 16 granular |
| **Auto-detection** | ✅ Finds Everything + es.exe automatically | ❌ Manual DLL path | ❌ Manual setup |
| **Everything 1.5** | ✅ Auto-detects instance | ❌ No support | ⚠️ Manual flag |
| **Content preview** | ✅ Read first N lines | ❌ | ❌ |
| **File type categories** | ✅ 10 categories | ❌ | ✅ |
| **Stats & counts** | ✅ Size stats, extension breakdown | ❌ | Partial |
| **Error handling** | ✅ All tools return clean errors | ❌ Raw exceptions | ❌ |
| **Test suite** | ✅ pytest | ❌ | ❌ |
| **Zero config** | ✅ Works out of the box | ❌ Need SDK DLL path | ❌ Need es.exe in PATH |

## Performance

Real benchmark from this machine (Windows, query: `everything.exe`):

- `everything-mcp` (Everything index via `es.exe`): **220.22 ms avg** (5 runs)
- Naive filesystem walk over `C:\`: **66,539.03 ms** (single run)
- Observed speedup: **~302x faster**

Reproduce locally (PowerShell):

```powershell
@'
import os
import subprocess
import time
import statistics

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

## Installation

### Prerequisites

1. **Windows** with [Everything](https://www.voidtools.com/) installed and **running**
2. **es.exe** (Everything command-line interface):
   - **Everything 1.5 alpha**: es.exe is included
   - **Everything 1.4**: Download from [github.com/voidtools/es](https://github.com/voidtools/es/releases)
   - Place `es.exe` in your PATH or in the Everything installation directory
3. **Python 3.10+** or **uv**

### Via uv (recommended — no install needed)

```bash
uvx everything-mcp
```

### Via pip

```bash
pip install everything-mcp
```

### From source

```bash
git clone https://github.com/elis132/everything-mcp.git
cd everything-mcp
pip install -e ".[dev]"
```

---

## Configuration

<details>
<summary>Shared MCP JSON Template</summary>

Use this server definition anywhere a client asks for MCP JSON:

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
</details>

<details>
<summary>Claude Code</summary>

Use the Claude Code CLI:

```bash
claude mcp add everything -- uvx everything-mcp
claude mcp list
```

Or add to `.claude/settings.json`:

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
</details>

<details>
<summary>Claude Desktop</summary>

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

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
</details>

<details>
<summary>Codex CLI</summary>

Use the Codex CLI:

```bash
codex mcp add everything -- uvx everything-mcp
codex mcp list
```
</details>

<details>
<summary>Gemini CLI</summary>

Use the Gemini CLI:

```bash
gemini mcp add -s user everything uvx everything-mcp
gemini mcp list
```
</details>

<details>
<summary>Kimi CLI</summary>

Use the Kimi CLI:

```bash
kimi mcp add --transport stdio everything -- uvx everything-mcp
kimi mcp list
```
</details>

<details>
<summary>Qwen CLI</summary>

Use the Qwen CLI:

```bash
qwen mcp add -s user everything uvx everything-mcp
qwen mcp list
```
</details>

<details>
<summary>Cursor</summary>

Cursor currently uses MCP settings/deeplinks rather than a stable `mcp add`
CLI command. Add the JSON config in Cursor's MCP settings UI.
</details>

<details>
<summary>Windsurf</summary>

Windsurf currently uses MCP settings rather than a stable `mcp add` CLI
command. On Windows, add the JSON config to:

`%USERPROFILE%\.codeium\windsurf\mcp_config.json`
</details>

<details>
<summary>Generic MCP Clients</summary>

Any MCP-compatible client can use this format:

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
</details>

<details>
<summary>Using pip Instead of uvx</summary>

```json
{
  "mcpServers": {
    "everything": {
      "command": "everything-mcp"
    }
  }
}
```

Or with explicit Python:

```json
{
  "mcpServers": {
    "everything": {
      "command": "python",
      "args": ["-m", "everything_mcp"]
    }
  }
}
```
</details>

---

## Environment Variables (Optional)

Everything MCP auto-detects your setup, but you can override:

| Variable | Description | Example |
|---|---|---|
| `EVERYTHING_ES_PATH` | Path to es.exe | `C:\Program Files\Everything\es.exe` |
| `EVERYTHING_INSTANCE` | Everything instance name | `1.5a` |

```json
{
  "mcpServers": {
    "everything": {
      "command": "uvx",
      "args": ["everything-mcp"],
      "env": {
        "EVERYTHING_INSTANCE": "1.5a"
      }
    }
  }
}
```

---

## Tools

### 1. `everything_search` — The Workhorse

Search files and folders using Everything's full query syntax.

| Parameter | Default | Description |
|---|---|---|
| `query` | *(required)* | Everything search query |
| `max_results` | 50 | 1–500 |
| `sort` | `date-modified-desc` | See sort options below |
| `match_case` | false | Case-sensitive |
| `match_whole_word` | false | Whole words only |
| `match_regex` | false | Regex mode |
| `match_path` | false | Match full path |
| `offset` | 0 | Pagination offset |

**Everything Search Syntax:**

```
*.py                          → All Python files
ext:py;js;ts                  → Multiple extensions
ext:py path:C:\Projects       → Python files in Projects
size:>10mb                    → Larger than 10 MB
size:1kb..1mb                 → Between 1 KB and 1 MB
dm:today                      → Modified today
dm:last1week                  → Modified in the last week
dc:2024                       → Created in 2024
"exact name.txt"              → Exact filename match
project1 | project2           → OR search
!node_modules                 → Exclude term
ext:py !test !__pycache__     → Python, excluding tests
content:TODO                  → Files containing TODO (requires content indexing)
regex:^test_.*\.py$           → Regex search
parent:src ext:py             → Python files in 'src' folders
dupe:                         → Duplicate filenames
empty:                        → Empty folders
```

### 2. `everything_search_by_type` — Category Search

Search by pre-defined file type categories.

**Categories:** `audio`, `video`, `image`, `document`, `code`, `archive`, `executable`, `font`, `3d`, `data`

| Parameter | Default | Description |
|---|---|---|
| `file_type` | *(required)* | Category name |
| `query` | `""` | Additional filter |
| `path` | `""` | Directory restriction |
| `max_results` | 50 | 1–500 |
| `sort` | `date-modified-desc` | Sort order |

### 3. `everything_find_recent` — What Changed?

Find recently modified files. Sorted newest-first.

**Periods:** `1min`, `5min`, `10min`, `15min`, `30min`, `1hour`, `2hours`, `6hours`, `12hours`, `today`, `yesterday`, `1day`, `3days`, `1week`, `2weeks`, `1month`, `3months`, `6months`, `1year`

| Parameter | Default | Description |
|---|---|---|
| `period` | `1hour` | Time period |
| `path` | `""` | Directory restriction |
| `extensions` | `""` | Extension filter (e.g. `py,js,ts`) |
| `query` | `""` | Additional filter |
| `max_results` | 50 | 1–500 |

### 4. `everything_file_details` — Deep Inspection

Get metadata and optional content preview for specific files.

| Parameter | Default | Description |
|---|---|---|
| `paths` | *(required)* | File paths to inspect (1–20) |
| `preview_lines` | 0 | Lines of text to preview (0–200) |

**Returns:** Full metadata (size, dates, permissions, hidden status). Directories: item count and listing. Text files with preview: first N lines of content.

### 5. `everything_count_stats` — Quick Analytics

Get count and size statistics without listing individual files.

| Parameter | Default | Description |
|---|---|---|
| `query` | *(required)* | Search query |
| `include_size` | true | Calculate total size |
| `breakdown_by_extension` | false | Sampled per-extension stats (files only) |

---

## Examples

> "Find all Python files modified today in my project"

→ `everything_find_recent(period="today", extensions="py", path="C:\Projects\myapp")`

> "How much disk space do my log files use?"

→ `everything_count_stats(query="ext:log", include_size=true, breakdown_by_extension=true)`

> "Show me the first 50 lines of that config file"

→ `everything_file_details(paths=["C:\Projects\app\config.yaml"], preview_lines=50)`

> "Find all duplicate filenames in Documents"

→ `everything_search(query='dupe: path:"C:\Users\me\Documents"')`

> "Find all images larger than 5MB"

→ `everything_search(query="ext:jpg;png;gif size:>5mb")`

---

## Troubleshooting

### "es.exe not found"

1. Ensure Everything is installed: https://www.voidtools.com/
2. Download es.exe: https://github.com/voidtools/es/releases
3. Place es.exe in your PATH or set `EVERYTHING_ES_PATH`

### "Everything IPC window not found"

1. Ensure Everything is **running** (check system tray)
2. If using Everything 1.5 alpha, set `EVERYTHING_INSTANCE=1.5a`
3. Ensure you're not running Everything Lite (no IPC support)

### "No results for valid queries"

1. Verify Everything's index is built (needs time on first run)
2. Try the same query in Everything's GUI
3. Check that the drive/path is included in Everything's index settings

### Debugging

```bash
# View server logs
everything-mcp 2>everything-mcp.log

# MCP Inspector
npx @modelcontextprotocol/inspector uvx everything-mcp
```

---

## Architecture

```
┌──────────────┐     MCP (stdio)     ┌──────────────────┐
│  AI Agent    │◄────────────────────►│  Everything MCP  │
│ (Claude,     │                      │  Server          │
│  Codex, etc) │                      │                  │
└──────────────┘                      │  5 Tools:        │
                                      │  • search        │
                                      │  • search_by_type│
                                      │  • find_recent   │
                                      │  • file_details  │
                                      │  • count_stats   │
                                      └────────┬─────────┘
                                               │ async subprocess
                                               ▼
                                      ┌──────────────────┐
                                      │     es.exe       │
                                      │  (CLI interface)  │
                                      └────────┬─────────┘
                                               │ IPC / Named Pipes
                                               ▼
                                      ┌──────────────────┐
                                      │   Everything     │
                                      │   Service        │
                                      │  (voidtools)     │
                                      │                  │
                                      │  Real-time NTFS  │
                                      │  file index      │
                                      └──────────────────┘
```

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/
```

## Contributing

Contributions welcome! Areas for improvement:

- Direct named pipe IPC (bypass es.exe for lower latency)
- Everything SDK3 integration for Everything 1.5
- Content search integration
- File watching / change notifications
- Bookmark and tag support (Everything 1.5)

## License

MIT — see [LICENSE](LICENSE)

## Acknowledgments

- [voidtools](https://www.voidtools.com/) for the incredible Everything search engine
- [Anthropic](https://anthropic.com/) for the Model Context Protocol specification
- The MCP community for driving adoption across AI tools
