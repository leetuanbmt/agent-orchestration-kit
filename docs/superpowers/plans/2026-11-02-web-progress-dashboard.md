# Web Progress Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **In this repository** each task below is handed to the executor role with
> `bridge_create_task` (fields given under "Bridge task"), run with `bridge_execute_task`, and
> reviewed from `bridge_task_result` before approval. The executor does not commit; the
> reviewer commits after approval.

**Goal:** `agent-bridge web` serves a read-only, bilingual (VI/EN), dark "metric tiles" page on
`127.0.0.1` showing what needs attention and how far each request has progressed across every
project on the machine.

**Architecture:** `bridge/progress.py` builds a language-neutral view model from each
project's `bridge.db` opened `mode=ro`; `bridge/webui.py` is a thin stdlib HTTP server exposing
it as JSON plus static files; `bridge/static/` renders it with vanilla JS.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `http.server`, `hashlib`), existing helpers
(`security.redact`, `render._next_for`, `accounts.known_projects`, `install.git_dir`/`bridge_dir`),
HTML/CSS/vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-web-progress-dashboard-design.md`

## Global Constraints

- No new dependency (runtime or dev). No web fonts; system font stack only.
- Listen on `127.0.0.1` only; no `--host` option; default port `8765`; `--port 0` allowed.
- Every database is opened as `file:<path>?mode=ro` (URI), one connection per call, closed
  before returning. Nothing writes to any `bridge.db`.
- Ids: tasks match `^T\d{4,}$`, workflows `^W\d{4,}$`; `pid` = `sha256(root).hexdigest()[:12]`.
- Truncation after `redact()`: objective 300, failure 500, review note 500, final summary 500
  characters.
- The API is language-neutral (keys, numbers, timestamps); all reader-facing wording is in
  `app.js` `I18N.vi` / `I18N.en`. Objectives are shown verbatim.
- Colours (A2): ground `#2B2419`, tile `#3A3125`, ink `#F4EFE7`, muted `#B9AE9C`, done `#4CB782`,
  review `#3A8C67`, check `#2F6F55`, build `#2A5A48`, queue `#5A4E3E`, paused `#8C8170`, needs
  `#E8A54B`, blocked `#E46A5E`, running/activity `#5AA9E6`. Dark only.
- Tests carry the repo's metadata comments above each test: `@pt.id`, `@pt.item`,
  `@pt.condition`, `@pt.expected`, `@pt.target`, `@pt.method`, `@pt.legacy` (see
  `tests/unit/test_packaging.py`).
- No change to the MCP server, the state machine, `.ai/`, protected paths or the Textual TUI.
- Executor must not set `TMPDIR` and must leave no scratch files (any stray file is a scope
  violation). The sandbox has no network.

## Review Focus

1. **A project's DB is locked or mid-migration while the MCP server runs** → that project shows
   `error: "unreadable"`, the others render, nothing raises (T1 test
   `test_snapshot_skips_unreadable_project`).
2. **Legacy databases without the `agent`/`workflow_id` columns** (older kit versions) → tasks
   still read, grouped as lone requests (T1 test `test_old_schema_without_workflow_column`).
3. **A request whose tasks are all CANCELLED** → percent `None` (shown "–"), never a division by
   zero (T1 test `test_percent_all_cancelled_is_none`).
4. **Hostile text in objectives or failures** (`<script>`, tokens) → redacted on the server, set
   with `textContent` in the browser (T1 test `test_free_text_is_redacted_and_truncated`; T3
   acceptance: no `innerHTML` in `app.js`).
5. **Host header with the right port but another name, or a missing Host** → 421 (T2 test
   `test_rejects_foreign_or_missing_host`).

---

### Task 1: View model (`bridge/progress.py`)

**Files:**
- Create: `bridge/progress.py`
- Modify: `bridge/service.py:993-1025` (`project_tasks` uses `progress.discover_roots`)
- Test: `tests/unit/test_progress.py`

**Interfaces:**
- Consumes: `security.redact(text, limit)`, `render._next_for(task: dict) -> str | None`
  (needs keys `task_id`, `state`, `needs_human_approval`, `review_pending`, `failure`),
  `accounts.known_projects()`, `install.git_dir(root)`, `install.bridge_dir(gdir)`,
  `quota.remaining_pct(rate_limits, now)`, `config.load_config(root)`,
  `orchestrator.load(root, cfg)`.
- Produces (used by T2):
  - `project_id(root: str) -> str`
  - `discover_roots(here: Path | None = None) -> list[str]` (known projects plus their git
    worktrees, de-duplicated, order kept; moved from `service.project_tasks`)
  - `stage_of(state: str, prev_state: str | None) -> str | None` → one of
    `"queue" "build" "check" "review" "done"`, `None` for `CANCELLED`
  - `exception_of(state: str, needs_human: bool, failure: str | None) -> str | None` → one of
    `"needs" "blocked" "failed" "paused" "review_wait"`
  - `pause_reason(failure: str | None) -> str` → `"QUOTA" "AUTH" "NETWORK" "INTERRUPTED"
    "MISSING_BINARY" "OTHER"` (prefix before `:`)
  - `percent(counts: dict[str, int]) -> int | None`
  - `snapshot(roots: list[str] | None = None, now: float | None = None) -> dict` (shape of spec
    §3.5 `/api/overview`)
  - `request_detail(pid: str, rid: str, roots: list[str] | None = None) -> dict | None`
  - `task_detail(pid: str, tid: str, roots: list[str] | None = None) -> dict | None`
  - `TASK_ID = re.compile(r"^T\d{4,}$")`, `WORKFLOW_ID = re.compile(r"^W\d{4,}$")`

**Bridge task:**
- objective: "Add bridge/progress.py: a read-only, language-neutral view model of every
  project's tasks for the web dashboard, with unit tests."
- allowed_paths: `bridge/progress.py`, `bridge/service.py`, `tests/unit/test_progress.py`
- verification_commands: `uv run pytest -q tests/unit/test_progress.py`,
  `uv run pytest -q tests/integration/test_cli_views.py`, `scripts/check.sh`
- risk_level: medium

Rules the implementer cannot infer (from spec §3):

| state | stage | exception |
|---|---|---|
| BACKLOG, READY | queue | – |
| RUNNING, REWORK | build | – |
| VERIFYING | check | – |
| REVIEW | review | review_wait |
| APPROVED | review | needs if `needs_human` else – |
| DONE | done | – |
| PAUSED | stage of prev | paused |
| FAILED | build | failed |
| BLOCKED | stage of prev | blocked |
| CANCELLED | None | – |

- "prev" = `from_state` of the task's latest `events` row with `kind='transition'` and
  `to_state` = current state; if prev is itself PAUSED/BLOCKED/FAILED/None → `build`.
- Attention order: needs, blocked, failed, paused (AUTH, then QUOTA, then the rest),
  review_wait; ties by oldest `updated_at` first. `snapshot()` returns every attention item;
  the UI shows 5.
- `percent` = `floor(100 * done / (total - cancelled))`; `None` when the denominator is 0.
- Requests: one per workflow (`workflows.id`, name = `objective`), plus one per task whose
  `workflow_id` is NULL (id = task id, name = first line of the task objective). Request stage =
  lowest stage among its non-done, non-cancelled tasks (queue < build < check < review), `done`
  when all counted tasks are done; request exception = most severe among its tasks. Sorted:
  with exception first (by severity), then `updated_at` desc; at most 50.
- Running: tasks in RUNNING/VERIFYING; `started_at` from the latest `attempts` row;
  `timeout_s` = the agent's `timeout_s` from `orchestrator.load(root, load_config(root))`, else
  `load_config(root).default_timeout_s`, else 1800 (catch every exception while loading config);
  `quota_left` = `remaining_pct(json.loads(rate_limits), now)` from the latest `account_quota`
  row of the task's account, else `None`.
- Activity: `events` with `kind='transition'` and `ts >= now - 86400`; `per_hour` is 24 ints,
  index 23 = the current hour; `done_24h` counts `to_state='DONE'`; `latest` = newest such event
  (`task_id`, `to_state`, `ts`, `reason` = `pause_reason(failure)` when `to_state='PAUSED'`).
- Project badge: most severe exception in the project (`{"kind": ..., "ref": <request id>}`),
  else `{"kind": "worktrees", "count": n}` when the root has other worktrees, else `None`.
- `root` in output is shortened: `str(Path(root))` with the home prefix replaced by `~`.
- Read every table defensively: missing `workflows`/`attempts`/`account_quota` tables or missing
  columns (`workflow_id`, `agent`, `account`) behave as empty / NULL.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_progress.py`. Build fixture DBs with
  `bridge.state.Store(<tmp>/.git/agent-bridge/bridge.db)` in a git repo made by the `repo`
  fixture, using `Store.create`, `Store.transition`, `Store.create_workflow`,
  `Store.start_attempt`; freeze time by passing `now=`.

```python
def test_stage_mapping_covers_every_state():
    assert {s: stage_of(s, None) for s in State} == {
        "BACKLOG": "queue", "READY": "queue", "RUNNING": "build", "REWORK": "build",
        "VERIFYING": "check", "REVIEW": "review", "APPROVED": "review", "DONE": "done",
        "PAUSED": "build", "FAILED": "build", "BLOCKED": "build", "CANCELLED": None}
    assert stage_of("BLOCKED", "VERIFYING") == "check"
    assert stage_of("PAUSED", "REVIEW") == "review"

def test_exceptions_and_pause_reason():
    assert exception_of("APPROVED", True, None) == "needs"
    assert exception_of("APPROVED", False, None) is None
    assert exception_of("REVIEW", False, None) == "review_wait"
    assert pause_reason("QUOTA: limit hit") == "QUOTA"
    assert pause_reason("weird") == "OTHER"

def test_percent_rounds_down_and_ignores_cancelled():
    assert percent({"done": 2, "queue": 1}) == 66
    assert percent({"done": 9, "build": 1, "cancelled": 5}) == 90

def test_percent_all_cancelled_is_none():
    assert percent({"cancelled": 3}) is None

def test_snapshot_attention_order(repo):  # needs < blocked < failed < paused(AUTH<QUOTA) < review_wait
    ...  # assert [a["kind"] for a in snap["attention"]] == ["needs", "blocked", "failed", "paused", "paused", "review_wait"]
    ...  # assert the AUTH pause precedes the QUOTA pause; needs item has command "agent-bridge approve T0001"

def test_snapshot_groups_workflows_and_lone_tasks(repo):
    ...  # one workflow with 3 tasks + 1 lone task -> 2 requests; workflow name == its objective; done/total counted

def test_snapshot_running_and_activity(repo):
    ...  # a RUNNING task with an attempt started 240 s before now -> running[0]["started_at"] == that time, timeout_s == 1800
    ...  # two DONE transitions in the last 24 h and one 25 h ago -> activity["done_24h"] == 2, len(per_hour) == 24

def test_free_text_is_redacted_and_truncated(repo):
    ...  # objective containing "sk-" + 40 chars and 400 chars long -> no token in snapshot, len <= 301

def test_snapshot_skips_unreadable_project(repo, tmp_path):
    ...  # second root whose bridge.db is not SQLite -> that project has error "unreadable", the first renders

def test_old_schema_without_workflow_column(tmp_path):
    ...  # tasks table created without workflow_id/agent/account columns -> one lone request per task

def test_snapshot_does_not_modify_databases(repo):
    ...  # sha256 and mtime of bridge.db (and -wal if present) identical before and after snapshot()

def test_details_validate_ids(repo):
    pid = project_id(str(repo))
    assert task_detail(pid, "../etc", roots=[str(repo)]) is None
    assert task_detail("0" * 12, "T0001", roots=[str(repo)]) is None
    assert request_detail(pid, "W0001", roots=[str(repo)])["tasks"]
```

Each `...` stands for fixture setup plus the assertion written in its comment.

- [ ] **Step 2: Run** `uv run pytest -q tests/unit/test_progress.py` — expected: FAIL
  (`ModuleNotFoundError: bridge.progress`).
- [ ] **Step 3: Implement** the Produces interfaces in `bridge/progress.py` with the rules above.
  `task_detail` returns: `task_id, objective, state, stage, exception, needs_human, risk_level,
  agent, account, attempts_used, max_attempts, attempts[{n, kind, outcome, started_at, ended_at,
  exit_code}], checks[{command, passed, error}], scope_violations, head_changed, files, stat,
  reviews[{decision, notes, actor, ts}], command, failure` — never `patch`, `final` truncated to
  500, `stderr_tail` omitted. `max_attempts` = `spec.max_attempts` or 3.
- [ ] **Step 4: Change** `service.project_tasks` to iterate `progress.discover_roots(here)`;
  behaviour unchanged.
- [ ] **Step 5: Run** the three verification commands — expected: all pass.
- [ ] **Step 6: Commit** (reviewer, after approval)

```bash
git add bridge/progress.py bridge/service.py tests/unit/test_progress.py
git commit -m "add a read-only progress view model for the web dashboard"
```

---

### Task 2: HTTP server and `web` command

**Files:**
- Create: `bridge/webui.py`
- Modify: `bridge/cli.py` (subparser `web`, dispatch before `asyncio.run`)
- Test: `tests/integration/test_webui.py`, `tests/integration/test_cli_views.py`

**Interfaces:**
- Consumes: `progress.snapshot`, `progress.request_detail`, `progress.task_detail`,
  `progress.discover_roots`, `progress.TASK_ID`, `progress.WORKFLOW_ID`.
- Produces:
  - `make_server(port: int = 8765, roots: list[str] | None = None) -> ThreadingHTTPServer`
    (bound to `("127.0.0.1", port)`; `server.server_address[1]` is the real port)
  - `serve(port: int = 8765, open_browser: bool = False) -> None` (prints
    `agent-bridge web: http://127.0.0.1:<port>/`, serves until Ctrl+C)
  - `STATIC = Path(__file__).parent / "static"`
  - Routes: `GET /` → `static/index.html`; `GET /static/<name>` for `app.css`, `app.js` only
    (allowlist); `GET /api/overview`; `GET /api/projects/<pid>/requests/<rid>`;
    `GET /api/projects/<pid>/tasks/<tid>`. JSON bodies UTF-8, `ensure_ascii=False`.

**Bridge task:**
- objective: "Add agent-bridge web: a localhost-only, GET-only HTTP server for the progress view
  model and static files, with integration tests."
- allowed_paths: `bridge/webui.py`, `bridge/cli.py`, `tests/integration/test_webui.py`,
  `tests/integration/test_cli_views.py`
- verification_commands: `uv run pytest -q tests/integration/test_webui.py tests/integration/test_cli_views.py`,
  `scripts/check.sh`
- depends_on: Task 1. risk_level: medium

Pinned behaviour (spec §5):
- `Host` must equal `127.0.0.1:<port>` or `localhost:<port>`, else 421 with `{"error": "bad host"}`.
- Methods other than GET/HEAD → 405 with `Allow: GET`.
- Unknown route, unknown pid, id failing `TASK_ID`/`WORKFLOW_ID` → 404 `{"error": "not found"}`.
- Headers on every response: `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`.
- No `Access-Control-*` header. Request logging off (`log_message` returns None).
- Port in use → `serve` prints `error: port <n> is in use; try --port 0` to stderr and exits 1.
- If `bridge/static/index.html` does not exist yet, `/` returns 503 `{"error": "ui not built"}`
  (Task 3 adds the files).
- CLI: `agent-bridge web [--port N] [--open]`; refused under `AGENT_BRIDGE_AGENT=1` by the
  existing guard in `main()`.

- [ ] **Step 1: Write the failing tests** in `tests/integration/test_webui.py` using
  `make_server(0, roots=[<fixture repo with a Store and two tasks>])` in a thread and
  `http.client.HTTPConnection`:

```python
def test_overview_shape(server): ...      # 200, keys {"generated_at","projects","attention","running","activity","requests"}
def test_rejects_foreign_or_missing_host(server): ...  # Host "evil.test:<port>" -> 421; no Host header -> 421
def test_only_get(server): ...            # POST /api/overview -> 405, header Allow == "GET"
def test_bad_ids_are_404_without_paths(server): ...    # /api/projects/<pid>/tasks/..%2Fx and unknown pid -> 404, body has no "/" path of the repo
def test_security_headers(server): ...    # the four headers above on / and /api/overview; no Access-Control-Allow-Origin
def test_static_allowlist(server): ...    # /static/../progress.py -> 404
def test_requests_leave_db_unchanged(server): ...  # hash+mtime of bridge.db equal before/after 10 requests
```

and in `tests/integration/test_cli_views.py`:

```python
def test_web_help(): ...                  # `agent-bridge web --help` exits 0 and mentions --port and --open
def test_web_refused_inside_agent(): ...  # AGENT_BRIDGE_AGENT=1 agent-bridge web -> exit 2, "refused"
```

- [ ] **Step 2: Run** the first verification command — expected: FAIL (`ModuleNotFoundError: bridge.webui`).
- [ ] **Step 3: Implement** `bridge/webui.py` (subclass `BaseHTTPRequestHandler`) and the `web`
  subcommand in `bridge/cli.py` (handle `args.cmd == "web"` in `main()` right after the agent
  guard, calling `webui.serve(args.port, args.open)`).
- [ ] **Step 4: Run** both verification commands — expected: pass.
- [ ] **Step 5: Commit** (reviewer, after approval)

```bash
git add bridge/webui.py bridge/cli.py tests/integration/test_webui.py tests/integration/test_cli_views.py
git commit -m "add agent-bridge web: a read-only localhost server for the dashboard"
```

---

### Task 3: Static UI

**Files:**
- Create: `bridge/static/index.html`, `bridge/static/app.css`, `bridge/static/app.js`
- Test: `tests/unit/test_packaging.py`

**Interfaces:**
- Consumes: the JSON of Task 2 routes; field names exactly as in spec §3.5 and Task 1
  `task_detail`.
- Produces: the page; no Python interface.

**Bridge task:**
- objective: "Add the dashboard page (bridge/static) in the A2 dark tile style, bilingual
  VI/EN, rendering /api/overview with keyed updates."
- allowed_paths: `bridge/static/*`, `tests/unit/test_packaging.py`
- verification_commands: `uv run pytest -q tests/unit/test_packaging.py`, `scripts/check.sh`
- depends_on: Task 2. risk_level: medium

Pinned by the spec (§3.4, §4) and the approved A2 mockup:
- Layout top to bottom: top bar (brand "agent-bridge"; centred pill filter "Tất cả/All" + one
  pill per project; live pill; VI/EN toggle) → tile grid `repeat(auto-fit, minmax(300px, 1fr))`
  with gap 12px: NEEDS ATTENTION (span 2), PROGRESS (ring, r=40, stroke 11), RUNNING NOW, one
  tile per project, LAST 24 HOURS (sparkline 24 points) → REQUESTS table (columns: request,
  project, stage, progress bar 96px + x/y, updated, id; 3px coloured left marker on exception
  rows) → footer note "view only".
- Tile anatomy: radius 14px, padding 16px 18px, 1px border `rgba(255,255,255,0.07)`; label
  12px bold uppercase letter-spacing .08em with 15px stroke icon; value 36px bold; badge 12px on
  `rgba(255,255,255,0.08)` radius 6px. NEEDS ATTENTION border `rgba(232,165,75,0.45)` when > 0.
- Stage and exception labels:

| key | vi | en |
|---|---|---|
| queue | Chờ | Queued |
| build | Đang làm | Building |
| check | Kiểm tra | Checking |
| review | Duyệt | Review |
| done | Xong | Done |
| needs | Cần bạn | Needs you |
| blocked | Bị chặn | Blocked |
| failed | Lỗi | Failed |
| paused | Tạm dừng | Paused |
| review_wait | Chờ duyệt | Waiting for review |

- Pause reasons: QUOTA "hết quota / out of quota", AUTH "lỗi đăng nhập / login needed",
  NETWORK "mất mạng / network down", INTERRUPTED "bị ngắt / interrupted", MISSING_BINARY
  "thiếu CLI / CLI missing", OTHER "khác / other".
- Behaviour: poll every 3000 ms; stop while `document.hidden`, fetch on `visibilitychange`;
  on fetch error the live pill turns grey "Mất kết nối · thử lại sau 5 giây / Disconnected ·
  retrying in 5 s", content gets `opacity: .5`, retry after 5000 ms. Keyed patching via
  `data-key`. Language default from `navigator.language` (`vi*` → vi, else en), saved in
  `localStorage` (wrapped in try/catch). Hash state `#p=<pid>&lang=<vi|en>&r=<rid>&t=<tid>`.
  Drawer from the right (width `min(560px, 100vw)`), closes on `Esc` and ×; shows summary,
  5-stage stepper, task list, then the selected task's technical detail and its `command` with a
  copy button (`navigator.clipboard.writeText`, fallback: select the text).
- Empty states: no projects → one tile "Chưa có dự án nào. Chạy `agent-bridge init` trong một
  dự án. / No project yet. Run `agent-bridge init` in a project."; project `error` → its tile
  says "Không đọc được lúc này / Cannot read right now"; no attention → "Không có gì đang chờ. /
  Nothing is waiting.".
- Safety: data is written only with `textContent`/`setAttribute`; `app.js` contains no
  `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `eval`, `new Function`; no inline script or
  style attribute handlers (CSP `default-src 'self'`); no external URL.
- Below 700px: tiles one column, attention tile no span, table scrolls inside its box.

- [ ] **Step 1: Write the failing test** in `tests/unit/test_packaging.py`:

```python
def test_dashboard_assets_ship_in_package():  # bridge/static/{index.html,app.css,app.js} exist and "bridge" is in wheel packages
def test_dashboard_js_never_parses_html():    # app.js text contains none of innerHTML, outerHTML, insertAdjacentHTML, eval(, new Function
def test_dashboard_has_no_external_urls():    # no "http://" or "https://" in the three files
```

- [ ] **Step 2: Run** the first verification command — expected: FAIL (files missing).
- [ ] **Step 3: Implement** the three files to the pins above.
- [ ] **Step 4: Run** both verification commands — expected: pass.
- [ ] **Step 5: Reviewer smoke check (not in the suite):** start `agent-bridge web --port 0`
  against a fixture project, load the page with Playwright, assert no console error, the four
  first-row tiles and the table exist, VI/EN toggle changes the PROGRESS label; save a
  screenshot to the scratchpad and show it to the user.
- [ ] **Step 6: Commit** (reviewer, after approval)

```bash
git add bridge/static tests/unit/test_packaging.py
git commit -m "add the web dashboard page"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md` (after "### 3. Follow it in the dashboard"), `README.vi.md` (same place),
  `CHANGELOG.md` (`[Unreleased]` → `### Added`)

**Bridge task:**
- objective: "Document agent-bridge web in both READMEs and the changelog."
- allowed_paths: `README.md`, `README.vi.md`, `CHANGELOG.md`
- verification_commands: `scripts/check.sh`
- depends_on: Task 3. risk_level: low

- [ ] **Step 1: Add** a short "Web dashboard" subsection: command, what it shows, read-only,
  localhost only, `--port`/`--open`, the command table row `web`; Vietnamese text in
  `README.vi.md`; one CHANGELOG line.
- [ ] **Step 2: Run** `scripts/check.sh` — expected: pass.
- [ ] **Step 3: Commit** (reviewer, after approval)

```bash
git add README.md README.vi.md CHANGELOG.md
git commit -m "docs: describe agent-bridge web"
```
