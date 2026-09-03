# Claude Code Token Usage Tracker

Automatically records **every Claude Code interaction** across **every local project**,
organised by year / month / date / project, and serves a local dashboard for
exploring the data.

```
data/usage/2026/09/03/data-platform/prompts.jsonl
data/usage/2026/09/03/InventoryManagementSystem/prompts.jsonl
data/usage/2026/09/03/AccentHRP/prompts.jsonl
```

Runs on **Windows**, **Ubuntu/Linux** and **macOS**.

Everything is local. No network calls, no proxy, no credentials read or stored,
no changes to your projects.

---

## Quick start

Clone the repository, then follow the three lines for your platform. Nothing here
depends on where you put the project.

### Windows (PowerShell)

```powershell
cd path\to\Claude_Code
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt        # optional: only the test suite needs it
python scripts\install_hooks.py
python server.py
```

### Ubuntu / Linux

```bash
cd path/to/Claude_Code
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # optional: only the test suite needs it
python3 scripts/install_hooks.py
python3 server.py
```

### macOS

```bash
cd path/to/Claude_Code
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # optional: only the test suite needs it
python3 scripts/install_hooks.py
python3 server.py
```

The dashboard opens at **http://127.0.0.1:8765**. Tracking starts with your next
Claude Code session; add `--backfill` to the install command to import the
history already on your machine.

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Requirements](#2-requirements)
3. [Windows setup](#3-windows-setup)
4. [Ubuntu / Linux setup](#4-ubuntu--linux-setup)
5. [macOS setup](#5-macos-setup)
6. [Claude Code integration](#6-claude-code-integration)
7. [Installing the tracker](#7-installing-the-tracker)
8. [Starting the dashboard](#8-starting-the-dashboard)
9. [Using the dashboard](#9-using-the-dashboard)
10. [CLI commands](#10-cli-commands)
11. [Data directory structure](#11-data-directory-structure)
12. [Configuration](#12-configuration)
13. [Token tracking](#13-token-tracking)
14. [Troubleshooting](#14-troubleshooting)
15. [Updating the tracker](#15-updating-the-tracker)
16. [Uninstalling](#16-uninstalling)
17. [Privacy](#17-privacy)
18. [Known limitations](#18-known-limitations)

Appendix: [architecture](#appendix-a-architecture) · [JSON schema](#appendix-b-json-schema) ·
[API reference](#appendix-c-api-reference) · [reports](#appendix-d-reports) ·
[running the tests](#appendix-e-running-the-tests)

---

## 1. Project overview

You run `claude` inside any project, as you always have. After each response,
Claude Code fires a hook; the hook reads that session's own transcript, extracts
the turn that just completed together with its **actual API token counts**, and
appends one JSON line to a file organised by date and project.

```
cd <any project directory>
claude
   -> you send a prompt
   -> Claude Code responds
   -> Stop hook fires
        -> tracker reads the session transcript
        -> detects project (git root) and date (interaction timestamp)
        -> captures the prompt and the real token usage
        -> appends to data/usage/YYYY/MM/DD/<project>/prompts.jsonl
        -> updates the SQLite index
   -> dashboard at http://127.0.0.1:8765 shows it
```

**No per-project setup. No project list to maintain. No extra command per
prompt.** Install once, globally, and every project on the machine is tracked
automatically.

### What each hook event actually provides

The hook payloads below were **captured from a live Claude Code session**, not
assumed. This matters, because it determines the whole design:

| Field | `SessionStart` | `UserPromptSubmit` | `Stop` | `SessionEnd` |
|---|---|---|---|---|
| `session_id` | yes | yes | yes | yes |
| `transcript_path` | yes | yes | yes | yes |
| `cwd` | yes | yes | yes | yes |
| `hook_event_name` | yes | yes | yes | yes |
| `prompt` (text) | no | **yes** | no | no |
| `prompt_id` | no | yes | **yes** | yes |
| `permission_mode` | no | yes | yes | no |
| `last_assistant_message` | no | no | yes | no |
| `source` / `reason` | `source` | no | no | `reason` |
| **model** | **no** | **no** | **no** | **no** |
| **input / output / cache tokens** | **no** | **no** | **no** | **no** |
| **timestamp** | **no** | **no** | **no** | **no** |

**No hook event carries token counts, the model, or a timestamp.** All three do
exist in the session transcript at `transcript_path`, which the payload hands us.
So the hook is used purely as a *trigger*, and every number is read from the
transcript. See [section 13](#13-token-tracking).

---

## 2. Requirements

| | Required | Notes |
|---|---|---|
| **Python** | 3.9 minimum, **3.11+ recommended** | Standard library only - the tracker itself installs nothing |
| **Claude Code** | any version | The thing being tracked |
| **Git** | optional | Improves project detection; without it the working directory name is used |
| **Disk** | a few MB | One JSONL line per interaction, plus a rebuildable SQLite index |
| **Network** | none | The tracker makes no outbound connections of any kind |

**Python packages: none at runtime.** `requirements.txt` contains `pytest`, and
only the test suite needs it. The dashboard has no JavaScript dependencies
either - charts are hand-drawn inline SVG, so it works entirely offline.

### Tested Python versions

| Version | Status |
|---|---|
| 3.14 | Verified (development machine) |
| 3.11 – 3.13 | Supported |
| 3.9 – 3.10 | Supported (minimum) |
| 3.8 and older | Not supported |

### Platform support

| Platform | Status |
|---|---|
| Windows 10 / 11 | Developed and tested on Windows 11 Pro (10.0.26200) |
| Ubuntu / Debian / other Linux | Implemented; reviewed for Linux compatibility, not physically tested |
| macOS (Intel and Apple silicon) | Implemented; reviewed for macOS compatibility, not physically tested |

The platform-specific logic is confined to one module,
[`tracker/platform_utils.py`](tracker/platform_utils.py), and every branch in it
is exercised by the test suite on whatever machine the tests run on - see
[appendix E](#appendix-e-running-the-tests).

---

## 3. Windows setup

### Windows requirements

```
Python 3.11+          python.org installer, or the Microsoft Store
Git                   optional, improves project detection
Claude Code           installed and working
PowerShell            5.1 or PowerShell 7+ (CMD also works)
```

Check what you have:

```powershell
python --version
git --version
claude --version
```

If `python` opens the Microsoft Store instead of running, install Python from
python.org and tick **"Add python.exe to PATH"**.

### Installation

**1. Open PowerShell and go to the project directory**

```powershell
cd path\to\Claude_Code
```

**2. Create a virtual environment**

```powershell
python -m venv .venv
```

**3. Activate it**

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

CMD:

```cmd
.venv\Scripts\activate.bat
```

If PowerShell refuses with *"running scripts is disabled on this system"*, allow
signed local scripts for your user once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**4. Install the test dependencies (optional)**

```powershell
pip install -r requirements.txt
```

**5. Install the Claude Code hook**

```powershell
python scripts\install_hooks.py
```

or, equivalently:

```powershell
.\scripts\install_windows.ps1
```

**6. Start the dashboard**

```powershell
python server.py
```

or:

```powershell
.\start_dashboard.bat
.\start_dashboard.ps1
```

**7. Verify tracking**

Open a *new* Claude Code session in any project, send a prompt, then:

```powershell
python -m tracker.cli today
```

> Creating `.venv` is recommended but optional. When it exists, the installer
> bakes that interpreter's absolute path into the hook command, so tracking keeps
> working in shells where the venv is not activated.

---

## 4. Ubuntu / Linux setup

### Ubuntu requirements

```
Python 3.11+
python3-venv          Debian/Ubuntu ship venv separately
Git                   optional, improves project detection
Claude Code           installed and working
```

### Installation

```bash
sudo apt update
sudo apt install python3 python3-venv git

cd path/to/Claude_Code

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt        # optional: only the test suite needs it

python3 scripts/install_hooks.py
python3 server.py
```

The shell wrappers are equivalent, if you prefer them:

```bash
chmod +x scripts/install_linux.sh start_dashboard.sh   # only if the bits were lost
./scripts/install_linux.sh
./start_dashboard.sh
```

On Fedora/RHEL use `sudo dnf install python3 git`; on Arch,
`sudo pacman -S python git`. Nothing else changes.

### Verify tracking

```bash
python3 -m tracker.cli status
python3 -m tracker.cli today
```

### Headless machines and SSH

The dashboard tries to open a browser on start. Suppress it:

```bash
python3 server.py --no-browser
```

Because the server binds to loopback only, reach it over an SSH tunnel rather
than by changing the bind address:

```bash
ssh -L 8765:127.0.0.1:8765 you@your-machine
```

then open `http://127.0.0.1:8765` on your own machine.

---

## 5. macOS setup

### macOS requirements

```
Python 3.11+          system python3, Homebrew, or python.org
Git                   optional, improves project detection
Claude Code           installed and working
Terminal              or iTerm2
```

**Homebrew is not required.** The `python3` that comes with the Xcode Command
Line Tools works. Install those if `python3` is missing:

```bash
xcode-select --install
```

If you would rather use Homebrew:

```bash
brew install python git
```

### Installation

```bash
python3 --version

cd path/to/Claude_Code

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt        # optional: only the test suite needs it

python3 scripts/install_hooks.py
python3 server.py
```

Or with the shell wrappers:

```bash
chmod +x scripts/install_macos.sh start_dashboard.sh   # only if the bits were lost
./scripts/install_macos.sh
./start_dashboard.sh
```

### Verify tracking

```bash
python3 -m tracker.cli status
python3 -m tracker.cli today
```

macOS may ask whether Python should accept incoming network connections the
first time the dashboard starts. Either answer works: the server binds to
`127.0.0.1` only, so it is never reachable from another machine regardless.

---

## 6. Claude Code integration

The tracker registers two hooks in Claude Code's **user-level** settings file, so
they apply to every project on the machine.

| | Location |
|---|---|
| Config directory | `~/.claude` on Windows, Linux and macOS alike |
| Settings file | `~/.claude/settings.json` |
| Session transcripts | `~/.claude/projects/<slug>/<session-id>.jsonl` |
| Override | `CLAUDE_CONFIG_DIR` (honoured on all three platforms) |

Nothing in this project hardcodes a user directory. The location is resolved at
runtime by [`tracker/platform_utils.py`](tracker/platform_utils.py), which
expands `~` correctly per platform and honours `CLAUDE_CONFIG_DIR` if you have
moved the directory. Confirm what it resolved to:

```
python -m tracker.cli status          # Windows
python3 -m tracker.cli status         # Linux / macOS
```

### The hooks that are registered

| Event | Why |
|---|---|
| `Stop` | Fires when a turn's response finishes - the point at which the transcript holds that turn's real token usage. This is the main path. |
| `SessionEnd` | A safety net that sweeps up anything the last `Stop` missed (interrupted turn, a message flushed to disk late). |

Guarantees, each covered by a test:

- **Idempotent.** Running the installer repeatedly never creates a duplicate hook.
- **Additive.** Existing hooks on the same event are preserved.
- **Non-destructive.** Every unrelated setting is copied through untouched.
- **Backed up.** A timestamped `settings.backup-cctracker-*.json` is written before
  any change, and only when something actually changes.
- **Reversible.** Uninstall restores the file to its exact previous content.
- **Safe on bad input.** An unparseable `settings.json` is refused, not overwritten.
- **Never fatal.** If the tracker breaks, the hook still exits 0 and Claude Code
  carries on exactly as if it were not installed.

---

## 7. Installing the tracker

The Python installer is the real implementation on **all three platforms**. The
`.ps1` and `.sh` scripts do nothing but find an interpreter and run it, so there
is no logic that can differ between operating systems.

| Platform | Command |
|---|---|
| Windows | `python scripts\install_hooks.py` |
| Ubuntu/Linux | `python3 scripts/install_hooks.py` |
| macOS | `python3 scripts/install_hooks.py` |

Convenience wrappers, all equivalent:

| Platform | Wrapper |
|---|---|
| Windows | `.\scripts\install_windows.ps1` or `.\scripts\install_hooks.ps1` |
| Ubuntu/Linux | `./scripts/install_linux.sh` |
| macOS | `./scripts/install_macos.sh` |

### Options

```
--backfill            also import the Claude Code history already on this machine
--settings <path>     write to a specific settings.json
--python <path>       pin a specific interpreter into the hook command
--quiet               only report problems
```

Importing existing history is safe to re-run - already-recorded interactions are
skipped by their stable id:

```
python  scripts/install_hooks.py --backfill      # Windows
python3 scripts/install_hooks.py --backfill      # Linux / macOS
```

or later, at any time:

```
python -m tracker.cli backfill
```

### Check the result

```
python -m tracker.cli status
python -m tracker.hooks status
```

### Start tracking

There is no step here. Open any project and use Claude Code normally:

```
cd <any project>
claude
```

Hooks are read when a session starts, so restart Claude Code after installing.

---

## 8. Starting the dashboard

The portable command, identical everywhere:

```
python  server.py          # Windows
python3 server.py          # Linux / macOS
```

Or, as a module-style invocation from the project root, either of:

```
python -m tracker.cli status     # to check first
python server.py --no-browser    # to run without opening a browser
```

Convenience launchers:

| Platform | Start |
|---|---|
| Windows | `.\start_dashboard.bat` or `.\start_dashboard.ps1` |
| Ubuntu/Linux | `./start_dashboard.sh` |
| macOS | `./start_dashboard.sh` |

Then open **http://127.0.0.1:8765** (the launcher opens it for you).

### Options

```
--port 9000        listen on another port
--host 127.0.0.1   bind address; must be loopback, anything else is refused
--no-browser       do not open a browser window
--reindex          rebuild the SQLite index before serving
```

### Stopping the dashboard

Press **Ctrl+C** in the terminal running it. That is the same on all three
platforms. If it is running detached:

| Platform | Stop |
|---|---|
| Windows (PowerShell) | `Get-Process python \| Where-Object { $_.Path -like '*Claude_Code*' } \| Stop-Process` |
| Ubuntu/Linux, macOS | `pkill -f "server.py"` |

The server binds to `127.0.0.1` only and is **not reachable from the network**.
A non-loopback host in `config.json` or on `--host` is refused at startup rather
than silently honoured, because the dashboard serves your prompt text and has no
authentication.

---

## 9. Using the dashboard

### Summary cards

```
Total Prompts    Input Tokens    Output Tokens
Cache Tokens     Total Tokens    Projects
```

### Filters

```
Date From    Date To    Year    Month
Project      Model      Prompt Search
```

The project and model dropdowns are populated from the data, never hard-coded.

### Quick filters

```
Today    Yesterday    Last 7 Days    Last 30 Days
This Month    Previous Month    This Year    All Time
```

### Actions

```
Apply Filters    Reset Filters    Refresh    Export CSV    Export JSON
```

Exports respect the filters currently applied.

### Tables

**Project summary** - sortable on every column:

```
Project | Prompt Count | Input Tokens | Output Tokens | Cache Tokens | Total Tokens | Usage %
```

Click a project name to filter the whole dashboard by it.

**Prompt history** - sortable, paginated at 25/50/100/250 rows, with search terms
highlighted; click a prompt to expand it:

```
Timestamp | Project | Branch | Model | Prompt | Input | Output | Cache | Total
```

### Charts

- **Daily token usage** - line/area chart, switchable between Total / Input /
  Output / Cache, with a crosshair and a hover tooltip giving exact counts.
- **Token usage by project** - bar chart, switchable between Total / Input /
  Output; click a bar to filter by that project.

Both are hand-drawn inline SVG. There is no React, no charting library and no
CDN, so the dashboard works with no network connection at all.

### Other

- **Auto refresh** - off by default; 30s / 1min / 5min. The default can be set in
  `config.json` (`dashboard.auto_refresh_seconds`).
- **Light and dark themes**, following the OS with a manual toggle.

Prompt text is HTML-escaped before it is rendered, including inside search
highlighting, so a prompt containing markup can never execute in the page.

---

## 10. CLI commands

Identical on every platform; only the interpreter name differs (`python` on
Windows, `python3` on Linux and macOS).

```
python -m tracker.cli today
python -m tracker.cli yesterday
python -m tracker.cli month
python -m tracker.cli year
python -m tracker.cli project data-platform
python -m tracker.cli projects
python -m tracker.cli range --from 2026-09-01 --to 2026-09-03
python -m tracker.cli search ClickHouse
python -m tracker.cli report
python -m tracker.cli backfill
python -m tracker.cli reindex
python -m tracker.cli status
python -m tracker.cli json --project data-platform
```

On Linux and macOS:

```bash
python3 -m tracker.cli today
python3 -m tracker.cli yesterday
python3 -m tracker.cli month
python3 -m tracker.cli project data-platform
python3 -m tracker.cli report
```

| Command | What it does |
|---|---|
| `today` / `yesterday` / `month` / `year` | Usage for that period, with a per-project breakdown |
| `range --from --to` | Usage for an explicit date range |
| `project <name>` | One project, broken down by date and model |
| `projects` | Per-project table with percentage of usage |
| `search <text>` | Search stored prompt text |
| `report` | Write daily / monthly / yearly JSON reports |
| `backfill` | Import Claude Code history already on this machine |
| `reindex` | Rebuild the SQLite index from the JSONL files |
| `status` | Installation, platform and data health check |
| `json` | Machine-readable summary, for scripting |

Example:

```
Claude Code Usage - 03 Sep 2026

Prompts       : 124
  (human)     : 118
Input Tokens  : 523,421
Output Tokens : 121,231
Cache Tokens  : 51,231
Total Tokens  : 644,652

Projects:

  data-platform     312,421
  AccentHRP         201,231
  ReviewMint        131,000
```

---

## 11. Data directory structure

The year, month and day come from **each interaction's own timestamp**, converted
to local time (or to the configured timezone), so a day in the dashboard matches
your actual working day. No date is ever hard-coded, and no project name is ever
configured.

```
data/usage/
└── 2026/
    └── 09/
        └── 03/
            ├── data-platform/
            │   └── prompts.jsonl
            ├── AccentHRP/
            │   └── prompts.jsonl
            └── ReviewMint/
                └── prompts.jsonl
```

One interaction per line. Files are only ever appended to.

**This layout is byte-for-byte identical on Windows, Linux and macOS**, so a
`data/` directory can be copied between machines and read anywhere. Project names
are sanitised to characters that are valid on all three platforms, which is why a
tree written on Linux always opens cleanly on Windows.

### How project detection works

Fully automatic, derived from the session's working directory. **There is no
configured list of projects anywhere in this system.**

1. Ask git for the repository root and use that directory's name:
   ```
   git rev-parse --show-toplevel
   ```
2. If the directory is not a git repository, or git is not installed, use the
   working directory's own name.

```
D:\New_Projects\data-platform                    -> data-platform
/home/you/projects/InventoryManagementSystem     -> InventoryManagementSystem
/Users/you/Git/data-platform/services/api        -> data-platform   (git root wins)
```

Each interaction records `project`, `working_directory`, `git_repository` and
`git_branch` when they are available.

Resolutions are cached in `data/state/project_cache.json` (24h by default) so the
hook does not spawn a git process on every turn. On Windows the cache key is
case-folded, because Windows paths are case-insensitive; on Linux and macOS it is
not, because they are not.

To exclude a directory from tracking entirely, add a glob to `ignore_paths` in
`config.json` (write patterns with `/` on every platform):

```json
{ "ignore_paths": ["*/client-confidential", "D:/Scratch/*", "/home/me/scratch/*"] }
```

### Other directories

```
data/state/       per-session parse offsets and the project cache (rebuildable)
data/index.sqlite3  the query index (rebuildable: python -m tracker.cli reindex)
reports/          generated daily / monthly / yearly JSON (rebuildable)
logs/             tracker.log and server.log
```

---

## 12. Configuration

Everything lives in [`config.json`](config.json) at the project root. Every key is
optional: anything missing falls back to a built-in default, so a truncated or
hand-edited file can never stop the hook recording usage.

```json
{
  "store_prompt_text": true,
  "prompt_text_max_chars": 8000,
  "track_non_human_turns": true,
  "timezone": "local",

  "paths": {
    "data_dir": "data",
    "usage_dir": "data/usage",
    "state_dir": "data/state",
    "index_path": "data/index.sqlite3",
    "reports_dir": "reports",
    "logs_dir": "logs"
  },

  "server": { "host": "127.0.0.1", "port": 8765, "open_browser": true },
  "dashboard": { "auto_refresh_seconds": 0, "page_size": 25 },
  "project_detection": {
    "use_git_root": true,
    "cache_ttl_seconds": 86400,
    "git_timeout_seconds": 3.0
  },
  "ignore_paths": [],
  "hook_budget_seconds": 8.0
}
```

| Setting | Meaning |
|---|---|
| `store_prompt_text` | Store the prompt text. `false` keeps only its length and hash - see [privacy](#17-privacy) |
| `prompt_text_max_chars` | Truncate stored prompts at this length (`0` disables truncation) |
| `track_non_human_turns` | Record turns Claude Code started itself; they cost real tokens |
| `timezone` | Timezone used to bucket days - see below |
| `paths.data_dir` | Base directory for everything generated |
| `paths.usage_dir` | The JSONL tree (the source of truth) |
| `paths.state_dir` | Per-session parse offsets and the project cache |
| `paths.index_path` | The SQLite index |
| `paths.reports_dir` | Generated reports |
| `paths.logs_dir` | `tracker.log` and `server.log` |
| `server.host` | Dashboard bind address. **Must be loopback**; anything else is refused |
| `server.port` | Dashboard port |
| `server.open_browser` | Open a browser when the dashboard starts |
| `dashboard.auto_refresh_seconds` | Default auto-refresh: `0` (off), `30`, `60` or `300` |
| `dashboard.page_size` | Default rows per page: `25`, `50`, `100` or `250` |
| `project_detection.use_git_root` | Prefer the git repository root's name |
| `project_detection.cache_ttl_seconds` | How long a directory→project resolution is cached |
| `project_detection.git_timeout_seconds` | How long to wait for `git rev-parse` |
| `ignore_paths` | Glob patterns for directories to skip entirely |
| `hook_budget_seconds` | Give up a sync rather than delay Claude Code |

### Paths are portable

Write paths **relative to the project root, with `/` separators** - that is what
ships, and it resolves correctly on all three platforms. Absolute paths are also
accepted if you keep data elsewhere:

```json
{ "paths": { "data_dir": "/var/lib/cctracker/data" } }
{ "paths": { "data_dir": "D:/ClaudeUsage/data" } }
```

### Timezone

| Value | Behaviour |
|---|---|
| `"local"` (default) | Follows the machine's timezone |
| `"+05:30"`, `"-08:00"`, `"UTC"` | Fixed offset - works on every platform with no extra packages |
| `"Asia/Kolkata"` | IANA name - needs a timezone database. Built in on Linux and macOS; on Windows run `pip install tzdata` |

An unresolvable timezone falls back to the machine's own rather than failing,
because this code runs inside a Claude Code hook.

### Environment overrides

Useful for CI, containers, or running two installations from one checkout. See
[`.env.example`](.env.example) for the full list with examples.

| Variable | Overrides |
|---|---|
| `CCTRACKER_ROOT` | The project root, and what relative paths resolve against |
| `CCTRACKER_CONFIG` | The config file path |
| `CCTRACKER_DATA_DIR` | `paths.data_dir` and everything under it |
| `CCTRACKER_LOGS_DIR` | `paths.logs_dir` |
| `CCTRACKER_HOST` / `CCTRACKER_PORT` | `server.host` / `server.port` |
| `CCTRACKER_TIMEZONE` | `timezone` |
| `CCTRACKER_NO_BROWSER=1` | Never open a browser |
| `CLAUDE_CONFIG_DIR` | Where Claude Code keeps its config (read by Claude Code too) |

**There are no secrets in this project.** It has no API keys, no tokens and no
credentials, makes no network calls, and never reads
`~/.claude/.credentials.json`. `.env.example` is documentation only - every line
in it is a comment - and `.env` is gitignored. Never put a credential in either
file or in `config.json`.

---

## 13. Token tracking

**Short version: the counts come from Claude Code's own session transcript, which
contains the API's real `usage` object. Nothing is estimated.**

Hook payloads contain no token information at all (see the table in
[section 1](#1-project-overview)). What they do contain is `transcript_path`.
Every assistant record in that transcript carries:

```json
{
  "type": "assistant",
  "timestamp": "2026-09-03T04:31:35.304Z",
  "gitBranch": "fix/clickhouse-startup",
  "sessionId": "00000000-1111-2222-3333-444444444444",
  "version": "2.1.252",
  "message": {
    "model": "claude-opus-5",
    "usage": {
      "input_tokens": 2,
      "cache_creation_input_tokens": 14014,
      "cache_read_input_tokens": 29857,
      "output_tokens": 374
    }
  }
}
```

Reading transcripts is a supported local mechanism: the file path is handed to
the hook by Claude Code itself, and the tracker only ever reads it.

### The tracked fields

| Field | Source | Meaning |
|---|---|---|
| `input_tokens` | actual | Prompt tokens billed at the full input rate (excludes anything served from cache) |
| `output_tokens` | actual | Tokens Claude generated, including thinking tokens |
| `cache_read_input_tokens` | actual | Context served from the prompt cache, billed at a large discount. Usually the biggest number by far |
| `cache_creation_input_tokens` | actual | Context written into the cache this turn |
| `total_tokens` | derived | The sum of the four above |

`total_tokens` is a **volume** figure, not a cost figure - the four dimensions are
billed at very different rates, so do not read the total as spend.

### When a value is unavailable

It is stored as `null`, **never as `0` and never as an estimate**. The dashboard
shows `—` for unknown values. In practice this happens when a prompt was
interrupted before any response was generated: the prompt is recorded, the tokens
are `null`.

**Character-count estimation (`characters / 4`) is not implemented anywhere in
this codebase.** The single place token counts are read is
[`tracker/token_parser.py`](tracker/token_parser.py).

### Attribution

Assistant records do not name their prompt, so the parser attributes them
positionally: a `promptId` on a user record opens a turn, and every assistant
message until the next `promptId` belongs to it. The transcript is strictly
append-ordered, which makes this exact. Tool results carry the same `promptId` and
so stay inside the turn that caused them.

The `usage.iterations` array is deliberately **not** summed - its entries already
roll up into the top-level fields, and adding both would double-count.

---

## 14. Troubleshooting

Start here on any platform:

```
python  -m tracker.cli status      # Windows
python3 -m tracker.cli status      # Linux / macOS
```

It prints the detected OS, the interpreter, whether git and the `claude` CLI were
found, every resolved path, and whether the hooks are installed.

### Nothing is being recorded

```powershell
# Windows
python -m tracker.cli status
Get-Content logs\tracker.log -Tail 30
```

```bash
# Linux / macOS
python3 -m tracker.cli status
tail -n 30 logs/tracker.log
```

Hooks are read when a session starts, so **restart Claude Code after installing**.

### `python` / `python3` is not found

| Platform | Fix |
|---|---|
| Windows | Install from python.org with "Add python.exe to PATH" ticked. If `python` opens the Microsoft Store, the store alias is shadowing it - install properly or use the full path |
| Ubuntu/Debian | `sudo apt install python3 python3-venv` |
| Fedora | `sudo dnf install python3` |
| macOS | `xcode-select --install`, or `brew install python` |

The installer bakes an **absolute** interpreter path into the hook command, so
the hook does not depend on `PATH` at all. If you later move or replace Python,
re-run the installer to refresh it.

### Git is not installed

Project detection falls back to the working directory name, which is correct in
almost every case - it only differs when you run Claude Code in a subdirectory of
a repository. Nothing else is affected.

### Claude Code is not installed / not found on PATH

The installer says so and still writes the hooks; they take effect whenever
Claude Code next runs. To check Claude Code itself:

```
claude doctor
claude --version
```

### PowerShell refuses to run the scripts

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Or skip the wrappers entirely and use `python scripts\install_hooks.py`.

### `Permission denied` running a shell script on Linux/macOS

The executable bit did not survive the copy:

```bash
chmod +x scripts/*.sh start_dashboard.sh
```

### `bad interpreter: /bin/sh^M`

The script was checked out with Windows line endings. The repository's
[`.gitattributes`](.gitattributes) prevents this; if you copied files in by hand:

```bash
sed -i 's/\r$//' scripts/*.sh start_dashboard.sh
```

### Port 8765 is already in use

The dashboard says so in one line rather than raising. Use another port:

```
python  server.py --port 8766      # Windows
python3 server.py --port 8766      # Linux / macOS
```

Or set `server.port` in `config.json`, or `CCTRACKER_PORT`.

### The dashboard shows nothing

Check that JSONL files exist under `data/usage`, then rebuild the index:

```
python -m tracker.cli reindex
```

If there is no data at all, import your existing history:

```
python -m tracker.cli backfill
```

### Permission denied writing to `data/` or `logs/`

The tracker degrades to "nothing recorded" rather than breaking Claude Code, and
logs the reason. Either fix the permissions or point the data elsewhere:

```bash
export CCTRACKER_DATA_DIR=~/cctracker-data
```

### A project has the wrong name

The name is the git repository root's directory name. Check with:

```
git -C <path> rev-parse --show-toplevel
```

Renaming a repository directory starts a new project name from that point on;
earlier data keeps the old name.

### Corrupted JSONL / invalid JSON

Unparseable lines are skipped rather than aborting the read, so a truncated final
line (a crash mid-write) costs at most that one interaction. Rebuild the index
afterwards with `python -m tracker.cli reindex`.

### Duplicate records

Impossible to double-count by design - see
[duplicate protection](#duplicate-protection). If you suspect it anyway, a full
reindex re-derives every total from the JSONL files:

```
python -m tracker.cli reindex
```

### I moved the tracker directory

Re-run the installer so the hook command points at the new location:

```
python scripts/install_hooks.py
```

### Did the hook slow Claude Code down?

`logs/tracker.log` records a warning if a sync exceeds its budget. Normal syncs
take a few milliseconds, because only newly appended transcript bytes are read.

---

## 15. Updating the tracker

```bash
git pull
```

Then, on any platform:

```
python  scripts/install_hooks.py      # Windows
python3 scripts/install_hooks.py      # Linux / macOS
```

Re-running the installer is safe and idempotent: it refreshes the hook command
(in case the interpreter or the project location changed) and reports "no change
needed" when nothing did.

Your collected data is untouched by an update - `data/`, `logs/` and `reports/`
are gitignored, so `git pull` never overwrites them. If the index schema ever
changes, rebuild it from the JSONL source of truth:

```
python -m tracker.cli reindex
```

If you edited `config.json`, `git pull` may report a conflict. Your settings are
all optional overrides, so the simplest resolution is to take the new file and
re-apply your changes - or move them into environment variables instead
(see [section 12](#12-configuration)).

---

## 16. Uninstalling

### Remove the Claude Code integration

| Platform | Command |
|---|---|
| Windows | `python scripts\uninstall_hooks.py` |
| Ubuntu/Linux | `python3 scripts/uninstall_hooks.py` |
| macOS | `python3 scripts/uninstall_hooks.py` |

Wrappers: `.\scripts\uninstall_hooks.ps1` (Windows),
`./scripts/uninstall_hooks.sh` (Linux/macOS).

Only hook entries carrying this project's `--cctracker` marker are removed. Every
other hook and every other setting is left exactly as it was, and the settings
file is backed up first.

### Also delete the collected data

Collected data is **not** deleted by default, because it is the one thing here
that cannot be regenerated. To remove it as well:

```
python  scripts/uninstall_hooks.py --purge-data      # Windows
python3 scripts/uninstall_hooks.py --purge-data      # Linux / macOS
```

It lists what it is about to delete and asks for confirmation (`--yes` skips the
prompt). Or do it yourself:

```powershell
Remove-Item -Recurse -Force data, reports, logs     # Windows
```

```bash
rm -rf data reports logs                            # Linux / macOS
```

### Remove the project entirely

Delete the directory. Nothing is installed outside it except the two hook entries
in `~/.claude/settings.json`, so remove those first.

---

## 17. Privacy

**Never read or stored:** Claude authentication credentials, API keys, OAuth or
session tokens, cookies, passwords, environment secrets. The tracker never opens
`~/.claude/.credentials.json`, never intercepts network traffic, never proxies the
API, and makes no outbound connections of any kind.

**Is stored:** your prompt text, working directory paths, git branch names, model
names and token counts - all under `data/` on this machine only.

### Prompt text

Prompt text can contain sensitive project information. To store only a length and
a hash instead of the text, set in `config.json`:

```json
{ "store_prompt_text": false }
```

The default is `true`. With it off, `prompt` becomes `null` while `prompt_length`
and `prompt_hash` are kept, so counting and deduplication still work but prompt
search no longer matches anything. The setting applies to newly recorded
interactions; delete existing files if you want past prompt text gone.

Long prompts are truncated on disk at `prompt_text_max_chars` (default 8000);
`prompt_length` always records the true length.

### Why usage data is gitignored

`data/`, `logs/` and `reports/` are excluded from version control **deliberately**.
`data/usage/**/prompts.jsonl` contains your prompt text, working directory paths
and git branch names - project-sensitive information that must not travel with a
fork, a clone or a pushed branch. Everything under `data/` except the raw JSONL is
rebuildable anyway.

To back the data up, copy the `data/` directory yourself. It is plain JSONL and
reads identically on any platform.

### The dashboard

Bound to `127.0.0.1`, with no authentication, because it is not reachable from
another machine. A non-loopback bind address is **refused at startup**, not
merely discouraged. Do not put the dashboard behind a public reverse proxy - it
exposes your prompt text. To view it from another machine, use an SSH tunnel
(see [section 4](#4-ubuntu--linux-setup)).

Prompt text is HTML-escaped before rendering, so a prompt containing markup
cannot execute in the page. The API takes no filesystem paths from the caller,
and static file serving is confined to the `dashboard/` directory.

---

## 18. Known limitations

Known and deliberate limits, so the numbers are not over-read:

1. **Hooks expose no token data.** All counts come from the session transcript.
   If a future Claude Code version changes the transcript format, token capture
   would need updating - `tracker/token_parser.py` is the single place to change.

2. **Tokens are volume, not cost.** The four dimensions are billed at very
   different rates (cache reads are heavily discounted, cache writes carry a
   premium). `total_tokens` is dominated by cache reads and should not be read as
   spend. This tracker deliberately does not compute a currency figure, because
   doing so would require hard-coding prices that change.

3. **A turn is the unit, not an API request.** One prompt often means several API
   requests (tool use round-trips). Their usage is summed into one interaction;
   `assistant_messages` tells you how many there were.

4. **Interrupted prompts have no tokens.** If you cancel before any response, the
   prompt is recorded with `null` usage rather than a guess.

5. **`--bare` and `--safe-mode` skip hooks.** Sessions started with either flag are
   not tracked live. `python -m tracker.cli backfill` picks them up afterwards,
   since the transcript is still written.

6. **Subagent (`Task`) usage** is attributed to the parent turn and counted in
   `sidechain_messages`. In the transcripts examined, subagent messages were not
   written to the main session transcript, so their tokens may not appear at all.

7. **Only what Claude Code writes locally is visible.** There is no local record
   of usage from other clients (claude.ai, the API directly, other machines), and
   this tracker does not query any billing endpoint.

8. **Deleted transcripts cannot be backfilled.** Claude Code prunes old sessions;
   once a transcript is gone, only data already recorded here survives. This is a
   reason to install the hooks rather than relying on periodic backfill.

9. **Days are bucketed in local time** (or the configured timezone). Transcripts
   store UTC. Interactions around midnight land on the local day, which is what
   makes the dashboard match your working day.

10. **IANA timezone names need a timezone database on Windows.** Linux and macOS
    ship one; on Windows either run `pip install tzdata` or use a fixed offset
    such as `"+05:30"`, which needs nothing anywhere.

11. **Linux and macOS are implemented but not physically tested.** The project was
    developed on Windows 11. All platform-specific behaviour is isolated in
    `tracker/platform_utils.py` and every branch of it is covered by tests that
    run on any machine, but no run on real Linux or macOS hardware has been
    performed.

---

## Appendix A: Architecture

```
Claude Code session
        |
        | Stop / SessionEnd hook  (payload on stdin)
        v
scripts/collect_usage.py  ---->  tracker/collector.py
                                       |
                    reads transcript_path (incrementally, from a byte offset)
                                       |
                                 tracker/parser.py        group records into turns
                                 tracker/token_parser.py  pull the real usage numbers
                                 tracker/project_detector.py  git root -> project name
                                 tracker/platform_utils.py    every OS-specific decision
                                       |
                        +--------------+--------------+
                        v                             v
             tracker/storage.py                tracker/database.py
             JSONL  (source of truth)          SQLite (rebuildable index)
             data/usage/Y/M/D/project/         data/index.sqlite3
                        |                             |
                        +--------------+--------------+
                                       v
                                   server.py           localhost-only JSON API
                                       v
                                 dashboard/            HTML + CSS + vanilla JS
```

### Why both JSONL and SQLite

The JSONL tree is the **source of truth**: append-only, human-readable,
greppable, and independent of any database.

SQLite is a **disposable index** on top of it. Once history reaches tens or
hundreds of thousands of interactions, answering "tokens per project for the last
30 days" by walking every file is linear in total history and needs the whole
dataset in memory. The index makes those queries indexed and keeps the
dashboard's memory flat regardless of how much history exists. SQLite is part of
the Python standard library on every platform, so it adds no dependency.

Nothing depends on the index surviving:

```
python -m tracker.cli reindex     # fully rebuilt from the JSONL files
```

The test suite asserts that index totals and JSONL totals agree exactly.

### Files

```
Claude_Code/
├── README.md
├── requirements.txt              only pytest, and only for the tests
├── config.json                   all settings
├── .env.example                  optional environment overrides (no secrets)
├── .gitattributes                pins line endings so clones work on every OS
├── server.py                     dashboard + JSON API (loopback only)
├── start_dashboard.bat           Windows launcher
├── start_dashboard.ps1           Windows launcher (PowerShell)
├── start_dashboard.sh            Linux / macOS launcher
│
├── tracker/
│   ├── __init__.py
│   ├── collector.py              hook entry point; transcript -> JSONL
│   ├── parser.py                 transcript records -> turns (incremental)
│   ├── token_parser.py           real token extraction (never estimates)
│   ├── project_detector.py       working directory -> project name
│   ├── platform_utils.py         every Windows/Linux/macOS decision, in one place
│   ├── storage.py                JSONL layout, read/write, dedupe
│   ├── database.py               SQLite index and queries
│   ├── query.py                  shared filter/date-range vocabulary
│   ├── reports.py                daily / monthly / yearly reports
│   ├── hooks.py                  settings.json install / uninstall
│   ├── cli.py                    python -m tracker.cli ...
│   └── utils.py                  logging, time, hashing, file locking
│
├── dashboard/
│   ├── index.html
│   ├── css/dashboard.css
│   └── js/dashboard.js           charts drawn as inline SVG, no libraries
│
├── scripts/
│   ├── install_hooks.py          THE installer, all platforms
│   ├── uninstall_hooks.py        THE uninstaller, all platforms
│   ├── install_windows.ps1       thin wrapper
│   ├── install_hooks.ps1         thin wrapper
│   ├── install_linux.sh          thin wrapper
│   ├── install_macos.sh          thin wrapper
│   ├── uninstall_hooks.ps1       thin wrapper
│   ├── uninstall_hooks.sh        thin wrapper
│   ├── _python.sh                shared interpreter discovery for the .sh wrappers
│   ├── collect_usage.py          the exact command the hook runs
│   └── generate_report.py
│
├── tests/
├── data/
│   ├── usage/YYYY/MM/DD/PROJECT/prompts.jsonl
│   ├── state/                    per-session parse offsets, project cache
│   └── index.sqlite3
├── reports/{daily,monthly,yearly}/
└── logs/{tracker.log,server.log}
```

The shell and PowerShell scripts contain **no business logic**. Each one locates
an interpreter and runs the corresponding Python file, so all three platforms
execute the same tested code.

### Logging

```
logs/tracker.log     the hook: what it recorded, and anything it swallowed
logs/server.log      the dashboard: requests and errors
```

Both paths come from `config.json` (`paths.logs_dir`) and are never hard-coded.
Logging is best-effort by design: if the log file cannot be opened, the logger
degrades to a null handler rather than raising. **A failure anywhere in the
tracker - logging included - can never break Claude Code**; the hook catches
everything and exits 0.

---

## Appendix B: JSON schema

One line per interaction, where an interaction is **one prompt and everything
Claude did in response to it**:

```json
{
  "id": "0fcd644bbf4313e3a2b2d5abfb2d654f",
  "revision": 0,
  "timestamp": "2026-09-03T14:32:10+05:30",
  "date": "2026-09-03",
  "year": 2026,
  "month": 9,
  "day": 3,

  "project": "data-platform",
  "working_directory": "/home/you/projects/data-platform",
  "git_repository": "/home/you/projects/data-platform",
  "git_branch": "fix/clickhouse-startup",

  "session_id": "00000000-1111-2222-3333-444444444444",
  "prompt_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",

  "model": "claude-opus-5",
  "models": ["claude-opus-5"],

  "prompt": "Fix the ClickHouse startup error...",
  "prompt_length": 46,
  "prompt_hash": "sha256:523979f70e8c...",
  "prompt_truncated": false,

  "usage": {
    "input_tokens": 12500,
    "output_tokens": 3200,
    "cache_read_input_tokens": 2100,
    "cache_creation_input_tokens": 0,
    "total_tokens": 17800
  },
  "usage_source": "claude_code_transcript",

  "assistant_messages": 2,
  "sidechain_messages": 0,
  "origin": "human",
  "prompt_source": "typed",
  "claude_code_version": "2.1.252",
  "entrypoint": "claude-vscode",
  "tracker_version": "1.0.0"
}
```

`working_directory` and `git_repository` are recorded in the platform's own
form - `D:\Projects\app` on Windows, `/home/you/projects/app` on Linux,
`/Users/you/projects/app` on macOS - and the dashboard reads all three.

### Field notes

| Field | Meaning |
|---|---|
| `id` | `sha256(session_id, prompt_id)`, truncated. Stable across reruns - this is what makes duplicate protection work |
| `revision` | Normally `0`. Bumped when a turn is re-recorded because more of it reached disk after it was first written. **Readers must keep the highest revision per `id`** |
| `usage.*` | Real API counts. `null` means Claude Code did not report that dimension - never `0` as a stand-in |
| `usage.total_tokens` | Sum of the four dimensions above it. `null` if none were reported |
| `usage_source` | Always `claude_code_transcript`, recording where the numbers came from |
| `model` | The model that produced most of the turn's output. `models` lists all of them if a turn spanned more than one |
| `origin` | `human` for prompts you sent; `task-notification` / `system` for turns Claude Code started itself. Both cost tokens, so both are recorded; only `human` counts toward "user-initiated" prompts |
| `assistant_messages` | How many assistant messages the turn contained |
| `prompt` | `null` when prompt storage is disabled - see [privacy](#17-privacy) |

### Duplicate protection

Three independent layers:

1. **Stable id.** `(session_id, prompt_id)` identifies a turn permanently, so
   re-processing a transcript can never create a second record for it.
2. **Per-session state.** `data/state/sessions/*.json` remembers a byte offset
   and a signature (`messages:tokens`) for every turn already written, so a
   normal re-sync does no work at all.
3. **Revision-aware reads.** If a turn's signature changes because a late message
   landed after `Stop` fired, a new line is appended with `revision + 1`. Both the
   index and `dedupe_records()` keep only the highest revision, so totals stay
   correct without ever rewriting an existing line.

---

## Appendix C: API reference

Every endpoint accepts `from`, `to`, `range`, `year`, `month`, `project`, `model`,
`search`, `origin`, `session_id`. All are read-only and bound to loopback.

| Endpoint | Returns |
|---|---|
| `GET /api/summary` | Totals for the current filter |
| `GET /api/projects` | Per-project aggregates with percentages |
| `GET /api/usage` | Per-day aggregates |
| `GET /api/models` | Per-model aggregates |
| `GET /api/prompts` | Paginated history (`page`, `per_page`, `sort`, `dir`) |
| `GET /api/filters` | Dropdown values (projects, models, years, branches) |
| `GET /api/meta` | Last-updated timestamp, settings and platform |
| `GET /api/export.csv` | CSV of everything matching the filter |
| `GET /api/export.json` | JSON of everything matching the filter |
| `GET /api/refresh` | Re-scan the JSONL tree into the index |

No endpoint accepts a filesystem path, and static files are served only from the
`dashboard/` directory, so the API cannot be used to read arbitrary files.

---

## Appendix D: Reports

```
python  scripts/generate_report.py                # Windows
python3 scripts/generate_report.py                # Linux / macOS
python  scripts/generate_report.py --only daily
python -m tracker.cli report
```

Writes one file per period actually present in the data:

```
reports/daily/2026-09-03.json
reports/monthly/2026-09.json
reports/yearly/2026.json
```

```json
{
  "date": "2026-09-03",
  "total_prompts": 124,
  "input_tokens": 523421,
  "output_tokens": 121231,
  "cache_tokens": 51231,
  "total_tokens": 644652,
  "projects": {
    "data-platform": 312421,
    "AccentHRP": 201231,
    "ReviewMint": 131000
  }
}
```

Reports are derived views and can be regenerated or deleted freely.

---

## Appendix E: Running the tests

```
pip install -r requirements.txt

python  -m pytest tests/ -q       # Windows
python3 -m pytest tests/ -q       # Linux / macOS
```

Coverage: project detection, path handling on all three platforms, git detection
(and its absence), date and timezone bucketing, JSONL storage, token aggregation,
duplicate and revision handling, date/project/model filtering, prompt search, CSV
and JSON export, the dashboard API, loopback-only binding, configuration
(including environment overrides and invalid values), hook installation and
uninstallation, idempotency, settings preservation, report generation, the CLI,
and that hook failures never propagate to Claude Code.

Every test uses temporary directories - none depends on a specific path, a
specific user, or a specific operating system. Where a platform-specific branch
exists, `sys.platform` is monkeypatched so the Windows, Linux and macOS code
paths are all exercised wherever the suite runs.
