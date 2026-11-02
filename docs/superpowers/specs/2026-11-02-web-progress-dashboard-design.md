# Web progress dashboard: design

Date: 2026-11-02 · Status: approved in brainstorming, awaiting spec review

## 1. Goal

A read-only, local web page that shows the progress of every project agent-bridge runs in, for
two audiences at once:

- **Non-technical readers** (designers, PMs, stakeholders) answer within about 5 seconds:
  "is anything stuck or waiting for someone?" and "how far along is each request?".
- **Engineers** reach the evidence (checks, scope, diff stat, attempts, review) within one or
  two clicks.

Visual direction: the "A2 · dark metric tiles" mockup reviewed during design: warm dark
ground, tiles with an uppercase label, a large number, small badges and a mini chart, then a
table with inline bars and a coloured left marker on exception rows. Dark theme only. The
interface is bilingual (VI/EN).

### Decisions taken

| Topic | Decision |
|---|---|
| Audience | Both: plain-language overview, drill-down to technical detail |
| Medium | Local web dashboard, `agent-bridge web` |
| Information hierarchy | Exceptions first ("Needs attention"), then progress, then activity |
| Language | Bilingual VI/EN with a toggle |
| Style | Dark metric tiles (A2), no light theme |
| Architecture | stdlib `http.server`, static HTML/CSS/vanilla JS, JSON API, no new dependency |
| REVIEW state | Listed in "Needs attention" at the lowest priority ("Waiting for review") |

### Non-goals

- No write action of any kind: no approve, resume, cancel, reassign or login from the page.
  Human-only actions are shown as a command to copy.
- No full patch view, no per-attempt log view.
- No light theme, no network exposure beyond `127.0.0.1`, no static HTML export (possible
  later from the same JSON).
- No change to the Textual dashboard, the MCP server or the task state machine.

## 2. Architecture

| Unit | Responsibility | Depends on |
|---|---|---|
| `bridge/progress.py` (new) | Pure Python view model: reads each project's `bridge.db` read-only, maps states to stages, computes percentages, attention items, running tasks, 24 h activity, request rows and detail payloads. No HTTP. | `sqlite3`, `accounts.known_projects()`, the worktree discovery used by `service.project_tasks()`, `render._next_for()`, `security.redact()` |
| `bridge/webui.py` (new) | Thin `ThreadingHTTPServer`: serves `bridge/static/`, three JSON endpoints, `GET` only, security checks. No business logic. | `progress.py` |
| `bridge/static/` (new) | `index.html`, `app.css`, `app.js` (vanilla JS, VI/EN strings). Shipped in the wheel because it lives inside the `bridge` package. | the API |
| `bridge/cli.py` (changed) | New command `agent-bridge web [--port 8765] [--open]`; prints the URL; `--open` uses `webbrowser`. The existing `AGENT_BRIDGE_AGENT` guard in `main()` already covers it. | `webui.py` |

Data flow:

```
browser ── GET /api/overview (every 3 s) ──▶ webui ──▶ progress.snapshot()
                                                         └─ each project/worktree: bridge.db (mode=ro)
open a request ── GET /api/projects/<pid>/requests/<rid> ──▶ progress.request_detail()
open a task    ── GET /api/projects/<pid>/tasks/<tid> ─────▶ progress.task_detail()
```

Project identity: `pid` is the first 12 hex characters of `sha256(root path)`. The server
resolves a `pid` only against the list of known projects and their worktrees; it never accepts
a path. A **request** is a workflow (`workflows.objective`); a task created without a workflow
is a request of its own.

## 3. Data and translation rules

### 3.1 State → stage

Every task has exactly one stage and at most one exception.

| Raw state | Stage (VI / EN) | Exception |
|---|---|---|
| `BACKLOG`, `READY` | Chờ / Queued | – |
| `RUNNING`, `REWORK` | Đang làm / Building | – (REWORK shows "run n/m") |
| `VERIFYING` | Kiểm tra / Checking | – |
| `REVIEW` | Duyệt / Review | **Chờ duyệt / Waiting for review** (lowest priority) |
| `APPROVED` with `needs_human` | Duyệt / Review | **Cần bạn / Needs you** (highest), command `agent-bridge approve <id>` |
| `APPROVED` without `needs_human` | Duyệt / Review | – (the bridge closes it) |
| `DONE` | Xong / Done | – |
| `PAUSED` | stage before the pause | **Tạm dừng / Paused**, reason from `failure` prefix: `QUOTA`, `AUTH`, `NETWORK`, `INTERRUPTED`, `MISSING_BINARY` |
| `FAILED` | Đang làm / Building | **Lỗi / Failed**, with "run n/m" |
| `BLOCKED` | stage before the block | **Bị chặn / Blocked**, diagnosis from `failure` |
| `CANCELLED` | not counted | – (hidden from percentages, visible in detail) |

"Stage before" is the stage of the `from_state` of the task's latest state-change event in
`events`; if none exists, Building.

### 3.2 Attention

Priority: Needs you → Blocked → Failed → Paused (AUTH before QUOTA before the rest) → Waiting
for review. Within one level, the longest wait first. The tile shows at most 5 rows; the count
covers all. Suggested commands come from `render._next_for()` so web, CLI and TUI say the same
thing.

### 3.3 Numbers

- Percentage of a project or request = `DONE / (total − CANCELLED)`, rounded **down**; 100 %
  only when everything counted is done. A request with every task cancelled shows "–".
- Request name: the workflow `objective`, or the first line of a lone task's `objective`, shown
  verbatim (never translated).

### 3.4 Tiles and table

| Element | Source | Rule |
|---|---|---|
| NEEDS ATTENTION (2 columns wide) | `tasks` + 3.1/3.2 | count, top 5 rows, copy button on rows with a command; amber border when count > 0 |
| PROGRESS | `tasks` | percentage, `done/total tasks`, ring split by stage, badge = number of requests |
| RUNNING NOW | `RUNNING`/`VERIFYING` tasks + latest `attempts.started_at` | elapsed vs the agent's timeout as a bar; badge = agent name or "n agents"; footer = quota of that account from the latest `account_quota` row, else "unknown" |
| One tile per project | `tasks` of the project | percentage, stage bar, badge = the project's most severe exception, else "+n worktree", else none |
| LAST 24 HOURS | `events` with `to_state` in the last 24 h | number = transitions to `DONE`; sparkline = events per hour (24 points); footer = latest event as a sentence |
| REQUESTS table | `workflows` + lone tasks | exception rows first, then most recently updated; at most 50 rows; columns: request, project, stage, progress (bar + x/y), updated, id; coloured left marker on exception rows |

The project filter (pill bar) is applied in the browser: `/api/overview` already carries every
project's data. Activity sentences use per-language templates keyed by event kind and state,
for example `paused` + `QUOTA` → "T-0021 tạm dừng: hết quota" / "T-0021 paused: out of quota".

### 3.5 API shapes

`GET /api/overview`:

```json
{
  "generated_at": 1793589000.0,
  "projects": [
    {"pid": "3f2a9c1b7e44", "name": "shop-web", "root": "~/code/shop-web",
     "worktree_of": null, "error": null,
     "counts": {"done": 5, "review": 1, "check": 1, "build": 1, "queue": 1},
     "percent": 55, "badge": {"kind": "needs", "text_key": "needs", "ref": "WF-7"}}
  ],
  "attention": [
    {"pid": "3f2a9c1b7e44", "task_id": "T-0012", "kind": "needs", "objective": "…",
     "since": 1793587500.0, "reason": null, "command": "agent-bridge approve T-0012"}
  ],
  "running": [
    {"pid": "…", "task_id": "T-0016", "stage": "build", "agent": "codex-main",
     "started_at": 1793588760.0, "timeout_s": 1800, "quota_left": 0.64}
  ],
  "activity": {"done_24h": 7, "per_hour": [1, 0, 0, …], "latest": {"task_id": "T-0014",
               "kind": "check", "ts": 1793588880.0, "reason": null}},
  "requests": [
    {"pid": "…", "id": "WF-7", "name": "Checkout: pay with saved cards", "stage": "review",
     "exception": "needs", "done": 3, "total": 6, "updated_at": 1793588880.0}
  ]
}
```

Texts shown to readers are produced in the browser from keys (`kind`, `stage`, `reason`), so
the API stays language-neutral. Objectives, failures and review notes are data, passed through
`redact()` and truncated (objective 300, failure 500, review note 500 characters).

`GET /api/projects/<pid>/requests/<rid>`: the request name, one plain-language summary made of
counts per stage and exception, and its tasks (id, objective, stage, exception, updated).

`GET /api/projects/<pid>/tasks/<tid>`: raw state, `needs_human`, `risk_level`, agent,
account, attempts used / max, the attempts (n, kind, outcome, started, ended, exit code), the
latest attempt's verification checks (command, pass/fail, duration), scope violations,
`head_changed`, diff stat (files, added, removed; **no patch**), review decisions with notes,
and the suggested command.

Errors: unknown `pid`, `rid` or `tid`, or an id not matching `^T-\d+$` / `^WF-\d+$` → 404 with
`{"error": "not found"}` and no path in the body.

## 4. Interface

- `index.html` holds the frame: top bar (brand, project pill filter, live pill, VI/EN), the tile
  grid (`repeat(auto-fit, minmax(300px, 1fr))`), the requests table and the detail drawer.
- `app.css` holds the A2 tokens: ground `#2B2419`, tile `#3A3125`, ink `#F4EFE7`, muted
  `#B9AE9C`, done `#4CB782`, in-progress greens `#3A8C67 / #2F6F55 / #2A5A48`, queued
  `#5A4E3E`, paused `#8C8170`, needs-you `#E8A54B`, blocked `#E46A5E`, running/activity
  `#5AA9E6`. System font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`),
  no web fonts, so the page works offline.
- `app.js`:
  - polls `/api/overview` every 3 s; pauses while the tab is hidden and fetches at once when it
    is shown again;
  - renders by keyed patching (`data-key` = pid, request id, task id): only changed nodes are
    touched, so there is no flicker, the scroll position holds and an open drawer stays open;
  - `I18N = {vi: {…}, en: {…}}` for every interface string and activity template; the default
    language comes from `navigator.language`, the choice is kept in `localStorage`;
  - filter, language and open drawer live in the URL hash (`#p=<pid>&lang=en&r=WF-7`).
- **Detail drawer** slides in from the right in the same dark style. Top: the plain summary,
  the five-stage stepper, the task list. Selecting a task shows the engineer layer (3.5) and the
  suggested command with a copy button. Closes with `Esc` or ×.
- **States:**
  - server unreachable: the live pill turns grey ("Disconnected · retrying in 5 s"), the last
    data stays visible but dimmed;
  - no project yet: one tile explaining `agent-bridge init`;
  - one project unreadable (locked or not a bridge DB): its tile says "Cannot read right now",
    the others render;
  - nothing needs attention: "Nothing is waiting", normal border.
- **Accessibility:** a real `<table>`; rows open with `Enter`; charts carry an `aria-label` with
  the numbers; every exception colour also has a text label; below 700 px tiles stack in one
  column and the table scrolls horizontally inside its box; text contrast at least 4.5:1.

## 5. Security

- Listens on `127.0.0.1` only; there is no `--host` option.
- Rejects with 421 any request whose `Host` header is not `127.0.0.1:<port>` or
  `localhost:<port>` (DNS-rebinding protection).
- `GET` only (others 405); no CORS headers. Every response sets `X-Content-Type-Options:
  nosniff`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer` and
  `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`.
- Default port 8765; if taken, exit with a clear message suggesting `--port 0`; no silent port
  change.
- Opens `bridge.db` only as `file:…?mode=ro`, one connection per request, closed at once; no
  migration, no write, no lock held across requests.
- Never reads credential files, account homes, Codex rollouts or per-attempt log files; only
  tables in `bridge.db`. Free text from the DB is passed through `redact()` and truncated again.
- Roots are shown shortened with `~`; ids in URLs are validated before any query.
- The browser writes data only with `textContent`, never `innerHTML`; commands are built
  server-side from validated ids.
- The command starts no agent, calls no `bridge_*` tool and performs no human-only action.
  It refuses to run under `AGENT_BRIDGE_AGENT=1` like every other command. Nothing under `.ai/`,
  `~/.claude`, `~/.codex` or global configuration is touched.

## 6. Testing

| File | Covers |
|---|---|
| `tests/unit/test_progress.py` (new) | stage mapping for all 12 states incl. stage-before for PAUSED/BLOCKED; attention priority and ordering; `needs_human` → approve command; percentage excludes CANCELLED and rounds down; workflow and lone-task grouping; 24 h counts with a frozen clock; redaction and truncation applied; a corrupt or locked DB is reported for that project only |
| `tests/integration/test_webui.py` (new) | server on port 0 with fixture DBs: `/api/overview` shape; foreign `Host` → 421; `POST` → 405; bad `pid`/`tid` → 404 without paths; security headers present; static files served; database files unchanged after requests (mtime and hash); refused under `AGENT_BRIDGE_AGENT=1` |
| `tests/unit/test_packaging.py` (changed) | the wheel contains `bridge/static/*` |
| `tests/integration/test_cli_views.py` (changed) | `agent-bridge web --help` works |

JavaScript has no unit tests: every rule that can be wrong lives in Python. The last task runs
a one-off Playwright smoke check (page loads, no console error, the four first-row tiles
exist, screenshot), not added to the suite.

## 7. Delegation

Implementation goes through the executor (`bridge_create_task`), as the project rules require.

| Task | Scope | `allowed_paths` | Depends on |
|---|---|---|---|
| T1 | view model + unit tests | `bridge/progress.py`, `tests/unit/test_progress.py` | – |
| T2 | server, `web` command, integration tests | `bridge/webui.py`, `bridge/cli.py`, `tests/integration/test_webui.py`, `tests/integration/test_cli_views.py` | T1 |
| T3 | static UI, packaging test | `bridge/static/*`, `tests/unit/test_packaging.py` | T2 |
| T4 | docs | `README.md`, `README.vi.md`, `CHANGELOG.md` | T3 |

Each task: verification `scripts/check.sh` (or the relevant `uv run pytest …`), `risk_level:
medium`, no new dependency, no change to protected paths, the MCP server or the state machine.

## 8. Done when

- `scripts/check.sh` passes.
- `agent-bridge web --open` shows the A2 layout with real data from the projects on the machine.
- No request changes any `bridge.db`.
