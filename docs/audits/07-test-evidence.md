# Phase 7 — Independent Testing & Evidence Validation

## Phạm vi, phương pháp, môi trường

- **Phạm vi:** độ tin cậy của test suite hiện có (kiến trúc test, assertion, fixture, fake), chạy lại toàn bộ bộ test, mutation probe trên các guard quan trọng, đối chiếu tuyên bố của README/test report với kết quả thật, và tổng hợp các kiểm thử độc lập do Phase 2–6 thực hiện.
- **Môi trường:** macOS 26.5, Apple M2 8 core, Python 3.12.11 (`.venv`), HEAD `0541153`. Mọi lần chạy dùng basetemp riêng trong scratchpad, `tests/fakes` đứng đầu PATH, HOME tạm. Không sửa test, fixture hay source của repo; mutation chạy trên **bản copy** ngoài repo.
- **BLOCKED:** real-CLI smokes (`CODEX_SMOKE`, `CLAUDE_SMOKE`, `OPENCODE_SMOKE`, `AGY_SMOKE`): tốn quota thật. Landlock tests: không có trên macOS. Đo coverage dòng: `coverage` không có trong `.venv` và audit không cài thêm vào môi trường dự án.
- Trạng thái máy: trong lúc audit, `~/.agent-bridge/accounts.json` và `accounts.db` thay đổi. Nguyên nhân đã truy ra là lệnh `agent-bridge account-remove` do **người dùng** chạy ở terminal riêng (ttys002, 14:32), người dùng đã xác nhận. Mọi tiến trình của audit dùng HOME giả; `.git/agent-bridge/bridge.db` của repo không đổi (mtime 2026-11-02).

## 1. Kiến trúc test suite (đọc mã + quét AST)

| Chỉ số | Giá trị | Nguồn |
|---|---|---|
| File test / hàm test | 65 / 525 (sau parametrize: 719 item được collect) | quét AST `tests/**/test_*.py` |
| Phân tầng | `tests/unit`, `tests/integration`, `tests/e2e` (fake + real opt-in), `tests/qa` (end-to-end: CLI thật của bridge, MCP stdio, Chromium, chỉ chạy khi `QA_E2E=1`) | cây thư mục |
| Fake agent CLI | `tests/fakes/{codex,claude,opencode,agy,codex-switch}`, có kịch bản `ok, slow, commit, outside, quota, auth, network, crash, hang…` (`tests/fakes/codex:136-170`) | đọc mã |
| Cô lập | fixture autouse `isolated_home` đặt HOME tạm, xoá `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `AGENT_BRIDGE_AGENT` (`tests/conftest.py:20-27`); **không** xoá `AGENT_BRIDGE_HOME` (CFG-08) | đọc mã |
| Mock | 0 `unittest.mock`; 148 `monkeypatch.setattr` (nhiều nhất: `storage.running_processes` 15, `cli.sys.stdout.isatty` 7, `osutil.WINDOWS` 6) | quét text |
| Chờ theo thời gian | 27 `time.sleep` | quét text |
| Test không có assert | 2: `test_state_location.py:136` (ủy quyền cho 2 test khác), `test_quota_limits.py:103` (kiểm "không raise"); chấp nhận được | quét AST |
| Timeout theo test | **Không có** (dev deps chỉ `pytest`; không có `pytest-timeout`) | `pyproject.toml` |
| Metadata test | Quy ước `# @pt.id/@pt.condition/@pt.expected/@pt.target` | đọc mã |

Đánh giá: suite dựa trên tiến trình con thật và fake CLI nói đúng giao thức JSONL/stream-json, ít mock — đó là điểm mạnh. Điểm yếu nằm ở những gì fake **không thể** mô phỏng (mục 5).

## 2. Kết quả chạy

| # | Bộ / lệnh | Input | Kỳ vọng | Thực tế | Kết quả |
|---|---|---|---|---|---|
| T1 | `.venv/bin/python -m pytest -q -rs` | toàn bộ, addopts repo | 0 fail | 605 passed, 114 skipped, 252.6 s | PASS |
| T2 | `QA_E2E=1 NODE_PATH=/opt/homebrew/lib/node_modules .venv/bin/python -m pytest -q -rs tests/qa --basetemp=<scratch>` | 106 test e2e, Chromium headless | 0 fail | 104 passed, 2 skipped (Landlock `test_e2e_security.py:154`, axe-core `test_e2e_web.py:219`), 496.3 s | PASS |
| T3 | Tệp tái hiện cũ `docs/audits/evidence/test_audit_repro.py` (11 test, viết ở revision `3571546`, PASS = lỗi còn) chạy trên bản copy (Phase 4) | — | — | 10 FAIL (lỗi cũ không còn tái hiện **bằng test cũ**), 1 PASS (REL-01 cũ: PASS giả vì test monkeypatch `_git` trả `""`, đi vòng qua đúng chỗ đã sửa) | Xem mục 4 |
| T4 | Contract flag: so mọi flag `executor.py` truyền cho agent với `codex exec --help`, `codex exec resume --help`, `claude --help` | codex-cli 0.166.0, Claude Code 2.1.319 | mọi flag tồn tại | 100% có mặt; `codex exec resume` không có `--sandbox`, bridge dùng `-c sandbox_mode=…` (`executor.py:392-395`) | PASS (cú pháp); hiệu lực của `-c sandbox_mode` khi resume chưa kiểm chứng |
| T5 | `pip-audit` (Phase 4) | 39 gói runtime / 44 gói mọi group | 0 lỗ hổng biết | 0 | PASS |
| T6 | Mutation probe (mục 3) | 8 mutant + 1 đối chứng | mỗi mutant bị một test liên quan bắt | 6 bị bắt đúng chỗ, 1 tương đương, 1 chỉ bị phát hiện bằng treo | Xem mục 3 |
| T7 | Real-CLI smokes | — | — | — | BLOCKED (quota thật) |
| T8 | Landlock (`test_verify_sandbox.py` ×3) | — | — | skip | BLOCKED (macOS) |

## 3. Mutation probe (độ nhạy của test với các guard quan trọng)

**Cách làm.** Script copy `bridge/`, `tests/`, `pyproject.toml`, kit files (`.claude/rules`, `.claude/skills`, `examples`, `.ai/config.example.json`, `CLAUDE.md`) sang scratchpad, `git init` + commit, áp đúng một thay đổi chuỗi, chạy `PYTHONPATH=<copy> .venv/bin/python -m pytest -x -q -p no:cacheprovider --basetemp=<copy>/pt tests/unit tests/integration tests/e2e --deselect tests/integration/test_setup.py::test_fresh_install`. Bốn mutant chạy song song.

**Đối chứng (M0, đổi một comment):** 518 passed, 1 failed (`test_setup.py::test_preserve_existing`). Toàn bộ `test_setup.py` phụ thuộc nội dung kit gốc nên hỏng trên bản copy; mọi lần "bị bắt" chỉ bởi `test_setup.py` vì vậy **không** được tính.

| Mutant | Thay đổi | Test bắt được | Kết luận |
|---|---|---|---|
| M1 | `security.validate_pattern`: bỏ `or matches(norm, protected)` | `test_core.py::test_path_validation` (DID NOT RAISE), 6 s | Bắt đúng |
| M2 | `review.scope_violations` luôn `return []` | `test_bridge.py::test_scope_violation_blocks_review[…README.md=hack…]`, 187 s | Bắt đúng |
| M3 | `models.check_transition` luôn `return` | `test_core.py::test_transitions_enforce_actor_and_legality`, 7 s | Bắt đúng |
| M4 | `security.redact` không làm gì | `test_core.py::test_redact_masks_credentials`, 10 s | Bắt đúng |
| M5 | `cli._human_confirm`: bỏ kiểm tra TTY | `test_accounts_concurrency.py::test_reassign`, 120 s | Bắt đúng |
| M6 | `state.transition`: `>= account_limit` → `> account_limit` | Chỉ `test_setup.py` (đối chứng) | **Sống sót, nhưng là mutant tương đương**: trong cùng worktree, nhánh one-writer ngay sau đó (`if active: raise ConflictError`) luôn chặn trước; giới hạn liên-worktree do `AccountLock` lo. Kiểm tra `account_limit` ở đây là mã thừa (gợi ý dọn ở ARC-04), không phải lỗ hổng test |
| M7 | `security.clean_env` trả `dict(env)` | `test_core.py::test_clean_env`, 8 s | Bắt đúng |
| M8 | `state.transition`: bỏ rule one-writer-per-worktree | Không có assertion nào fail; `test_accounts_concurrency.py::test_conflicts` **treo** > 40 phút, phải kill tay | **Chỉ phát hiện được bằng treo** (TEST-02) |

## 4. Đánh giá lại bằng chứng cũ

- **Tệp tái hiện của audit cũ** (T3): 10/11 không còn tái hiện bằng chính test cũ, nhưng Phase 4 cho thấy 7 trong số đó chỉ được sửa một phần và vẫn tái hiện được bằng biến thể mới (SEC-01, SEC-04, SEC-06, SEC-07, SEC-08, SEC-10, SEC-11 mới), và SEC-08 cũ (human gate) nay là SEC-03, nặng hơn. Kết luận: "test cũ FAIL" **không** đồng nghĩa "lỗi đã được sửa"; từng ID đã được kiểm lại bằng test riêng (chi tiết: `04-security-assessment.md`, bảng revalidation).
- **REL-01 cũ:** test PASS vì monkeypatch `_git` → `""`, tức là đi vòng qua đúng đoạn đã sửa; với git thật, `EvidenceError` được raise. Đây là một ví dụ về test cho kết quả đúng vì lý do sai.
- **README "The suite covers … the dashboards"** và `docs/qa/e2e-test-cases.md`: kết quả T2 xác nhận 104 test e2e pass trên máy này, nhưng xem TEST-03 về những gì e2e không kiểm.

## 5. Findings

### TEST-01 — Các lỗi đã xác nhận ở Phase 2–6 không có test nào bắt; suite xanh không phản ánh các bất biến đã tài liệu hoá
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect (coverage gap)
- Priority: P1
- Evidence: T1 và T2 xanh trong khi các lỗi sau được tái hiện bằng fake CLI trên cùng HEAD: FUN-01 (recover tiêu attempt, `service.py:1047-1050` khác `runner.py:330-338`), FUN-02 (dependency CANCELLED → WAITING), SEC-02 (`webactions.py:104` chạy `-m bridge` với cwd project), SEC-07 (`verified_head` chỉ được ghi ở `runner.py:293`, không nơi nào đọc), REL-01/REL-02 (agent mồ côi sau `kill -9`, `kill_stale` chỉ SIGTERM: `executor.py:677` `kill_group(pid, force=False)`), UX-01, UX-03.
- Expected vs actual: các bất biến mà `.claude/rules/orchestration.md` và README tuyên bố (PAUSED không tốn budget; BLOCKED/CANCELLED → INCOMPLETE; approve gắn với trạng thái đã verify) phải có test hồi quy; thực tế không có.
- Root cause & impact: test viết theo luồng đã implement, không theo bất biến tài liệu; người đọc "605 passed" sẽ tin quá mức.
- Options & trade-offs: thêm test hồi quy cho từng finding Confirmed khi sửa (rẻ, đúng chỗ); hoặc thêm một lớp "invariant tests" sinh từ bảng trong `orchestration.md` (tốn hơn, bao rộng hơn).
- Acceptance criteria / verification method: mỗi finding Confirmed P0/P1 có ít nhất một test FAIL trên `0541153` và PASS sau khi sửa.

### TEST-02 — Không có timeout theo test: một hồi quy ở rule one-writer làm suite treo vô hạn thay vì fail
- Severity: Low      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: M8 (mục 3): `test_accounts_concurrency.py::test_conflicts` treo > 40 phút; `pyproject.toml` `[dependency-groups] dev = ["pytest>=9.1.1"]`, không `pytest-timeout`; `.github/workflows/ci.yml` không đặt `timeout-minutes` (mặc định GitHub là 360 phút).
- Expected vs actual: hồi quy ở guard đồng thời phải làm test fail nhanh, có tên test; thực tế CI sẽ treo tới giới hạn job.
- Root cause & impact: test đồng thời chờ điều kiện không có hạn. Tốn phút CI, che lỗi.
- Options & trade-offs: thêm `pytest-timeout` (một dependency dev) với timeout mặc định rộng; hoặc đặt deadline trong các vòng chờ của test đồng thời; thêm `timeout-minutes` cho job CI (không cần dependency).
- Acceptance criteria / verification method: chạy lại M8 → một test FAIL có tên trong < 5 phút.

### TEST-03 — E2E dashboard chỉ thử bàn phím ở lần render đầu, không thử sau khi poll cập nhật DOM
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect (false confidence)
- Priority: P1
- Evidence: `tests/qa/web/dashboard.cjs:764-768` focus một `.card` và nhấn Enter ngay; check "Enter on a card opens the drawer" PASS trong T2. Phase 6 (UX-01) tái hiện: sau khi `syncNode` (`app.js:241-250`) chép `onkeydown` từ node mới sang node cũ, closure trong `activate` (`app.js:320-324`) so `event.target === row` với node mới đã bị bỏ, nên Enter/Space không còn tác dụng trên hàng đã được patch.
- Expected vs actual: e2e phải phát hiện lỗi bàn phím WCAG 2.1.1; thực tế pass.
- Root cause & impact: kịch bản không đi qua chu kỳ poll 3 s; lỗi chỉ xuất hiện sau cập nhật DOM. Người dùng bàn phím bị chặn.
- Options & trade-offs: thêm bước chờ ít nhất một poll (hoặc gọi hàm render lần hai) trước khi nhấn phím; rẻ, tăng thời gian e2e vài giây.
- Acceptance criteria / verification method: check mới FAIL trên `0541153`, PASS sau khi sửa UX-01.

### TEST-04 — Fake CLI không mô phỏng các ranh giới bảo mật thực; nhánh sandbox chưa từng được runtime-verify trên macOS
- Severity: Medium      - Confidence: Confirmed
- Kind: Intentional limitation (có tài liệu) với hệ quả chưa được nêu rõ
- Priority: P1
- Evidence: fake `codex`/`claude` ghi file trực tiếp, không có seatbelt, không áp quy tắc `--allowedTools/--disallowedTools`; Landlock tests skip trên macOS (T1/T2, T8); real smokes opt-in và BLOCKED; CI chạy cả `ubuntu-latest` (có thể có Landlock) nhưng audit không kiểm kết quả CI. Phase 4 liệt kê các câu hỏi chỉ trả lời được bằng CLI thật: seatbelt có chặn ghi `.ai/`, `.git/`, ghi qua symlink, `setsid`; deny `Read(...)` có áp lên Grep/Glob của Claude không.
- Expected vs actual: các tuyên bố an ninh ở README ("Codex sandbox behaviour was verified on macOS only", "Probes wrote no files") cần bằng chứng có thể lặp lại; thực tế dựa trên probe một lần trong `docs/REPORT.md`, không có test chạy định kỳ.
- Root cause & impact: không thể chạy CLI thật trong CI miễn phí. Nếu CLI thay đổi hành vi (codex-cli 0.166.0 và Claude Code 2.1.319 đều cập nhật thường xuyên), bridge mất lớp bảo vệ mà không ai biết.
- Options & trade-offs: (a) một bộ "probe" nhỏ chạy tay trước mỗi release với account thử, ghi kết quả vào `docs/REPORT.md` kèm phiên bản CLI; (b) kiểm tra phiên bản CLI tối thiểu/tối đa đã probe trong `health`. (a) tốn quota nhỏ, (b) rẻ nhưng chỉ cảnh báo.
- Acceptance criteria / verification method: mỗi tuyên bố sandbox trong README trỏ tới một probe có ngày và phiên bản CLI không cũ hơn bản đang cài.

### TEST-05 — Một số test cho kết quả đúng vì lý do sai hoặc phụ thuộc môi trường người chạy
- Severity: Low      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: (1) REL-01 cũ PASS nhờ monkeypatch `_git` → `""` (mục 4). (2) CFG-08: `tests/conftest.py:20-27` không xoá `AGENT_BRIDGE_HOME`; Phase 3 đo được 3 test fail chập chờn và ghi vào state chỉ định khi biến này có trong shell, 86 pass khi bỏ biến. (3) Toàn bộ `test_setup.py` phụ thuộc nội dung kit ở thư mục gốc (đối chứng M0).
- Expected vs actual: test phải tự cô lập và kiểm đường thật.
- Root cause & impact: một nhà phát triển có `AGENT_BRIDGE_HOME` trong shell có thể làm bẩn state thật khi chạy test.
- Options & trade-offs: thêm `AGENT_BRIDGE_HOME` vào danh sách `delenv` của `isolated_home` (một dòng).
- Acceptance criteria / verification method: chạy suite với `AGENT_BRIDGE_HOME=/some/dir` → không file nào được tạo trong `/some/dir`, 0 fail.

## 6. Tổng hợp kiểm thử độc lập của các phase (chỉ số, chi tiết ở từng báo cáo)

| Phase | Số kịch bản độc lập chạy | Ghi chú |
|---|---|---|
| 2 | 9 script với `Bridge` API + fake CLI | transitions bất hợp lệ, cycle, budget, cancel liên tiến trình 0.61 s, recover, slot_wait |
| 3 | repro config/env (envdump qua fake CLI) | `env_allow` lọt sang mọi adapter và verification (AUTH-01) |
| 4 | 15 test mới + chạy lại 11 repro cũ | 2 High/P0 mới (SEC-01, SEC-02) |
| 5 | benchmark ~6.400 task / 13 project, 6–16 tiến trình tranh slot/ledger | ledger đúng $100/$100; slot đúng 2/2 |
| 6 | Playwright 1440/390 px, light/dark, reduced-motion, axe-core 4.10.2 (24 lượt, 0 vi phạm), TUI headless | UX-01 High |

## 7. Verdict

**Testing: NEEDS IMPROVEMENT.** Suite rộng, ít mock, chạy ổn định (719 item, 0 fail) và bắt bằng assertion 6/7 mutant không tương đương ở guard đơn lẻ (mutant thứ 7 chỉ làm suite treo); nhưng nó không bảo vệ các bất biến đa bước đã tài liệu hoá (TEST-01), có điểm mù bàn phím ở e2e (TEST-03), không có timeout (TEST-02), và không có cách lặp lại bằng chứng về sandbox của CLI thật (TEST-04).
