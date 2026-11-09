# Agent Bridge — Architecture Audit & Redesign (2026-11-09)

Phạm vi: audit read-only theo `GOAL` "Agent Bridge Architecture Audit & Redesign". Không sửa source.
Bằng chứng: source `bridge/*.py` tại `6b4872d` (main) và test suite. GitDiagram chỉ dùng làm đối chiếu.

**Baseline:** `uv run pytest -q` → **721 passed, 114 skipped** (347 s, macOS).
- Các file có điều kiện skip: `tests/e2e/test_real_*_smoke.py`, `tests/qa/*` (cần `QA_E2E`),
  `test_sandbox_seatbelt.py`, `test_verify_sandbox.py`, `test_orphan_slot.py`, `test_core.py`.
- `IMPLEMENTATION-TRACEABILITY.md` ghi cùng con số 114, với lý do là Seatbelt lồng nhau và QA opt-in.
- Vì vậy suite này không chứng minh được hành vi của CLI thật hay sandbox thật.

**Nhãn bằng chứng:**
- **C** — Confirmed: đã đọc dòng code tương ứng.
- **P** — Plausible: suy ra từ code, cần repro.
- **U** — Unverified: phụ thuộc hành vi của CLI bên ngoài.

**Nhãn nguồn gốc:**
- **New** — chưa có trong `docs/audits` hay `docs/plans`.
- **Known** — đã có finding ID và vẫn còn mở.
- **Planned** — đã có thiết kế trong `docs/plans`.

> **Cần bạn xác nhận:** GOAL ghi `~/agents-bridge/`, nhưng code dùng `~/.agent-bridge` (`storage.py:26`,
> `registry.py:14`). Không có chỗ nào dùng `~/agents-bridge/`. Báo cáo này giả định GOAL viết nhầm.

---

## 1. Actual Architecture Map

```
             Claude Code (MCP client)        Human terminal              Browser
                    │ stdio                         │                        │ HTTP 127.0.0.1
                server.py ──────────┐            cli.py ─────┐            webui.py
                (15 tools, async)   │      (subcommands,     │      (launch code → cookie)
                                    ▼       tty gates)       ▼               │
                          ┌──────────────── service.Bridge ────────────┐     ├─ progress.py ── raw sqlite (read model riêng)
          tui.py ────────▶│ create/execute/review/close/recover/plan/  │     └─ webactions.py ── subprocess `python -P -m bridge <cmd>`
          (Textual;       │ _drive; ~20 delegate sang gate/runner      │                          (quay lại cli.py)
           + store trực   └──┬──────────┬──────────┬──────────┬────────┘
           tiếp, _summary)   │          │          │          │
                     gate.py (routing│ runner.py  │ review.py  │ orchestrator.py + config.py + registry.py
                     _pick/_route/   │ _start →   │ snapshot/  │ (orchestrator.yaml, accounts.yaml,
                     _unavailable/   │ _run_inner │ diff/scope/│  legacy config.json, ~/.agent-bridge/accounts.json)
                     _quota_fallback/│ → verdict  │ verify     │
                     _reserve, limits)│ → review   │   │        │
                             │       │   │        │ sandbox.py (Seatbelt / Landlock, chỉ cho verification)
                             ▼       ▼   ▼        │
                   accounts.py (Account, AccountLock flock, keychain, login probes)
                   quota.py (rollout rate_limits, usage ledger)
                             │
              executor.py: Adapter → CodexCli | ClaudeCli | OpenCode | Agy | client
                             │ argv + scrubbed env + account env (token) + pass_fds(slot lock)
                             ▼
                   Agent CLI process ──writes──▶ git worktree
Persistence:
  $AGENT_BRIDGE_HOME (~/.agent-bridge)                         ~/.agent-bridge (cứng, registry.py)
    projects/<slug>-<sha12(git common dir)>/project.json         accounts.json, projects.json(+lock), homes/<name>/
      worktrees/<slug>-<sha8(path)>/bridge.db, logs/
    accounts.db  (marks/quota dùng chung theo account_key)      macOS Keychain (service "agent-bridge")
    locks/agent-bridge-acct-<sha>-<i>.lock
  Legacy: <git-dir>/agent-bridge|codex-bridge/, .ai/bridge.db
```

**Chênh lệch với GitDiagram** (GitDiagram tự ghi là "AI-made"):

| GitDiagram | Thực tế |
|---|---|
| `gate.py` = "Verification Gates" | `gate.py` lo routing, eligibility, limit và quota fallback. Verification nằm ở `review.py` + `sandbox.py`. |
| Agent Adapters → Sandboxing | `sandbox.py` chỉ bọc **verification commands** (`review.py:380`). Agent Codex dùng sandbox riêng của Codex. Agent Claude chỉ có permission rules, không có OS sandbox. |
| Web Dashboard → Bridge Service | Web **không đi qua `Bridge`**. `progress.py` đọc thẳng sqlite (`progress.py:150,170`). Action thì spawn CLI subprocess (`webactions.py:104-110`). |
| Terminal Dashboard → Bridge Service | Đúng một phần: TUI còn gọi `bridge.store.*` và hàm private `_summary` (`tui.py:371,472,486,541`). |
| (không có) | Slot `flock` xuyên tiến trình (`accounts.py:488`) và `accounts.db` dùng chung mới là cơ chế phối hợp giữa các bridge. |

## 2. Component Responsibility Matrix

| Module | Trách nhiệm thực tế | Vấn đề |
|---|---|---|
| `server.py` | 15 MCP tool, bọc lại `Bridge` | Gọi hàm sync và blocking trong `async def` (`server.py:50-66`, `service.py:422-428`) |
| `cli.py` | Lệnh cho người và agent, tty gate cho lệnh human-only | `account-clear` không có tty gate (`cli.py:53`, `:467`). `account-add`/`remove` gọi thẳng `accounts.register`, bỏ qua `Bridge` |
| `service.py` (1215 dòng) | Facade: create, execute, review, close, recover, plan, `_drive`, health, accounts view | God-object. Có ~20 delegate tồn tại chỉ để test monkeypatch (`service.py:206-278`). Migration ngầm trong `__init__` (`:95-104`) |
| `runner.py` | Acquire slot → chạy agent → diff → verify → verdict → review stage | Gọi thẳng hàm private của gate (`runner.py:59,66`). Giữ slot suốt verification (`:127` vs `:278`) |
| `gate.py` | Pick, route, eligibility, limit, reserve, quota fallback | Route ghi vào DB **trước** khi acquire slot (`gate.py:353` → `runner.py:76`) |
| `review.py` | Snapshot, diff, scope, verification, content binding | Gọi `git` với config của worktree mà không override (`review.py:30-34`). Recheck vứt bỏ patch sau verification (`:350-362`) |
| `executor.py` | Adapter cho từng CLI, build prompt, chạy process, kill | Request dựng ở 2 nơi (`runner.py:180`, `service.py:726`). `read_deny` chỉ áp cho Claude (`executor.py:462-464`) |
| `state.py` | SQLite per worktree + `accounts.db` dùng chung, transitions | Dùng `check_transition` ở một chỗ (tốt). Bypass: `_promote_dependents` dùng raw SQL (`:711`); workflow state là chuỗi tự do (`:561`). Không có `user_version` |
| `orchestrator.py` / `config.py` / `registry.py` | Config, account registry | 7 nguồn sự thật (§3.3). Danh sách provider hardcode ở ≥6 chỗ |
| `accounts.py` / `quota.py` | Account env, keychain, slot lock, rollout quota, ledger | `read_quota` không cache, mỗi lần gọi walk cả cây sessions (`quota.py:79-120`) |
| `storage.py` | Vị trí store tập trung, migrate, relink, gc | Có 2 đường migrate ngầm khác chạy song song (§3.5) |
| `progress.py` + `app.js` | Read model của web | Logic trạng thái, "needs you", next command và quota bị viết lại lần thứ hai |
| `render.py` / `tui.py` | Bảng CLI, TUI | Bộ quy tắc "needs you" thứ ba (`render.py:21`) |

## 3. Architecture Gap Analysis

### 3.1 Lifecycle & state machine
- **Tốt:** mọi transition của task đều đi qua `Store.transition` → `check_transition` (`state.py:659-672`, `models.py:38-81`). Mỗi worktree chỉ có một writer, và điều đó được kiểm trong transaction (`state.py:683`).
- **Gap:** human gate nằm ở caller (`service.py:758`), không nằm trong state machine. `TRANSITIONS` cho phép BRIDGE đi APPROVED→DONE với mọi task (`models.py:62`). C/New.
- **Gap:** workflow không có state machine và không có owner CAS (`state.py:561-570`). Hai tiến trình có thể cùng drive một workflow. C/Known (FUN-04, ARC-02).
- **Gap:** agent báo `status: blocked` vẫn bị đưa vào REWORK (`models.py:114-126`), rồi `_drive` tự resume. Vòng đó đốt tối đa `max_attempts-1` lượt executor + reviewer. FAILED do timeout cũng bị resume tự động với cùng timeout. C/New.
- **Gap:** `_assert_gates` chạy 2 lần khi approve (`service.py:755`, rồi `:838` trong `_close`). Nếu lần sau fail thì task kẹt ở APPROVED và không surface nào gợi ý hành động. C/New.

### 3.2 Isolation & verification
- Sandbox verification là Seatbelt `(allow default)(deny file-write*)…`, chỉ chặn đọc Keychains (`sandbox.py:94-112`). Mặc định `verify_sandbox: auto` vẫn cho network. Landlock chỉ giới hạn ghi. → Test code do agent viết **đọc được** account homes (nơi các CLI lưu login), rồi gửi ra ngoài được. `.ai/orchestrator.yaml` không chứa token: account chỉ tham chiếu token qua tên biến môi trường (`token_env`) hoặc Keychain. C (cấu hình) / P (exfil).
- `read_deny` (account homes) chỉ áp cho adapter Claude. Codex chỉ có sandbox riêng của nó, và Codex có đọc được home của account khác hay không thì chưa probe. U/Known (AUTH-05).
- Bằng chứng review gắn theo **từng attempt**: baseline chụp lại mỗi attempt (`runner.py:147-148`), còn `touched`, `after_hashes`, `touches_sensitive` chỉ tính trên attempt cuối (`runner.py:298-301`). P/New (§4, F-02).
- Reviewer không thấy thay đổi do verification gây ra: `recheck` không trả patch (`review.py:350-362`), trong khi hash binding lại lấy nội dung sau verification. C/New.

### 3.3 Account / Provider / Model / Routing / Token
- **7 nguồn sự thật:**
  1. `orchestrator.yaml`
  2. `.ai/accounts.yaml`
  3. `config.json` (legacy, vẫn giữ `lock_dir`, binaries, sandbox, model, `max_attempts`)
  4. `~/.agent-bridge/accounts.json`
  5. `bridge.db` (bảng `accounts`, `account_quota`)
  6. `accounts.db`
  7. Keychain
- Account state được ghi 2 lần trong 2 transaction riêng (`state.py:338-350`). Khi đọc, bridge fallback về row cục bộ, nên một mark cũ có thể sống lại. C/New.
- **Provider** suy ra từ map adapter hardcode (`orchestrator.py:30-37`, `accounts.py:28,199,219,243`, `cli.py` `_PROVIDER_ADAPTER`). Thêm provider phải sửa ≥6 chỗ. Chấp nhận được với 4 adapter; chỉ gom lại khi thật sự thêm provider.
- **Model** chỉ là chuỗi trên agent. Codex còn có fallback `cfg.model` (`executor.py:410`), adapter khác thì không. C.
- **Routing:**
  - Bridge bind agent/account trước khi acquire slot. Binding chỉ được gỡ khi gặp `ConflictError`/`LimitExceeded` (`service.py:638-641,680-683`). Các `ValueError` từ `_start` (account unavailable, unscopable, hook settings; `runner.py:59-71`) để lại binding cũ. C/New.
  - `_quota_fallback` không kiểm `_unscopable` (`gate.py:319`). C/New.
  - Concurrency cap và `lock_dir` khai báo theo từng project (`service.py:139-141`, `config.py:43`). C/Planned (`multi-bridge-accounts.md` A3/A4).
- **Token:**
  - Env scrub, prefix check, đọc keychain qua stdout không qua argv, redact: tốt (`security.py:166-175`, `accounts.py:124-189`, `state.py:121`).
  - Agent Claude bật `allow_bash` đọc được token của chính nó qua env. Người khác cùng user cũng đọc được qua `ps eww`. U (CLI có scrub env cho tool con hay không thì chưa rõ). Known (`allow_bash` đã được ghi là "weaker" trong rules).
  - Keychain item của account không có home dùng key `anthropic:account:<name>` (`accounts.py:131`). Hai project trùng tên account sẽ dùng chung secret. C/New.

### 3.4 Concurrency & recovery
- **Tốt:**
  - Cancel: CANCELLED trước, runner kill process group trong ~1 s.
  - `kill_stale` dùng pid + start time.
  - Slot fd truyền sang agent qua `pass_fds`, nên runner chết thì slot vẫn bị giữ đến khi agent chết (REL-01, đã sửa).
- **Gap:** `_consult` (reviewer, planner) không ghi pid. `ACTIVE = {RUNNING, VERIFYING}` (`models.py:35`) nên `recover()` (`service.py:1135`) bỏ qua REVIEW. Reviewer mồ côi tiếp tục đốt quota và giữ slot đến timeout; task REVIEW không bao giờ được review lại. C/New.
- **Gap:** run bị interrupt được hoàn attempt (`service.py:1163`), nên một task làm bridge crash liên tục sẽ retry vô hạn. C/New.
- **Gap:** slot bị giữ suốt verification (tối đa 10 × 900 s) dù lúc đó không có model nào chạy (`runner.py:127` vs `:278`). C/New.
- **Gap:** git, sqlite và `ps` chạy đồng bộ trên event loop: `snapshot`/`diff` trong runner, `kill_stale` có `time.sleep`, `health` gọi subprocess 20 s. Mọi MCP call bị treo trong lúc bridge chấm một run. C/New.

### 3.5 Centralized storage
- Bố cục và key: §1. Key là path hash, nên move hay rename repo đều ra store rỗng một cách im lặng. Chỉ có `relink` thủ công cứu được, và nó từ chối nếu path mới đã có DB (`storage.py:144-145`). C/Known (đã bàn trong `2026-11-02-centralized-storage-audit.md`).
- `storage.migrate` làm tốt: lock, kiểm "không có bridge đang chạy", SQLite backup API, fingerprint, `os.replace`, idempotent, có rollback (`storage.py:289-358`).
- **Gap:** còn 2 đường migrate ngầm không có guard:
  - `Bridge.__init__` `os.replace` `.ai/bridge.db` cùng `-wal`/`-shm` trong khi tiến trình khác có thể đang giữ WAL (`service.py:95-104`). Đích lại là vị trí legacy trong git-dir, nên sau đó phải migrate thêm lần nữa.
  - `install.bridge_dir` đổi tên `codex-bridge` → `agent-bridge` như một side effect (`install.py:48-53`).
  - `plan()` chỉ lấy nguồn legacy đầu tiên (`storage.py:245-247`).
  - C/New.
- **Gap:** `registry_dir()` cứng `~/.agent-bridge` (`registry.py:17-18`). Đặt `AGENT_BRIDGE_HOME` thì storage bị tách làm hai, và registry bỏ qua kiểm symlink/owner. C/Known (CFG-05).
- **Gap:** `accounts.json` ghi không lock, dùng temp file tên cố định (`accounts.py:281-286`). C/New.
- **Gap:** worktree mới hoặc clone mới không có `orchestrator.yaml` (`.ai/.gitignore` sinh ra ở `config.py:122` chỉ giữ `config.json`). Bridge **lặng lẽ** rơi về legacy mode và Codex làm executor (`orchestrator.py:476-477`). File không chứa token nhưng có đường dẫn account home theo từng máy, nên commit nó hay không là quyết định của người dùng. Tối thiểu nên báo rõ khi chạy legacy, hoặc yêu cầu opt-in. C/New.
- **Gap:** retention: `gc` chỉ xoá log jsonl. Store mồ côi, events, tasks không bao giờ được dọn (`storage.py:368-408`). Không có schema version. C/Known (ARC-03).

### 3.6 Nhất quán CLI / TUI / Web / MCP

| Thao tác | MCP | CLI | TUI | Web |
|---|---|---|---|---|
| create / plan / run workflow / health | ✓ | ✓ | – | – |
| execute | ✓ (bg) | ✓ (wait) | – | ✓ (CLI subprocess) |
| resume | ✓ | ✓ (`recover()` trước) | – | ✓ (từ chối RUNNING → nút resume cho `runner_dead` không chạy) |
| review / cancel | ✓ | ✓ | ✓ | ✓ (ghi actor = CLIENT, không phải HUMAN) |
| human approve / reassign | – (đúng) | tty + gõ lại id | modal Yes/No (yếu hơn CLI) | – (đúng) |
| account login/add/remove | – | tty gate | qua CLI | – |
| account-clear | – | **không tty gate** | ✓ | – |

- Human-only gate đứng vững trên MCP và Web (`webactions.py:15-20`). Web bind `127.0.0.1`, kiểm Host/Origin, dùng launch code → cookie `SameSite=Strict`, bắt POST JSON. Tuy vậy action **bật mặc định** (`webui.py:198`).
- Có 3 bộ quy tắc "needs you": `render.ATTENTION` (`render.py:21`), `progress.py:100-104`, `app.js:307`. Hai helper next-command (`progress._command:184`, `render._next_for:237`) cho kết quả khác nhau. Có 3 nguồn quota (`service.py:193`, `progress.py:424`, `progress.py:536`). Default `max_attempts`/timeout bị copy cứng (`progress.py:268-279,704`). C/Known một phần (UX-04..12 có nhắc thuật ngữ).

## 4. Security and Reliability Findings

| ID | Sev | Nhãn | Finding | Evidence | Fix tối thiểu |
|---|---|---|---|---|---|
| F-01 | **P0** | P/New | **Thoát sandbox qua git config.** Trong main checkout, `.git/` nằm trong vùng ghi được của verification. Test do agent viết có thể đặt `core.fsmonitor` hoặc clean filter vào `.git/config`. Sau verification, `recheck` → `diff` → `snapshot` chạy `git status` và `git add -A` (`review.py:54,70`) **ngoài sandbox, với `os.environ` đầy đủ** (`review.py:32-33`). Lệnh đó chạy trước khi `_git_internals` hash config để phát hiện tamper (`review.py:119-123`). Không có override `fsmonitor`/`hooksPath` nào trong `bridge/`. | `review.py:30-55,97-123,350-354`; writable = `str(root)`, không chừa `.git/` (`review.py:380`). Chỉ áp dụng cho main checkout; ở linked worktree, `.git` là file trỏ ra ngoài vùng ghi | Trước git call đầu tiên sau run và sau verify, đọc thẳng từ filesystem `.git/config`, `info/attributes`, hooks và so với baseline; có khác biệt thì BLOCK **mà không gọi git**. Phòng thủ bổ sung: `-c core.fsmonitor=false -c core.hooksPath=/dev/null`, `--no-ext-diff --no-textconv`, env đã scrub, Seatbelt deny-write `.git/`. Landlock chỉ grant, không deny được thư mục con, nên trên Linux phải dựa vào bước so sánh. Thêm repro test |
| F-02 | P1 | P/New | Review, content binding và kiểm sensitive path chỉ phủ attempt cuối. Ví dụ: attempt 1 ghi `migrations/x` rồi timeout; khi resume, file đó đã nằm trong baseline, nên task có thể auto-close mà không cần người duyệt. | `runner.py:147-148,247-257,298-301` | Diff, hash và `touches_sensitive` tính từ baseline của attempt 1. Latch `needs_human` ở mọi nhánh |
| F-03 | P1 | C/New | Verification đọc được account homes (login của các CLI). `auto` vẫn có network. | `sandbox.py:94-112`; `executor.py:463` | Seatbelt `(deny file-read* (subpath <home>))` cho `_account_homes()`, `homes/` của registry và các file login global. Cân nhắc default `strict` |
| F-04 | P1 | C/New | Reviewer và planner (`_consult`) không ghi pid. `recover()` bỏ qua REVIEW, nên process mồ côi đốt quota và giữ slot. | `service.py:720-735,1135-1141`; `models.py:35` | Ghi pid của consult vào task/workflow, rồi `kill_stale` trong `recover()` |
| F-05 | P1 | C/Known (CFG-05) | `registry_dir` bỏ qua `AGENT_BRIDGE_HOME`, nên storage bị tách đôi. | `registry.py:17-18` | `registry_dir = storage.home`, cộng migrate một lần |
| F-06 | P1 | C/New | Worktree hoặc clone không có `orchestrator.yaml` lặng lẽ chạy legacy mode (Codex executor). | `orchestrator.py:476-477`; `config.py:122` | Warn trong `health`/`config-check`. Tạo task ở legacy mode khi `.ai/` không có `config.json` thì báo lỗi |
| F-07 | P1 | C/Planned | Concurrency cap và `lock_dir` theo từng project. | `service.py:139-141`; `config.py:43` | Làm theo `multi-bridge-accounts.md` §4.1 |
| F-08 | P2 | C/New | `account-clear` (gỡ mark QUOTA/AUTH) không có tty gate; agent gọi qua Bash được. | `cli.py:53,467,554` | Thêm vào `HUMAN_ACCOUNT_CMDS` |
| F-09 | P2 | C/New | Human gate không nằm trong state machine. | `models.py:62`; `service.py:758` | `transition` từ chối APPROVED→DONE khi `needs_human` và actor ≠ HUMAN |
| F-10 | P2 | C/New | Binding route bị rò khi `_start` ném `ValueError`; fallback bỏ qua `_unscopable`. | `service.py:638-641`; `gate.py:319` | `_unroute` cho mọi exception khi `routed`; fallback dùng `_ineligible` |
| F-11 | P2 | C/New | Migrate ngầm trong `Bridge.__init__` không có guard và có thể chạm WAL đang mở. | `service.py:95-104` | Bỏ đi, để `storage.migrate` xử lý mọi nguồn legacy |
| F-12 | P2 | C/New | `accounts.json` ghi không lock, dùng temp file tên cố định. | `accounts.py:281-286` | Dùng `projects_lock` + temp file theo pid |
| F-13 | P2 | C/New | Run bị interrupt được hoàn attempt mà không có giới hạn. | `service.py:1163` | Đếm số lần interrupt; vượt N thì BLOCKED |
| F-14 | P2 | C/New | Thay đổi file bị ignore chỉ phát hiện qua size + mtime. | `review.py:130-140` | Hash các file ignore khớp pattern protected/sensitive |
| F-15 | P2 | C/New | Keychain key trùng nhau cho các account không có home. | `accounts.py:131` | Key kèm project key, hoặc bắt buộc account có home |
| F-16 | P2 | C/New | Web action bật mặc định và ghi actor CLIENT. | `webui.py:198`; `service.py:771` | `--actions` opt-in; truyền actor HUMAN khi qua web |

## 5. Performance & quota waste

| Nguồn lãng phí | Evidence | Fix |
|---|---|---|
| `read_quota` walk cả `sessions/` và parse tới 20 rollout trên **mỗi** lần gọi. Mỗi lần start hoặc `list_accounts` gọi nhiều lần (`_account_quota`, `_effective_account_state`, `_limit_report`, `_pick`, `_reserve`, vòng `slot_busy`) | `quota.py:79-120`; `service.py:194`; `gate.py:53,88` | Cache TTL 30 s theo `(home, mtime)` đặt ngay trong `read_quota` (`progress.py` đã có cache riêng, bỏ đi) |
| Blocking I/O trong async MCP tool | `service.py:422-428`; `server.py:50-66`; snapshot/diff trong runner | `asyncio.to_thread` |
| Agent `blocked` → REWORK → `_drive` resume tự động (tốn quota executor + reviewer) | `models.py:114-126`; `service.py:997,1046` | `blocked` → BLOCKED; bỏ FAILED khỏi `_actionable` |
| Executor được dặn tự chạy verification rồi bridge chạy lại toàn bộ | `executor.py:715` | Dặn executor chạy check nhanh/targeted; gate vẫn là bridge |
| Resume INTERRUPTED gửi prompt REWORK; retry vì lỗi network gửi lại cả prompt vào session đã resume | `service.py:671`; `runner.py:180,196` | Prompt "continue" ngắn khi đã có session |
| `task_result` trả patch của **mọi** attempt (mỗi patch tới 30 KB) vào context client | `service.py:896-900` | Mặc định chỉ trả attempt cuối; `attempt=n` để xem các attempt cũ |
| 3 snapshot đầy đủ mỗi attempt; patch tính trong `recheck` bị vứt | `runner.py:147,222`; `review.py:354` | Tái dùng snapshot sau run; giữ patch sau verification (cũng sửa được F2 ở §3.2) |
| Slot bị giữ trong lúc verification | `runner.py:127,278` | Release ngay sau `adapter.run` |
| Web poll 3 s đọc lại toàn bộ events/tasks/attempts mỗi khi DB đổi và load YAML không cache; TUI chạy query không giới hạn mỗi 3 s | `progress.py:157-160,492`; `tui.py:344,369-371` | Giới hạn cửa sổ events; cache config theo mtime; một query cho ATTENTION |
| Callback cancel đọc DB mỗi 100 ms khi verify | `review.py:425` | 1 s |

## 6. Prioritized improvements

**P0**
1. F-01: so `.git/config`, attributes và hooks trực tiếp trên filesystem trước mọi git call sau run; giữ `-c` overrides làm phòng thủ bổ sung; thêm repro test.

**P1**

2. F-03: Seatbelt deny đọc account homes và file login global; đánh giá chuyển default sang `strict`.
3. F-02: gắn evidence theo baseline của attempt đầu; latch `needs_human`. Viết repro trước để xác nhận.
4. F-04: theo dõi pid của `_consult` và recover task REVIEW.
5. F-06: báo rõ khi chạy legacy fallback.
6. F-05: `registry_dir = storage.home` (CFG-05).
7. F-07: thực hiện `multi-bridge-accounts.md` task 1–3, theo thứ tự đã chốt.
8. Perf: cache `read_quota`; `to_thread` cho health, list_accounts và snapshot/diff.

**P2**

9. Rẻ, an toàn: F-08, F-09, F-10, F-12, F-13; agent `blocked` → BLOCKED.
10. Một module `attention()`/`next_command()` dùng chung cho render, progress và tui; JS chỉ hiển thị.
11. Bỏ migrate ngầm (F-11); gom 2 lần ghi account state về `accounts.db`; `PRAGMA user_version` khi có migration đầu tiên không chỉ thêm cột (ARC-03, YAGNI tới lúc đó).
12. Giảm token: `task_result` chỉ trả attempt cuối, prompt resume ngắn, giữ patch sau verification.
13. Dọn dẹp khi chạm tới: xoá delegate trong `service.py` (patch `gate`/`runner` trong test), `progress._timeout` (không có caller), `_NAME` regex định nghĩa 3 lần, alias `Account.codex_home`.

**Không đề xuất** (thiếu lợi ích đã chứng minh):
- Daemon scheduler toàn máy: `flock` + `accounts.db` đã đủ đúng (`multi-bridge-accounts.md` §7).
- Lớp "Provider plugin" cho 4 adapter.
- Tách `service.py` thành nhiều service mới. Ưu tiên xoá delegate và để dòng gọi thẳng `gate`/`runner`.
- Tự ý un-ignore `orchestrator.yaml`. File không chứa token (schema `extra="forbid"`, `token_env` chỉ là tên biến), nhưng có đường dẫn home theo từng máy, nên việc commit nó do người dùng quyết định.

## 7. Minimal migration & implementation plan

Mỗi bước là một bridge task (≤ ~400 dòng, `scripts/check.sh` là gate), PR riêng vào `main`.

| # | Bước | Risk | Phụ thuộc | Acceptance |
|---|---|---|---|---|
| 1 | Repro F-01 và F-02 thành test (expected-fail) | high | – | Test cho thấy fsmonitor chạy ngoài sandbox, hoặc chứng minh không chạy; tương tự cho auto-close nhiều attempt |
| 2 | F-01 fix | high (`risk_level: high`) | 1 | Test bước 1 pass; full suite xanh |
| 3 | F-03 Seatbelt read-deny account homes + file login global | high | – | Test Seatbelt: verification đọc `<home>/auth.json` thì fail (chạy ngoài sandbox lồng nhau) |
| 4 | F-02 baseline attempt-1 + latch `needs_human` | high | 1 | Repro pass; `test_approval_binding` giữ nguyên assertion |
| 5 | F-04 pid cho consult + recover REVIEW | medium | – | Kill server lúc đang review → lần start sau process reviewer bị kill, task vẫn có thể review lại |
| 6 | Perf: cache `read_quota`, `to_thread` | low | – | Đo `list_accounts` trước/sau trên máy thật; MCP không treo khi `health` chạy |
| 7 | F-05 registry theo `AGENT_BRIDGE_HOME`; migrate một lần: chỉ copy `accounts.json`/`projects.json` khi đích chưa có, **không đụng `homes/`** (giữ login hiện có) | medium | – | Test với `AGENT_BRIDGE_HOME` tạm; registry mặc định không đổi |
| 8 | F-06, F-08, F-09, F-10, F-12, F-13, `blocked`→BLOCKED | low–medium | – | Mỗi fix một test |
| 9 | `multi-bridge-accounts.md` task 1–4 | theo plan | 8 | Theo §6 của plan đó |
| 10 | Gom presentation rules + giảm token (§6 mục 10, 12) | low | – | Snapshot test: TUI và web cho cùng số "needs you" |
| 11 | F-11 + dọn dead code | low | 7 | `storage.migrate` xử lý cả 3 nguồn legacy; test migrate hiện có giữ nguyên |

Bước 1–4 cần human approve (`agent-bridge approve <id>`) vì chạm vào security gate.

## 8. Trạng thái triển khai (branch `feat/architecture-audit-fixes`)

Mỗi dòng là một bridge task do executor viết, được bridge kiểm chứng bằng full suite và review trước khi commit.

| Finding | Task | Commit | Bằng chứng |
|---|---|---|---|
| F-01 (P0) | T0127 | `afe7fad` | `tests/integration/test_git_config_tamper.py`. Đã kiểm thủ công trên git 2.50: `git status` chạy script `core.fsmonitor`, còn `GIT_CONFIG_*` tắt được nó |
| Perf (`read_quota`, `to_thread`) | T0128 | `4578930` | `tests/unit/test_quota.py`, `tests/unit/test_server_threads.py` |
| F-05, F-12 | T0129 | `acfe253` | `tests/unit/test_registry_home.py` |
| F-03 | T0130 | `85f0f55` | Test Seatbelt thật, chạy trên host. Commit message gọi `.ai/*.yaml` là "token files"; thực tế các file đó không chứa token, chặn đọc chỉ là phòng thủ thêm |
| F-02 | T0131 | `08713ee` | `tests/integration/test_attempt_evidence.py` |
| F-04 + attempt bị interrupt | T0132 | `4ed390b` | `tests/integration/test_consult_recovery.py` |

Chưa làm:

| Finding | Lý do |
|---|---|
| F-06 | Còn mở. Đã có thiết kế ở §3.5; cần chọn giữa cảnh báo hay bắt opt-in |
| F-08 | Không đổi. `account-clear` được QA e2e và quy trình MCP client gọi không cần TTY (`tests/qa/test_e2e_failures.py:30`); khoá nó thì vỡ luồng đó, mà lợi ích bảo mật thấp |
| F-09, F-10, F-13, agent `blocked` → không auto-resume | Còn mở, P2 |
| F-07 | Theo `docs/plans/multi-bridge-accounts.md` |
| F-11, F-14..F-16, gom presentation rules, giảm token trong `task_result` | Hoãn: P2, refactor hoặc đổi contract |
