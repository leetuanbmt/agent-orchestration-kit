# Master Audit Report — agent-orchestration-kit

Revision `main` @ `0541153`, audit độc lập ngày 2026-11-08 trên macOS 26.5 / Apple M2 / Python 3.12.11. Chế độ: chỉ audit, không sửa hệ thống. Báo cáo chi tiết:
[01 baseline](01-system-baseline.md) · [02 kiến trúc & chức năng](02-architecture-functionality.md) · [03 settings, auth, provider](03-settings-auth-providers.md) · [04 bảo mật](04-security-assessment.md) · [05 hiệu năng & tin cậy](05-performance-reliability.md) · [06 UX/UI](06-product-ux-ui.md) · [07 bằng chứng test](07-test-evidence.md) · [08 roadmap](08-improvement-roadmap.md).

## 1. Executive summary

`agent-orchestration-kit` là một công cụ local có nền móng tốt: một service layer duy nhất phía sau CLI, MCP, TUI và web; state machine khai báo tập trung và được ép trong cùng transaction với audit event; slot concurrency và budget ledger đúng dưới tranh chấp nhiều tiến trình; test suite rộng, ít mock và xanh (605 + 104 test). README thành thật về phần lớn giới hạn.

Điểm yếu tập trung ở **ranh giới tin cậy giữa bridge và mã do agent viết**:

- Trên macOS (nền tảng chính của tác giả), lệnh verification chạy mã của executor **không có sandbox** theo mặc định. Đã tái hiện: mã đó ghi ra ngoài worktree và giả được một lần human approve.
- Action trên dashboard web chạy `python -m bridge` từ thư mục project, nên một gói `bridge/` do agent để lại trong worktree sẽ được thực thi.
- Config an ninh bị agent sửa được phát hiện nhưng vẫn được nạp ở lần khởi động sau.
- Approve không gắn với `HEAD` đã verify.
- Phục hồi sau khi runner chết sai tài liệu: attempt bị trừ, và agent mồ côi sống tiếp trong khi slot đã nhả.

**Overall verdict: NEEDS IMPROVEMENT; Security HIGH RISK.** Không có lỗi Critical. Có 5 lỗi High: SEC-01, SEC-02, CFG-01, AUTH-01, UX-01. Tổng cộng 72 finding: 5 High, 31 Medium, 36 Low.

## 2. System health theo lĩnh vực

| Lĩnh vực | Verdict | Điểm mạnh đã kiểm chứng | Điểm yếu chính |
|---|---|---|---|
| Architecture | NEEDS IMPROVEMENT | Mọi bề mặt dùng chung `Bridge`; web action đi qua CLI | Coupling hai chiều giữa các private method (ARC-01); schema không có version (ARC-03) |
| Functionality | NEEDS IMPROVEMENT | Transition bất hợp lệ bị chặn; budget → BLOCKED; cancel liên tiến trình mất 0.61 s; high-risk dừng ở APPROVED | FUN-01 (recover trừ attempt), FUN-02 (workflow kẹt ở WAITING) |
| Settings | NEEDS IMPROVEMENT | Validate bằng pydantic, `config-check` | CFG-01 (danh sách bảo vệ không có mức sàn), CFG-05 (`AGENT_BRIDGE_HOME` không dời registry) |
| Authentication | NEEDS IMPROVEMENT | Login subscription Claude/Codex cô lập theo home | AUTH-01 (`env_allow` toàn cục lan sang mọi adapter và verification), AUTH-02 |
| Security | **HIGH RISK** | Web: Host 421, Origin 403, cookie `HttpOnly; SameSite=Strict`, CSP, không có `innerHTML`; `pip-audit` 0 lỗ hổng | SEC-01, SEC-02 (P0); SEC-04, SEC-07 |
| Performance | NEEDS IMPROVEMENT | CLI 0.26–0.6 s, không đổi theo số task; TUI refresh 11–30 ms | `/api/overview` p50 758 ms, payload 504 KiB, poll mỗi 3 s (PERF-01) |
| Reliability | NEEDS IMPROVEMENT | 6 tiến trình tranh 2 slot: tối đa đúng 2; ledger 16 tiến trình chi đúng $100/$100; timeout kill cả group | REL-01/02 (agent mồ côi), REL-04 (DB hỏng thì in traceback) |
| UX/UI | NEEDS IMPROVEMENT | axe-core 0 vi phạm trên 24 lượt quét; 390 px không tràn ngang; latency < 100 ms với 305 task | UX-01 (bàn phím), UX-02/03/04 (số liệu lệch giữa các bề mặt) |
| Testing | NEEDS IMPROVEMENT | 719 item, 0 fail; bắt được 6/7 mutant guard đơn | TEST-01 (không có test cho bất biến), TEST-03 (e2e bỏ sót UX-01), TEST-02 (không có timeout) |

## 3. Top findings

| ID | Sev / Conf / Pri | Vấn đề | Bằng chứng chính |
|---|---|---|---|
| SEC-01 | High / Confirmed / P0 | Verification chạy mã executor không có sandbox trên macOS; ghi được ra ngoài worktree; giả được `human_approve` của task khác | `health`: `"verify_sandbox": "unavailable on this system: checks run unconfined (auto)"`, `ok: true`. Repro: task high-risk thành DONE với event `actor=human {'tty': True}` |
| SEC-02 | High / Confirmed / P0 | Action web thực thi gói `bridge/` nằm trong worktree | `webactions.py:104` `[sys.executable, "-m", "bridge", …]`, cwd = root. Repro: resume trên web trả 202 và payload chạy |
| CFG-01 | High / Confirmed / P1 | `protected_paths`, `env_deny`, `verify_allowlist` thay thế hoàn toàn được, không có mức sàn | Repro: `.ai/**`, `.git/hooks/**`, `.mcp.json` được nhận làm `allowed_paths` |
| AUTH-01 | High / Confirmed / P1 | Cho phép một API key qua `env_allow` thì mọi adapter và lệnh verification đều nhận key đó | Repro envdump: `ANTHROPIC_API_KEY` xuất hiện trong `codex exec` và verification |
| UX-01 | High / Confirmed / P1 | Enter/Space trên hàng task không mở drawer sau khi poll cập nhật DOM (WCAG 2.1.1) | `app.js:249-250` chép `onkeydown`; closure `app.js:322-324` so `event.target === row` với node đã bị bỏ |
| SEC-04 | Medium / Confirmed / P1 | Sửa `.ai/config.json`/`orchestrator.yaml` thì task bị BLOCKED nhưng file vẫn còn; lần khởi động sau nạp `protected_paths=[]` | Repro Phase 4; `health` báo `protected_dirty: []` với file yaml bị git ignore |
| SEC-07 | Medium / Confirmed / P1 | Approve không so `HEAD` hay file mới sau khi verify | `verified_head` chỉ được ghi ở `runner.py:293`, không chỗ nào đọc |
| FUN-01 | Medium / Confirmed / P1 | Runner chết → PAUSED nhưng không trả attempt (trái rule "PAUSED không tốn budget") | `service.py:1047-1050` khác `runner.py:330-338`. Với `max_attempts=1`, resume ra BLOCKED mà agent không chạy lại |
| REL-01/02 | Medium / Confirmed / P1 | Sau `kill -9`, agent mồ côi vẫn chạy, slot đã nhả, run thứ hai trên cùng account khởi động được; `kill_stale` chỉ gửi SIGTERM | `executor.py:677` `kill_group(pid, force=False)`; repro Phase 5 |
| FUN-02 | Medium / Confirmed / P1 | Dependency bị CANCELLED/BLOCKED thì task con kẹt ở BACKLOG, workflow báo WAITING | Repro Phase 2: `waiting: {}` |
| PERF-01/02 | Medium / Confirmed / P1 | Dashboard dựng lại mọi project và chạy git trong mỗi request | c=1: p50 758 / p95 1056 / p99 1162 ms; c=10: p50 3160 ms, 3 req/s |
| UX-02/03 | Medium / Confirmed / P1 | Web báo "No quota reported yet" trong khi CLI thấy 62%; danh sách bị cắt im lặng ở 200 task | TUI "Tasks (200)" khi có 265 task; `list --limit 500` trả về 200 |
| BILL-01 | Medium / Confirmed / P1 | `total_cost_usd` (ước tính) bị ghi là `actual`; không phân biệt subscription với API | `executor.py:500-503`; `plan_type` đọc ở `quota.py:129-130` nhưng không dùng |
| SEC-09/10, AUTH-04 | Medium–Low / Confirmed / P1–P2 | Redact bỏ sót khoá JSON có nháy và nhiều tiền tố token; `*_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `SECRET_KEY` lọt qua bộ lọc env | Repro Phase 3/4 |
| TEST-01/03 | Medium / Confirmed / P1 | Suite xanh dù các lỗi trên tồn tại; e2e chỉ thử Enter ở lần render đầu | `tests/qa/web/dashboard.cjs:764-768` |

## 4. Authentication & Settings Support Matrix (tóm tắt; đầy đủ ở Phase 3 §1.2 và §2)

| Phương thức | Hỗ trợ | Ghi chú quyết định |
|---|---|---|
| Claude subscription OAuth (`/login`) | **Supported** | `account-add/-login` trong `CLAUDE_CONFIG_DIR` riêng. Hết hạn → PAUSED (AUTH). Trên macOS cần Keychain |
| `claude setup-token` (`CLAUDE_CODE_OAUTH_TOKEN`) | **Partial, chỉ toàn cục** | Bị lọc theo `*_TOKEN`; chỉ qua được bằng `env_allow`, khi đó đè mọi account Claude |
| Anthropic API key | **Partial, chỉ toàn cục** | Bị lọc theo `*_API_KEY`; `env_allow` làm key lộ cho mọi adapter và verification (AUTH-01) |
| `apiKeyHelper` | **Unsupported** | Bị chặn bởi `--setting-sources ""` (AUTH-03) |
| Bedrock/Vertex, `ANTHROPIC_AUTH_TOKEN` | **Unsupported / Unverified** | Credential bị lọc nhưng biến định tuyến lọt qua (AUTH-04) |
| Codex ChatGPT login | **Supported** | `CODEX_HOME` riêng, token dạng file; quota đọc từ rollout |
| OpenAI API key | **Partial** | Env bị lọc; dùng được nếu người dùng tự chạy `codex login --with-api-key` trong home. Không phân biệt được với subscription |
| opencode | **Partial** | Chỉ có login global, adapter read-only, không cô lập account |
| agy / Google | **Partial** | Home là cả `HOME`; chỉ kiểm kích thước và mtime của token |
| Direct API adapter | **Unsupported** | Không có. Giá do người dùng nhập |

Settings gap (chính): danh sách bảo vệ không có mức sàn (CFG-01); `orchestrator.yaml` bị git ignore nên thay đổi làm yếu an toàn không để lại dấu vết (CFG-02); key legacy bị bỏ qua mà không cảnh báo (CFG-03); `config-check` không kiểm `home` (CFG-04); `AGENT_BRIDGE_HOME` không dời registry, trái tài liệu (CFG-05); không có lệnh in cấu hình hiệu lực (CFG-07). Thông tin chính sách của nhà cung cấp được trích từ tài liệu chính thức ở Phase 3, có URL và ngày truy cập. Các fact về Codex chỉ ở mức Likely vì trang chính thức chuyển hướng.

## 5. Security risks & trust boundaries

| Ranh giới | Đánh giá |
|---|---|
| Browser ↔ dashboard | GOOD cho xác thực, CSRF, Host/Origin, XSS. **HIGH RISK** cho phần thực thi action (SEC-02) |
| Bridge ↔ executor agent (Codex) | Seatbelt của Codex: INSUFFICIENT EVIDENCE trên host này (không chạy CLI thật) |
| Bridge ↔ executor agent (Claude) | Không có OS sandbox (đã ghi trong tài liệu); Bash không bị giới hạn đường dẫn |
| Bridge ↔ verification | **HIGH RISK trên macOS** (SEC-01). Landlock trên Linux mới chỉ được đọc mã |
| Scope & approval | NEEDS IMPROVEMENT (SEC-04, SEC-05, SEC-07, SEC-08) |
| Human gate ↔ mã cùng UID | Giới hạn có chủ đích, đã ghi trong tài liệu. TTY check vượt được bằng `script` (SEC-03) |
| Secrets (env/log) | NEEDS IMPROVEMENT (SEC-09, SEC-10, AUTH-01, AUTH-04) |
| Supply chain | GOOD (pip-audit 0). Action trong CI pin theo tag, `uv sync` không có `--locked` (SEC-13) |

Revalidation audit cũ (revision `3571546`): đã sửa SEC-06, SEC-10, REL-01, REL-02, REL-03 cũ. Bảy mục chỉ được sửa một phần và vẫn tái hiện bằng biến thể mới. Human gate (SEC-08 cũ) nay là SEC-03. Chạy lại tệp tái hiện cũ cho 10 FAIL và 1 PASS. PASS đó là PASS giả do test monkeypatch `_git`. Chi tiết ở Phase 4.

**Cấu hình cục bộ (chưa có trên `main`):** `.claude/settings.json` trong working tree đã bỏ rule deny `Bash(*agent-bridge approve*)` (vẫn giữ `*-m bridge approve*`), và `CLAUDE.md` local cho phép Claude chạy `approve` khi người dùng yêu cầu rõ. Kết hợp với SEC-03, rule deny này chỉ là hướng dẫn, không phải ranh giới. Người dùng nên xem lại trước khi commit.

## 6. Performance benchmarks & scalability limits (Phase 5)

| Phép đo (13 project, ~6.400 task, ~45.000 event) | Kết quả |
|---|---|
| `/api/overview` c=1 / c=10 | p50 758 / 3160 ms; p99 1162 / 4152 ms; 1,3 / 3,0 req/s; 504 KiB mỗi lần poll 3 s |
| Chi tiết task / log | 419–502 ms cho phản hồi khoảng 1 KiB (poll log mỗi 2 s) |
| RSS server web | 101 → 175 MiB, cache không evict (PERF-03) |
| CLI `list/status/usage/health` | 0,26–0,6 s, không đổi theo số task. `tasks --json` trên 13 project: 0,7–1,4 s |
| `create_task` | 0,59 ms ở 1.000 task, khoảng 3,6 ms ở 5.000–10.000 task (thiếu index, PERF-04) |
| Overhead dispatch | 0,4–2,4 s mỗi run (khoảng 29 lệnh git), không đáng kể so với agent thật |
| Cancel | 0,61–1,19 s với agent tôn trọng TERM; khoảng 6,5 s nếu agent bỏ qua TERM (REL-07) |
| Dashboard UX ở 305 task | Mở drawer khoảng 50 ms; lọc 10–62 ms; đổi project 30–94 ms |

Giới hạn quy mô quan sát được: dashboard web là điểm nghẽn đầu tiên khi số project tăng. Danh sách 200 task (UX-03) và cửa sổ 10.000 task của `recover()`/`health` (REL-03) là các trần cứng.

## 7. UX/UI findings & khuyến nghị

- **Defect chức năng:** UX-01 (bàn phím), UX-02 (quota không hiện), UX-03 (cắt 200 task im lặng).
- **Usability đã xác nhận:** UX-04 ("Needs you" ra 33/35/8 trên ba bề mặt cho cùng một project), UX-05 (link hết hạn hiện trang trống), UX-06 (form vẫn bật khi offline, lỗi "Failed to fetch" thô), UX-08 (lộ repr enum Python), UX-09 (trang cao 10–14 nghìn px), UX-11 (TUI ở 80×24).
- **Visual inconsistency:** UX-10 (thuật ngữ approve, run/execute, tên sản phẩm), UX-12.
- **Khuyến nghị:** sửa các defect trước (P1-7). Thống nhất một định nghĩa "Needs you" dùng chung cho `progress.py` và TUI. Không cần redesign: axe, responsive, theming và latency đều đạt.

## 8. Test results, coverage gaps, unverified risks

| Bộ | Kết quả |
|---|---|
| Suite chính | 605 passed, 114 skipped, 0 failed (252,6 s) |
| QA e2e (Chromium, MCP stdio, CLI thật của bridge, fake agent) | 104 passed, 2 skipped (496,3 s) |
| Mutation probe (8 mutant + đối chứng) | 6 bị bắt đúng chỗ. 1 mutant tương đương: kiểm tra `account_limit` là mã thừa. 1 mutant (bỏ rule one-writer) chỉ làm suite treo hơn 40 phút |
| Contract flag với CLI thật đã cài | PASS (codex-cli 0.166.0, Claude Code 2.1.319) |
| pip-audit | 0 lỗ hổng |
| Real-CLI smokes, Landlock, đo coverage dòng | BLOCKED |

**Chưa kiểm chứng (cần CLI thật hoặc Linux):**
- Seatbelt của Codex có chặn ghi `.ai/`, `.git/`, ghi qua symlink và `setsid` hay không.
- Deny `Read(...)` của Claude có áp cho Grep/Glob hay không.
- `-c sandbox_mode` khi `codex exec resume`.
- Định dạng `resetsAt` của Claude.
- Chuỗi lỗi billing thật của CLI.
- Landlock trên Linux.
- Kết quả CI.
- Quota failover end-to-end, độ công bằng của `slot_wait`, hành vi khi đĩa đầy.

## 9. Roadmap (chi tiết ở Phase 8)

| Ưu tiên | Mục | Effort |
|---|---|---|
| **P0** | P0-1: action web gọi CLI bằng `-I`/`-P` hoặc entry point tuyệt đối (SEC-02) | S |
| **P0** | P0-2: verification trên macOS không âm thầm chạy unconfined. Bước 1 coi đây là problem hoặc cần xác nhận, và đặt `AGENT_BRIDGE_AGENT` cho verification. Bước 2 thêm confinement trên macOS (SEC-01) | S / L |
| P1 | Mức sàn cho config bảo vệ và không nạp config bị sửa (CFG-01, SEC-04) | M |
| P1 | Approve gắn với `verified_head` và toàn bộ worktree (SEC-07, SEC-05) | M |
| P1 | Recover: trả attempt, TERM→KILL, giữ slot tới khi orphan chết (FUN-01, REL-01..03) | M |
| P1 | `env_allow` theo agent và không áp cho verification; mở rộng deny/redact (AUTH-01, AUTH-04, SEC-09, SEC-10) | S–M |
| P1 | Workflow INCOMPLETE khi dependency thất bại (FUN-02) | S |
| P1 | Dashboard: cache git, endpoint chi tiết chỉ đọc một project (PERF-01/02) | M |
| P1 | Sửa UX-01/02/03 kèm e2e sau một chu kỳ poll (TEST-03) | S |
| P1 | Nhãn chi phí và phân loại lỗi billing (BILL-01, AUTH-06) | S–M |
| P1 | Test hồi quy cho mọi P0/P1; checklist probe CLI thật theo phiên bản (TEST-01, TEST-04) | M |
| P2 | Workflow/routing, lưu trữ/chẩn đoán, tiện dụng settings, UX còn lại, timeout test, dọn coupling | M mỗi nhóm |

Trade-off chính:
- P0-2 bước 1 rẻ nhưng làm verification trên macOS phiền hơn.
- Bước 2 bảo vệ thật nhưng dựa trên `sandbox-exec` (Apple đã deprecated) hoặc sandbox của Codex, nên cần probe trước.
- P1-1 buộc mọi thay đổi config an ninh đi qua một bước xác nhận của human.
- Không đề xuất rewrite.

## 10. Production readiness

| Bối cảnh sử dụng | Kết luận |
|---|---|
| Một người dùng, agent và repo tin cậy, chủ yếu dùng CLI/MCP | **CONDITIONAL GO.** Điều kiện: sửa P0-1, dùng `agent-bridge web --read-only` cho tới khi sửa xong, và chấp nhận rằng verification trên macOS chạy với quyền của bạn |
| Agent có thể bị prompt-injection (đọc issue, PR, web) trên macOS với cấu hình mặc định | **NO-GO** cho tới khi xong P0-1 và P0-2. SEC-01 cho phép mã của agent giả human approve |
| Linux có Landlock | **CONDITIONAL GO** sau P0-1 và P1-1/P1-2. Kết luận này dựa trên đọc mã, chưa chạy runtime |
| Nhiều người dùng, dịch vụ chia sẻ | Ngoài phạm vi thiết kế (README ghi rõ). Không đánh giá |

## Phương pháp & giới hạn của audit

- Mọi thí nghiệm runtime chạy với `HOME` và `AGENT_BRIDGE_HOME` giả, fake agent CLI và repo dùng một lần. Không dùng account, token hay quota thật. Không gọi tool MCP `agent-bridge` của phiên.
- Phase 2–6 do năm auditor độc lập thực hiện song song. Tôi tự kiểm lại từ source các finding then chốt: SEC-02, SEC-07, FUN-01, CFG-05, REL-02 và UX-01.
- Trong lúc audit, `~/.agent-bridge/accounts.json` và `accounts.db` thay đổi do lệnh `account-remove` người dùng tự chạy ở terminal riêng; người dùng đã xác nhận. `.git/agent-bridge/bridge.db` của repo không đổi.
- Kiểm tra `git status` sau audit: ngoài các thay đổi có sẵn của người dùng (`.claude/settings.json`, `CLAUDE.md`, `README.md`, `README.vi.md`, `docs/plans/multi-bridge-accounts.md`), chỉ có 9 file mới trong `docs/audits/` (`01`–`08` và `MASTER-AUDIT-REPORT.md`). Các file audit cũ không bị sửa.
- Bộ tái hiện bảo mật (15 test, PASS = finding còn tồn tại) được giữ ở [evidence/test_audit_2026_11_08_security.py](evidence/test_audit_2026_11_08_security.py), chạy cô lập bằng `docs/audits/evidence/run_audit_repro.sh docs/audits/evidence/test_audit_2026_11_08_security.py` (đã chạy lại trên `0541153`: 15 passed, 60,6 s). Các script khác của Phase 2, 3, 5, 6 nằm trong scratchpad và sẽ mất; lệnh, input và output chính đã chép vào từng báo cáo.
