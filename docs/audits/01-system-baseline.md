# Phase 1 — System Discovery & Baseline

## Phạm vi, phương pháp, môi trường

- **Revision:** `main` @ `0541153` ("drop the duplicate result command and the preselected approve in the drawer"), audit ngày 2026-11-08.
- **Working tree lúc bắt đầu (không phải thuộc tính của `main`):** người dùng có thay đổi chưa commit ở `.claude/settings.json`, `CLAUDE.md`, `README.md`, `README.vi.md` và file untracked `docs/plans/multi-bridge-accounts.md`. Audit không chạm vào các file này. Một điểm liên quan đến approval integrity: bản local của `.claude/settings.json` **bỏ** rule deny `Bash(*agent-bridge approve*)` (vẫn giữ `Bash(*-m bridge approve*)`), còn `CLAUDE.md` local cho phép Claude chạy `agent-bridge approve <id>` khi người dùng yêu cầu rõ ràng. Đây là cấu hình cục bộ, chưa có trên `main` (xem Phase 4).
- **Môi trường:** macOS 26.5, Apple M2 (8 core), Python 3.12.11 (`.venv` của dự án), git 2.50.1, Node 26.12.0, Playwright có cài (global), axe-core không có. Khoá phụ thuộc: `mcp 2.3.0`, `pyyaml 6.0.3`, `textual 8.2.8`, `pydantic 2.14.1`, `starlette 1.9.0`, `uvicorn 0.54.0`. CLI thật đã cài: `codex-cli 0.166.0`, `Claude Code 2.1.319`, `opencode`, `agy` (chỉ đọc `--version`/`--help`, không chạy tác vụ).
- **Cô lập:** mọi lần chạy runtime dùng `HOME`, `AGENT_BRIDGE_HOME` riêng trong scratchpad, `tests/fakes` đứng đầu `PATH` (đã xác nhận `command -v codex` → `tests/fakes/codex`) và một git repo dùng một lần. Không gọi tool MCP `agent-bridge` của phiên này, không chạm DB thật.
- **BLOCKED:** `tests/e2e/test_real_*` (`CODEX_SMOKE`, `CLAUDE_SMOKE`, `OPENCODE_SMOKE`, `AGY_SMOKE`) và `scripts/check.sh --with-health` trên cấu hình thật, vì sẽ dùng account/quota thật. Landlock tests (Linux-only) không chạy được trên macOS.

## 1. Cấu trúc hệ thống

### Module (`bridge/`, ~8.6k dòng Python + ~2k dòng JS/CSS/HTML)

| Lớp | Module | Vai trò (từ docstring + import thực tế) |
|---|---|---|
| Entry points | `cli.py` (481), `__main__.py`, `server.py` (206), `tui.py` (726), `webui.py` (217) | CLI `agent-bridge`, MCP stdio server, Textual TUI, HTTP dashboard localhost |
| Service | `service.py` (1102) | "the one orchestration layer behind both the MCP server and the CLI" |
| Thực thi | `runner.py` (374), `executor.py` (766), `review.py` (386), `sandbox.py` (83) | Chạy agent, adapter CLI (codex/claude/opencode/agy), thu evidence (snapshot hash, scope check, chạy lại verification), Landlock |
| Routing / account | `gate.py` (367), `accounts.py` (458), `quota.py` (281), `orchestrator.py` (411), `registry.py` (85) | Chọn agent/account, slot concurrency, limit/quota, cấu hình role |
| Persistence | `state.py` (752), `storage.py` (503), `models.py` (155) | SQLite (task/attempt/event), vị trí state, state machine |
| View | `progress.py` (638), `render.py` (296), `webactions.py` (123), `static/*` | Read model cho dashboard, render CLI, action web (gọi lại CLI qua subprocess) |
| Khác | `config.py`, `security.py`, `osutil.py`, `install.py` | Config legacy, path rule / redaction / env scrub, lock & process, `init`/`uninstall` |

Phụ thuộc vòng ở mức module: `service ↔ gate`, `service ↔ runner` (import trễ trong hàm). Chi tiết coupling ở Phase 2 (ARC-01).

### Entry points và bề mặt giao tiếp

- **CLI:** 31 subcommand (`agent-bridge --help`), trong đó 6 lệnh ghi rõ "HUMAN ONLY": `config-init`, `account-login`, `account-add`, `account-remove`, `approve`, `reassign`. Kiểm tra "human" là `sys.stdin.isatty() and sys.stdout.isatty()` + gõ lại task id (`cli.py:30-35`).
- **MCP (stdio):** 15+ tool `bridge_*` (`server.py:46-206`): accounts/usage/agents/health, create/execute/status/result/review/resume/cancel/list task, plan/run_workflow/workflow_status. Không có tool approve/reassign/account-*.
- **TUI:** `agent-bridge` không subcommand, chỉ khi có TTY (`cli.py:442-447`).
- **Web:** `agent-bridge web [--port] [--open] [--read-only]`, `127.0.0.1`, link một lần (hết hạn 600 s, `webui.py:130`), cookie phiên idle 12 h (`SESSION_IDLE = 43200`). API JSON `/api/overview`, `/api/projects/<id>/{tasks,requests}/…`, log, và `POST …/actions` (review/execute/resume/cancel), thực thi bằng subprocess CLI (`webactions.py`).
- **Database:** `~/.agent-bridge/projects/<project>/worktrees/<worktree>/bridge.db` (task, attempt, event), `~/.agent-bridge/accounts.db` (trạng thái account, slot, usage ledger), `accounts.json` (registry account máy), `projects.json`. `AGENT_BRIDGE_HOME` ghi đè (`storage.py:21-26`).

### Luồng dữ liệu chính

```
Claude Code (MCP client) ──stdio──> server.py ─> Bridge (service.py)
  create_task: validate spec (security.validate_pattern) ─> Store.insert (BACKLOG/READY)
  execute_task: gate._route (role→agent→account, slot/limit) ─> Store.transition RUNNING
     └─ TaskRunner: snapshot ─> adapter argv (codex exec / claude -p …) trong process group riêng,
        env đã scrub, account home riêng ─> JSONL events ─> RunResult
     └─ VERIFYING: diff snapshot, scope_violations, HEAD unchanged, chạy lại verification_commands
        (Landlock nếu có) ─> REVIEW | REWORK | PAUSED | BLOCKED
  review_task (client hoặc reviewer agent read-only) ─> APPROVED ─> DONE
     (high-risk / sensitive path: dừng ở APPROVED, người chạy `agent-bridge approve` ở TTY)
Web/TUI/CLI views ─> progress.py (SQLite mode=ro) ; web actions ─> subprocess CLI
```

### Trust boundaries (tóm tắt; mô hình đầy đủ ở Phase 4)

1. Người dùng ↔ phiên Claude Code (MCP client): client được tin để plan/review, không được approve high-risk.
2. Bridge ↔ agent CLI (executor/reviewer có thể bị prompt-injection): ranh giới thực thi bằng sandbox Codex (OS), quy tắc quyền Claude CLI (không có OS sandbox), scope check sau khi chạy.
3. Bridge ↔ verification commands: chạy code do executor viết. Trên Linux có Landlock; **trên macOS chạy không bị giới hạn** (đã quan sát: `health` → `"verify_sandbox": "unavailable on this system: checks run unconfined (auto)"`, `ok: true` kèm warning `verify_unconfined`).
4. Browser ↔ web dashboard: cookie + Origin/Sec-Fetch-Site + Host check.
5. Cùng user: mọi tiến trình chạy cùng UID, README tự nhận human approval "không phải boundary với code chạy dưới user của bạn".

## 2. Feature inventory (đã quan sát thực tế ở mức smoke)

| Tính năng | Bằng chứng runtime ở phase này | Phase kiểm sâu |
|---|---|---|
| `init` vào repo mới | rc=0, ghi `.ai/.gitignore`, `.claude/rules/*`, `.claude/settings.json`, skill, `.mcp.json`, `CLAUDE.md`. Thông báo dùng chữ "updated" cả khi file mới tạo (nhỏ, UX) | 6 |
| `config-check` / `agents` không có `orchestrator.yaml` | rc=0, `source: legacy .ai/config.json`, role: planner/reviewer/advisor=`client`, executor=`default`, stages `execute,test,review` | 3 |
| `health` | rc=0, `ok: true`, fake codex "Logged in using ChatGPT (fake)", quota `UNKNOWN`, warning verify unconfined; dùng DB `:memory:` khi project chưa có state (`service.py:104-107`, cố ý: không tạo state khi chỉ xem) | 3, 5 |
| `account-list` / `usage` | account `default` (openai, `isolated: false`, `source: project`), spend day/week/month = 0, quota UNKNOWN; liệt kê provider chưa cấu hình kèm gợi ý `account-add` | 3 |
| `storage status` | Liệt kê tiến trình bridge đang chạy **trên toàn máy** (thấy cả `agent-bridge serve` thật của người dùng) — thông tin chỉ đọc | 4 |
| Vòng đời task, workflow, routing, cancel, recover | Kiểm ở Phase 2 bằng `Bridge` API + fake CLI | 2 |
| Web dashboard, TUI | Kiểm ở Phase 6; bộ e2e Playwright chạy ở mục 3 | 6 |

## 3. Baseline tests

| Bộ | Lệnh | Kết quả | Thời gian |
|---|---|---|---|
| Unit + integration + e2e (fake) | `.venv/bin/python -m pytest -q -rs` (addopts của repo) | **PASS: 605 passed, 114 skipped**, 0 failed | 252.6 s |
| QA end-to-end (CLI thật của bridge, MCP stdio, dashboard trong Chromium, fake agent CLI, HOME tạm mỗi test) | `QA_E2E=1 NODE_PATH=/opt/homebrew/lib/node_modules .venv/bin/python -m pytest -q -rs tests/qa --basetemp=<scratch>` | **PASS: 104 passed, 2 skipped** | 496.3 s |
| Real CLI smokes | `CODEX_SMOKE=1 …` v.v. | **BLOCKED** (tốn quota thật) | — |
| Landlock | `tests/integration/test_verify_sandbox.py` (3), `tests/qa/test_e2e_security.py:154` | **SKIPPED** (macOS, không có Landlock) | — |
| axe-core a11y | `tests/qa/test_e2e_web.py:219` | **SKIPPED** (axe-core không có) | — |

Phân rã 114 skip của bộ chính: 5 real-CLI opt-in, 3 Landlock, 106 là bộ `tests/qa` bị tắt khi không có `QA_E2E=1` (đã chạy riêng ở dòng 2). Như vậy **trên macOS, toàn bộ nhánh verification sandbox chưa từng được runtime-verify**; CI có job `ubuntu-latest` chạy cả hai bộ (`.github/workflows/ci.yml`), nhưng kết quả CI không được kiểm trong audit này.

## 4. Đối chiếu tài liệu ↔ implementation (các điểm đã có bằng chứng)

| Tuyên bố trong README / rules | Thực tế | Trạng thái |
|---|---|---|
| "Environment failures (PAUSED) do not consume the attempt budget" (`.claude/rules/orchestration.md`) | Runner bị kill → `recover()` → PAUSED (INTERRUPTED) nhưng attempt **không** được trả lại; với `max_attempts=1`, resume ra `BLOCKED` mà agent không chạy lại | **Sai lệch** (FUN-01, Phase 2) |
| Workflow "INCOMPLETE (some task BLOCKED or CANCELLED)" | Dependency bị CANCELLED/BLOCKED → task con kẹt BACKLOG, workflow báo `WAITING` với `waiting: {}` | **Sai lệch** (FUN-02) |
| Dashboard "refreshes every 3 s" | `app.js:1352` `setTimeout(poll, disconnected ? 5000 : 3000)`, dừng khi tab ẩn | Đúng |
| One-time link hết hạn sau 10 phút | `webui.py:130` `time.monotonic() - started < 600` | Đúng |
| Flag CLI mà bridge truyền cho agent | Mọi flag trong `executor.py` (`--json`, `--output-schema`, `--ignore-user-config`, `-s`, `-o`; `--strict-mcp-config`, `--setting-sources`, `--permission-mode`, `--allowedTools`, `--disallowedTools`, `--output-format`, `--input-format`, `--json-schema`, `--disable-slash-commands`, `--resume`, `--settings`) đều có trong `--help` của `codex-cli 0.166.0` và `Claude Code 2.1.319`. `codex exec resume` không có `--sandbox`; bridge dùng `-c sandbox_mode=…` khi resume (`executor.py:392-395`) | Đúng về cú pháp; việc Codex tôn trọng `-c sandbox_mode` khi resume **chưa kiểm chứng runtime** |
| "Verification … On Linux with Landlock …; elsewhere unconfined" | `health` trên macOS xác nhận unconfined, `ok: true` | Đúng như tài liệu, nhưng là rủi ro nền tảng (Phase 4) |
| "Nothing the agent or a check started outlives it", cancel ~1 s | Cancel từ tiến trình khác hoàn tất trong 0.61 s (Phase 2) | Đúng ở mẫu đo |

Các sai lệch tài liệu khác (settings, auth, bảo mật, UX) được ghi trong báo cáo phase tương ứng.

## 5. Kết luận baseline

- Hệ thống chạy được trong môi trường cô lập, test suite xanh trên macOS (605 + 104 test).
- Hai sai lệch hành vi so với tài liệu đã được tái hiện (FUN-01, FUN-02).
- Vùng thiếu bằng chứng runtime trên host này: Landlock, CLI thật (Codex sandbox, Claude permission thực thi), CI.
- Verdict baseline: **NEEDS IMPROVEMENT** (chạy ổn định, nhưng có sai lệch tài liệu và vùng chưa kiểm chứng trên macOS).
