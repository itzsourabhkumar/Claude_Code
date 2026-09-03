# Claude Code Token Usage Tracker

Automatically records **every Claude Code interaction** across **every local project**,
organised by year / month / date / project, and serves a local dashboard for
exploring the data.

```
data/usage/2026/09/03/data-platform/prompts.jsonl
data/usage/2026/09/03/InventoryManagementSystem/prompts.jsonl
data/usage/2026/09/03/AccentHRP/prompts.jsonl
```

Everything is local. No network calls, no proxy, no credentials read or stored,
no changes to your projects.

---

## Table of contents

1. [What the system does](#1-what-the-system-does)
2. [Architecture](#2-architecture)
3. [Installation](#3-installation)
4. [Claude Code hook installation](#4-claude-code-hook-installation)
5. [How to start tracking](#5-how-to-start-tracking)
6. [Directory structure](#6-directory-structure)
7. [JSON schema](#7-json-schema)
8. [How project detection works](#8-how-project-detection-works)
9. [How token counts are obtained](#9-how-token-counts-are-obtained)
10. [The dashboard](#10-the-dashboard)
11. [CLI commands](#11-cli-commands)
12. [Reports](#12-reports)
13. [How to uninstall](#13-how-to-uninstall)
14. [Troubleshooting](#14-troubleshooting)
15. [Privacy considerations](#15-privacy-considerations)
16. [Limitations of Claude Code token reporting](#16-limitations-of-claude-code-token-reporting)

---

## 1. What the system does

You run `claude` inside any project, as you always have. After each response,
Claude Code fires a hook; the hook reads that session's own transcript, extracts
the turn that just completed together with its **actual API token counts**, and
appends one JSON line to a file organised by date and project.

```
cd D:\Projects\data-platform
claude
   -> you send a prompt
   -> Claude Code responds
   -> Stop hook fires
        -> tracker reads the session transcript
        -> detects project (git root) and date (interaction timestamp)
        -> captures the prompt and the real token usage
        -> appends to data/usage/YYYY/MM/DD/data-platform/prompts.jsonl
        -> updates the SQLite index
   -> dashboard at http://127.0.0.1:8765 shows it
```

No per-project setup. No extra command per prompt. Install once, globally.

### Environment this was built and verified against

| | |
|---|---|
| OS | Windows 11 Pro 10.0.26200 |
| Python | 3.14.6 (standard library only) |
| Node.js | v24.18.0 (not required) |
| Git | 2.55.0 |
| Claude Code CLI | 2.1.237 |
| Claude Code runtime that wrote the transcripts | 2.1.252 |
| Claude Code config | `C:\Users\<you>\.claude\` |
| Settings file | `C:\Users\<you>\.claude\settings.json` |
| Session transcripts | `C:\Users\<you>\.claude\projects\<slug>\<session-id>.jsonl` |

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
transcript. See [section 9](#9-how-token-counts-are-obtained).

---

## 2. Architecture

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

The JSONL tree is the **source of truth** and is exactly the layout requested:
append-only, human-readable, greppable, and independent of any database.

SQLite is a **disposable index** on top of it. Once history reaches tens or
hundreds of thousands of interactions, answering "tokens per project for the
last 30 days" by walking every file is linear in total history and needs the
whole dataset in memory. The index makes those queries indexed and keeps the
dashboard's memory flat regardless of how much history exists.

Nothing depends on the index surviving:

```powershell
del data\index.sqlite3
python -m tracker.cli reindex     # fully rebuilt from the JSONL files
```

The test suite asserts that index totals and JSONL totals agree exactly.

### Files

```
Claude_Code/
├── README.md
├── requirements.txt              only pytest, and only for the tests
├── config.json                   all settings
├── server.py                     dashboard + JSON API (127.0.0.1 only)
├── start_dashboard.bat
│
├── tracker/
│   ├── __init__.py
│   ├── collector.py              hook entry point; transcript -> JSONL
│   ├── parser.py                 transcript records -> turns (incremental)
│   ├── token_parser.py           real token extraction (never estimates)
│   ├── project_detector.py       working directory -> project name
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
│   ├── install_hooks.ps1
│   ├── uninstall_hooks.ps1
│   ├── collect_usage.py          the exact command the hook runs
│   └── generate_report.py
│
├── tests/                        171 tests
├── data/
│   ├── usage/YYYY/MM/DD/PROJECT/prompts.jsonl
│   ├── state/                    per-session parse offsets, project cache
│   └── index.sqlite3
├── reports/{daily,monthly,yearly}/
└── logs/{tracker.log,server.log}
```

---

## 3. Installation

```powershell
cd D:\New_Projects\2.project_wise_token_usages\Claude_Code

# Optional. The tracker itself needs no packages; this is only for the tests.
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

If you create `.venv`, the installer bakes that interpreter's absolute path into
the hook command, so tracking keeps working in shells where the venv is not
activated.

---

## 4. Claude Code hook installation

```powershell
.\scripts\install_hooks.ps1
```

Optionally import the Claude Code history already on this machine:

```powershell
.\scripts\install_hooks.ps1 -Backfill
```

This registers two hooks in `~\.claude\settings.json`:

| Event | Why |
|---|---|
| `Stop` | Fires when a turn's response finishes - the point at which the transcript holds that turn's real token usage. This is the main path. |
| `SessionEnd` | A safety net that sweeps up anything the last `Stop` missed (interrupted turn, a message flushed to disk late). |

Guarantees, each covered by a test:

- **Idempotent.** Running it repeatedly never creates a duplicate hook.
- **Additive.** Existing hooks on the same event are preserved.
- **Non-destructive.** Every unrelated setting is copied through untouched.
- **Backed up.** A timestamped `settings.backup-cctracker-*.json` is written before
  any change, and only when something actually changes.
- **Reversible.** Uninstall restores the file to its exact previous content.
- **Safe on bad input.** An unparseable `settings.json` is refused, not overwritten.

Check the result at any time:

```powershell
python -m tracker.cli status
python -m tracker.hooks status
```

---

## 5. How to start tracking

There is no step here. Open any project and use Claude Code normally:

```powershell
cd D:\Projects\ProjectA
claude
```

Because the hooks live in the **user-level** settings file, they apply to every
project on the machine. Your projects are never modified and never need to know
the tracker exists.

To import the history already on disk (Claude Code keeps past sessions locally):

```powershell
python -m tracker.cli backfill
```

Safe to re-run - already-recorded interactions are skipped by their stable id.

---

## 6. Directory structure

The year, month and day come from **each interaction's own timestamp**, converted
to local time, so a day in the dashboard matches your actual working day. No date
is ever hard-coded, and no project name is ever configured.

```
data/usage/
└── 2026/
    └── 09/
        └── 03/
            ├── data-platform/
            │   └── prompts.jsonl
            ├── InventoryManagementSystem/
            │   └── prompts.jsonl
            └── AccentHRP/
                └── prompts.jsonl
```

One interaction per line. Files are only ever appended to.

---

## 7. JSON schema

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
  "working_directory": "D:\\New_Projects\\data-platform",
  "git_repository": "D:\\New_Projects\\data-platform",
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

### Field notes

| Field | Meaning |
|---|---|
| `id` | `sha256(session_id, prompt_id)`, truncated. Stable across reruns - this is what makes duplicate protection work. |
| `revision` | Normally `0`. Bumped when a turn is re-recorded because more of it reached disk after it was first written. **Readers must keep the highest revision per `id`.** |
| `usage.*` | Real API counts. `null` means Claude Code did not report that dimension - never `0` as a stand-in. |
| `usage.total_tokens` | Sum of the four dimensions above it. `null` if none were reported. |
| `usage_source` | Always `claude_code_transcript`, recording where the numbers came from. |
| `model` | The model that produced most of the turn's output. `models` lists all of them if a turn spanned more than one. |
| `origin` | `human` for prompts you sent; `task-notification` / `system` for turns Claude Code started itself. Both cost tokens, so both are recorded; only `human` counts toward "user-initiated" prompts. |
| `assistant_messages` | How many assistant messages the turn contained. |
| `prompt` | `null` when prompt storage is disabled - see [section 15](#15-privacy-considerations). |

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

## 8. How project detection works

Fully automatic, derived from the session's working directory. **There is no
configured list of projects anywhere in this system.**

1. Ask git for the repository root:
   ```
   git rev-parse --show-toplevel
   ```
   and use that directory's name.
2. If the directory is not a git repository, or git is unavailable, use the
   working directory's own name.

```
D:\New_Projects\data-platform                    -> data-platform
D:\New_Projects\InventoryManagementSystem        -> InventoryManagementSystem
D:\Git\data-platform\services\api  (git repo)    -> data-platform
```

Resolutions are cached in `data/state/project_cache.json` (24h by default) so the
hook does not spawn a git process on every turn. Names are sanitised only enough
to be valid directory names; ordinary project names pass through unchanged.

To exclude a directory from tracking entirely, add a glob to `ignore_paths` in
`config.json`:

```json
{ "ignore_paths": ["*/client-confidential", "D:/Scratch/*"] }
```

---

## 9. How token counts are obtained

**Short version: from Claude Code's own session transcript, which contains the
API's real `usage` object. Nothing is estimated.**

Hook payloads contain no token information at all (verified against 2.1.252 -
see the table in [section 1](#1-what-the-system-does)). What they do contain is
`transcript_path`. Every assistant record in that transcript carries:

```json
{
  "type": "assistant",
  "timestamp": "2026-09-03T04:31:35.304Z",
  "cwd": "D:\\New_Projects\\data-platform",
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

### The four dimensions

| Dimension | Source | Meaning |
|---|---|---|
| `input_tokens` | actual | Prompt tokens billed at the full input rate (excludes anything served from cache). |
| `output_tokens` | actual | Tokens Claude generated, including thinking tokens. |
| `cache_read_input_tokens` | actual | Context served from the prompt cache, billed at a large discount. Usually the biggest number by far. |
| `cache_creation_input_tokens` | actual | Context written into the cache this turn. |
| `total_tokens` | derived | The sum of the four above. |

`total_tokens` is a **volume** figure, not a cost figure - the four dimensions are
billed at very different rates, so do not read the total as spend.

### Attribution

Assistant records do not name their prompt, so the parser attributes them
positionally: a `promptId` on a user record opens a turn, and every assistant
message until the next `promptId` belongs to it. The transcript is strictly
append-ordered, which makes this exact. Tool results carry the same `promptId` and
so stay inside the turn that caused them.

The `usage.iterations` array is deliberately **not** summed - its entries already
roll up into the top-level fields, and adding both would double-count.

### When a value is unavailable

It is stored as `null`, never as `0` and never as an estimate. The dashboard shows
`—` for unknown values. In practice this happens when a prompt was interrupted
before any response was generated: the prompt is recorded, the tokens are `null`.

**Character-count estimation (`characters / 4`) is not implemented anywhere in
this codebase.**

---

## 10. The dashboard

```powershell
.\start_dashboard.bat
```

Then open **http://127.0.0.1:8765** (the script opens it for you).

The server binds to `127.0.0.1` only and is not reachable from the network.

### Features

- **Summary cards** - total prompts, input, output, cache, total tokens, projects used.
- **Date quick filters** - Today, Yesterday, Last 7 Days, Last 30 Days, This Month,
  Previous Month, This Year, All Time.
- **Filters** - date from/to, year, month, project, model, prompt search. The
  project and model dropdowns are populated from the data, never hard-coded.
- **Daily Token Usage chart** - switchable between Total / Input / Output / Cache,
  with a crosshair and hover tooltip.
- **Token Usage by Project chart** - switchable between Total / Input / Output;
  click a bar to filter the whole dashboard by that project.
- **Project summary table** - prompts, input, output, cache, total and percentage
  of usage per project; sortable on every column.
- **Prompt history** - every interaction, with timestamp, project, branch, model,
  prompt text and the four token columns. Sortable, paginated at 25/50/100/250
  rows, with search terms highlighted. Click a prompt to expand it.
- **Export CSV / Export JSON** - respects the filters currently applied.
- **Auto refresh** - off by default; 30s / 1min / 5min.
- **Light and dark themes**, following the OS with a manual toggle.

Charts are hand-drawn inline SVG. There is no React, no charting library and no
CDN - the dashboard works with no network connection at all.

### API

Every endpoint accepts `from`, `to`, `range`, `year`, `month`, `project`, `model`,
`search`, `origin`, `session_id`.

| Endpoint | Returns |
|---|---|
| `GET /api/summary` | Totals for the current filter |
| `GET /api/projects` | Per-project aggregates with percentages |
| `GET /api/usage` | Per-day aggregates |
| `GET /api/models` | Per-model aggregates |
| `GET /api/prompts` | Paginated history (`page`, `per_page`, `sort`, `dir`) |
| `GET /api/filters` | Dropdown values (projects, models, years, branches) |
| `GET /api/meta` | Last-updated timestamp and settings |
| `GET /api/export.csv` | CSV of everything matching the filter |
| `GET /api/export.json` | JSON of everything matching the filter |
| `GET /api/refresh` | Re-scan the JSONL tree into the index |

```powershell
.\start_dashboard.bat --port 9000
.\start_dashboard.bat --no-browser
```

---

## 11. CLI commands

```powershell
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

## 12. Reports

```powershell
python scripts\generate_report.py
python scripts\generate_report.py --only daily
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

## 13. How to uninstall

```powershell
.\scripts\uninstall_hooks.ps1
```

Removes only the hooks carrying this project's `--cctracker` marker. Every other
hook and setting is left exactly as it was, and a backup is taken first.

Collected data is **not** deleted. To remove it too:

```powershell
Remove-Item -Recurse -Force data, reports, logs
```

---

## 14. Troubleshooting

**Nothing is being recorded.**

```powershell
python -m tracker.cli status          # are the hooks installed?
Get-Content logs\tracker.log -Tail 30
```

Hooks are read when a session starts, so restart Claude Code after installing.

**`python` is not found by the hook.** The installer bakes an absolute
interpreter path into the command. If you moved or replaced Python, re-run
`.\scripts\install_hooks.ps1` to refresh it.

**The dashboard shows nothing.** Check that JSONL files exist under `data/usage`,
then rebuild the index:

```powershell
python -m tracker.cli reindex
```

**Port 8765 is already in use.**

```powershell
.\start_dashboard.bat --port 8766
```

**A project has the wrong name.** The name is the git repository root's directory
name. Check with `git -C <path> rev-parse --show-toplevel`. Renaming a repository
directory starts a new project name from that point on; earlier data keeps the
old name.

**I moved this tracker directory.** Re-run `.\scripts\install_hooks.ps1` so the
hook command points at the new location.

**Did the hook slow Claude Code down?** `logs/tracker.log` records a warning if a
sync exceeds its budget. Normal syncs take a few milliseconds because only newly
appended transcript bytes are read.

**Verify Claude Code itself is healthy.**

```powershell
claude doctor
claude --version
```

---

## 15. Privacy considerations

**Never read or stored:** Claude authentication credentials, API keys, OAuth or
session tokens, cookies, passwords, environment secrets. The tracker never opens
`~/.claude/.credentials.json`, never intercepts network traffic, never proxies the
API, and makes no outbound connections of any kind.

**Is stored:** your prompt text, working directory paths, git branch names, model
names and token counts - all under `data/` on this machine only.

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

The dashboard is bound to `127.0.0.1` and serves no authentication, because it is
not reachable from another machine. Do not put it behind a public reverse proxy -
it exposes your prompt text.

---

## 16. Limitations of Claude Code token reporting

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

9. **Timestamps are bucketed in local time.** Transcripts store UTC; days are
   bucketed by your local calendar day. Interactions around midnight land on the
   local day, which is what makes the dashboard match your working day.

---

## Running the tests

```powershell
pip install -r requirements.txt
python -m pytest tests/ -q
```

Covers project detection, date-directory creation, JSONL read/write, duplicate
and revision handling, token aggregation, date/project/model filtering, prompt
search, CSV and JSON export, the dashboard API, hook installation and
idempotency, settings preservation, report generation, the CLI, and that hook
failures never propagate to Claude Code.
