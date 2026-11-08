# Phase 8 — Consolidated Assessment & Improvement Roadmap

Revision `0541153`, audit 2026-11-08. Tổng hợp 72 finding từ Phase 2–7. Chi tiết, bằng chứng và tiêu chí chấp nhận của từng ID nằm trong báo cáo phase tương ứng; tài liệu này chỉ gom nhóm, xếp hạng và lập lộ trình. **Không có gì trong roadmap đã được triển khai.**

Lưu ý ID: trong `04-security-assessment.md`, các ID của audit cũ (revision `3571546`) chỉ xuất hiện trong bảng revalidation; mọi ID `SEC-NN`, `REL-NN` ở đây là ID **mới** của đợt này.

## 1. Phân bố finding

| Lĩnh vực | Critical | High | Medium | Low | Tổng |
|---|---|---|---|---|---|
| Architecture (ARC) | 0 | 0 | 1 | 3 | 4 |
| Functionality (FUN) | 0 | 0 | 2 | 4 | 6 |
| Settings (CFG) | 0 | 1 | 4 | 3 | 8 |
| Authentication (AUTH) | 0 | 1 | 5 | 2 | 8 |
| Billing (BILL) | 0 | 0 | 1 | 3 | 4 |
| Security (SEC) | 0 | 2 | 6 | 5 | 13 |
| Performance (PERF) | 0 | 0 | 2 | 3 | 5 |
| Reliability (REL) | 0 | 0 | 3 | 4 | 7 |
| UX/UI (UX) | 0 | 1 | 4 | 7 | 12 |
| Testing (TEST) | 0 | 0 | 3 | 2 | 5 |
| **Tổng** | **0** | **5** | **31** | **36** | **72** |

Theo độ tin cậy: 65 Confirmed (trong đó SEC-05, SEC-06 Confirmed với CLI giả nhưng Hypothesis với CLI thật; SEC-12 Confirmed ở header, Likely ở phần lộ sang cổng khác), 3 Likely (AUTH-06, AUTH-08, UX-07), 4 Hypothesis (AUTH-05, AUTH-07, BILL-03, REL-06).

## 2. Nhóm vấn đề gốc (đã khử trùng lặp)

Nhiều finding là triệu chứng của cùng một nguyên nhân. Roadmap sửa theo nhóm, không theo từng ID.

| Nhóm | Finding | Nguyên nhân gốc | Loại |
|---|---|---|---|
| G1. Verification chạy mã của executor không bị giới hạn trên macOS | SEC-01 (High), SEC-11, SEC-03 (một phần), AUTH-01 (key lọt vào verification), TEST-04 | `verify_sandbox: auto` chỉ có Landlock (Linux); trên macOS tự hạ xuống "unconfined" với `health ok: true`; verification không mang `AGENT_BRIDGE_AGENT` | Lỗi xác nhận + rủi ro nền tảng (có tài liệu, nhưng mặc định không an toàn) |
| G2. Action web thực thi mã từ worktree | SEC-02 (High) | `webactions.py:104` `[sys.executable, "-m", "bridge", …]` với cwd = project root → `sys.path[0]` = worktree | Lỗi xác nhận |
| G3. Cấu hình an ninh bị sửa vẫn được nạp | SEC-04, CFG-01 (High), CFG-02 | Phát hiện thay đổi file bảo vệ → BLOCKED nhưng không khôi phục; `protected_paths`, `env_deny`, `verify_allowlist` thay thế được hoàn toàn, không có mức sàn | Lỗi xác nhận |
| G4. Phê duyệt không gắn với trạng thái đã verify | SEC-07, SEC-05, SEC-08 | Approve chỉ so hash các file attempt đã chạm; `verified_head` lưu nhưng không so; file mới/ignored/symlink nằm ngoài tập so | Lỗi xác nhận |
| G5. Rò credential và định tuyến qua env/log | AUTH-01 (High), AUTH-04, SEC-09, SEC-10, AUTH-02 | `env_allow` toàn cục cho mọi adapter + verification; deny-list theo mẫu tên bỏ sót `*_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `SECRET_KEY`…; regex redact thiếu JSON key có nháy và nhiều tiền tố token | Lỗi xác nhận |
| G6. Phục hồi sau khi runner chết | FUN-01, REL-01, REL-02, REL-03, REL-07 | `recover()` không trả attempt; `kill_stale` chỉ SIGTERM; flock nhả trong khi agent mồ côi còn chạy; chỉ quét 10.000 task mới nhất | Lỗi xác nhận, trái tài liệu |
| G7. Ngữ nghĩa workflow | FUN-02, FUN-04, FUN-05, FUN-06, ARC-02 | Không propagate thất bại của dependency; không có owner workflow giữa tiến trình; phân loại retry bằng chuỗi | Lỗi xác nhận |
| G8. Hiệu năng dashboard web | PERF-01, PERF-02, PERF-03 | Mỗi request dựng lại toàn bộ snapshot mọi project + 3 lệnh git/project; cache không bao git, không evict | Lỗi xác nhận (đo được) |
| G9. Nhất quán dữ liệu và thao tác giữa các bề mặt | UX-01 (High), UX-02, UX-03, UX-04, UX-07, TEST-03 | Diff DOM chép handler sai node; dashboard không đọc quota rollout; giới hạn 200 task im lặng; định nghĩa "Needs you" khác nhau | Lỗi xác nhận |
| G10. Kế toán chi phí và phân loại lỗi provider | BILL-01, BILL-03, BILL-04, AUTH-06 | Không phân biệt subscription/API; `total_cost_usd` ước tính bị ghi `actual`; lỗi billing/403/529 bị xếp `crash` | Lỗi xác nhận + rủi ro |
| G11. Human gate cùng UID | SEC-03, SEC-12 | Mọi tiến trình cùng user; TTY check vượt được bằng `script`; README tự nhận không phải boundary | Giới hạn có chủ đích (có tài liệu) |
| G12. Bằng chứng test | TEST-01, TEST-02, TEST-04, TEST-05, CFG-08 | Test theo luồng implement, không theo bất biến; không timeout; fake không mô phỏng sandbox | Lỗi xác nhận |
| G13. Nợ kỹ thuật & tiện dụng | ARC-01, ARC-03, ARC-04, CFG-03..07, PERF-04, PERF-05, REL-04..06, UX-05, UX-06, UX-08..12, AUTH-03, AUTH-05, AUTH-07, AUTH-08, BILL-02, SEC-13 | Đa dạng | Cải tiến / rủi ro thấp |

## 3. Verdict theo lĩnh vực

| Lĩnh vực | Verdict | Lý do ngắn |
|---|---|---|
| Architecture | **NEEDS IMPROVEMENT** | Phân lớp tốt (mọi bề mặt đi qua `Bridge`, state machine tập trung, một transaction cho transition + event); coupling hai chiều giữa `Bridge`/`TaskRunner`/`AccountGate`, schema không version (ARC-01, ARC-03) |
| Functionality | **NEEDS IMPROVEMENT** | Transition bất hợp lệ, budget, cancel, high-risk gate đều đúng khi chạy thử; recover và workflow dependency sai tài liệu (FUN-01, FUN-02) |
| Settings | **NEEDS IMPROVEMENT** | Danh sách bảo vệ không có mức sàn (CFG-01); `AGENT_BRIDGE_HOME` không dời registry (CFG-05); không xem được cấu hình hiệu lực (CFG-07) |
| Authentication | **NEEDS IMPROVEMENT** | Login subscription Claude/Codex cô lập tốt; API key/setup-token chỉ dùng toàn cục và lan sang adapter khác + verification (AUTH-01, AUTH-02) |
| Security | **HIGH RISK** | Hai P0 xác nhận trên cấu hình mặc định macOS (SEC-01, SEC-02); web auth/CSRF/Host/XSS tốt; supply chain sạch (pip-audit 0) |
| Performance | **NEEDS IMPROVEMENT** | CLI/TUI/SQLite/dispatch GOOD; dashboard web chậm và tốn khi nhiều project (PERF-01/02) |
| Reliability | **NEEDS IMPROVEMENT** | Slot/ledger/cancel/timeout/retry đúng dưới tải; crash recovery và lỗi lưu trữ yếu (REL-01, REL-02, REL-04) |
| UX/UI | **NEEDS IMPROVEMENT** | axe 0 vi phạm, responsive và latency tốt; lỗi bàn phím (UX-01) và số liệu lệch giữa bề mặt (UX-02..04) |
| Testing | **NEEDS IMPROVEMENT** | 719 item xanh, bắt 6/7 mutant guard đơn; không bảo vệ bất biến đa bước, không timeout, không lặp lại được bằng chứng sandbox (TEST-01..04) |

## 4. Roadmap

Effort: **S** < 1 ngày, **M** 1–3 ngày, **L** > 3 ngày (ước lượng của auditor, chưa được nhóm phát triển xác nhận). Mỗi mục phải kèm test hồi quy FAIL trên `0541153` (G12 đi cùng mọi mục, không phải đợt riêng).

### P0 — trước khi dùng với agent không hoàn toàn tin cậy trên macOS

| # | Mục | Finding | Effort | Phương án & trade-off | Tiêu chí chấp nhận / kiểm chứng |
|---|---|---|---|---|---|
| P0-1 | Action web gọi CLI của kit, không gọi module từ cwd | SEC-02 | S | (a) `sys.executable -I -m bridge` hoặc `-P` (Python ≥ 3.11) — tối thiểu, nhưng `-I` cũng bỏ `PYTHON*` env; (b) gọi entry point theo đường dẫn tuyệt đối của kit. Chọn (a) nếu không cần env Python của người dùng | Repro Phase 4: worktree có `bridge/__main__.py`, bấm resume trên web → payload **không** chạy; e2e web vẫn pass |
| P0-2 | Verification trên macOS không âm thầm chạy unconfined | SEC-01, SEC-11, TEST-04 | S (bước 1) / L (bước 2) | Bước 1: khi không có sandbox, `auto` không coi là "ok": `health` báo `problems` (không chỉ warning), task cần xác nhận của human (hoặc mặc định `strict` từ chối) — rẻ, đổi UX. Bước 2: confinement trên macOS (`sandbox-exec` profile — API Apple đã deprecated nhưng còn hoạt động — hoặc chạy verification trong Codex `read-only`/`workspace-write` sandbox) — tốn, cần probe. Đồng thời đặt `AGENT_BRIDGE_AGENT=1` cho tiến trình verification | Repro Phase 4: payload trong `conftest.py` ghi ra ngoài worktree và gọi `Bridge.human_approve` → bị chặn (Bridge API từ chối do env, ghi ngoài bị sandbox chặn hoặc task không chạy verification khi chưa có xác nhận) |

### P1 — đợt sửa kế tiếp

| # | Mục | Finding | Effort | Phương án & trade-off | Tiêu chí chấp nhận |
|---|---|---|---|---|---|
| P1-1 | Config bảo vệ: mức sàn + không nạp file bị agent sửa | CFG-01, SEC-04, CFG-02 | M | Mức sàn cứng cho `protected_paths`/`env_deny` (người dùng chỉ thêm được); lưu hash config đã được human chấp nhận và từ chối khởi động khi khác (đổi UX: sửa config cần một lệnh xác nhận) | `.ai/config.json` với `protected_paths: []` vẫn chặn `.ai/**`; task sửa `.ai/orchestrator.yaml` → lần khởi động sau báo lỗi, không nạp |
| P1-2 | Gắn approve với trạng thái đã verify | SEC-07, SEC-05 | M | So `verified_head` và tập file **toàn worktree** (kể cả file mới, untracked) tại approve; kiểm symlink trong allowed path bằng `lstat`. Tốn thêm một snapshot khi approve | Repro Phase 4 (commit thêm + `conftest.py` mới sau verify) → approve bị từ chối với lý do rõ |
| P1-3 | Phục hồi runner chết đúng tài liệu | FUN-01, REL-01, REL-02, REL-03 | M | `recover()` trả attempt như `_park_if_stopping`; `kill_stale` TERM → chờ → KILL; giữ slot cho tới khi nhóm process mồ côi chết; recover quét mọi task ACTIVE (truy vấn theo state, không theo 10.000 dòng mới nhất) | Repro FUN-01 với `max_attempts=1` → resume chạy agent; repro REL-01/02 → không có hai agent trên cùng account/worktree |
| P1-4 | Env & secret | AUTH-01, AUTH-04, SEC-09, SEC-10 | S–M | `env_allow` theo agent, không bao giờ áp cho verification; thêm deny `*_BASE_URL`, `CLAUDE_CODE_USE_*`, `*_KEY`, `*_PAT`, `NETRC`…; bổ sung regex redact (JSON key có nháy, `sk_live_`, `AIza`, `github_pat_`, `npm_`, mật khẩu trong URL). Trade-off: deny rộng có thể chặn biến hợp lệ → có `env_allow` theo agent để mở | Repro envdump Phase 3: key cho phép ở agent A không xuất hiện ở agent B và verification; bộ chuỗi mẫu Phase 4 bị che hết |
| P1-5 | Thất bại của dependency → INCOMPLETE | FUN-02 | S | Khi dependency vào CANCELLED/BLOCKED, chuyển task phụ thuộc sang CANCELLED (có lý do) hoặc tính workflow INCOMPLETE | Repro FUN-02 → workflow `INCOMPLETE`, không có `waiting: {}` |
| P1-6 | Dashboard web nhẹ hơn | PERF-01, PERF-02 | M | Cache kết quả git theo mtime `.git/HEAD`/index; endpoint chi tiết chỉ đọc project được hỏi; tuỳ chọn: overview gửi delta/ETag. Trade-off: thêm trạng thái cache cần invalid đúng | Workload Phase 5 (13 project, ~6.400 task): `/api/overview` p50 < 200 ms ở c=1; chi tiết task < 100 ms |
| P1-7 | Lỗi UX có ảnh hưởng thao tác | UX-01, UX-02, UX-03, TEST-03 | S | Sửa closure bàn phím (so với `event.currentTarget`); dashboard đọc quota rollout như CLI; báo "đã cắt N/M" khi vượt 200 | E2E mới: Enter sau một chu kỳ poll mở drawer; cùng account cùng số quota ở web/CLI/TUI; `list` > 200 có thông báo |
| P1-8 | Nhãn chi phí đúng loại account | BILL-01, AUTH-02, AUTH-06 | S–M | Ghi `estimated` cho `total_cost_usd` khi account là subscription; phân loại billing/403/529 thành PAUSED thay vì `crash` | Test phân loại với chuỗi lỗi mẫu; `usage` hiển thị nguồn chi phí đúng |
| P1-9 | Bằng chứng test | TEST-01, TEST-04 | M | Test hồi quy cho mỗi finding P0/P1; checklist probe CLI thật trước release, ghi phiên bản CLI vào `docs/REPORT.md` và cảnh báo trong `health` khi CLI mới hơn bản đã probe | Mỗi mục P0/P1 có test; `health` cảnh báo khi `codex`/`claude` khác phiên bản đã probe |

### P2 — khi có thời gian, theo nhóm

| Nhóm | Finding | Effort | Ghi chú |
|---|---|---|---|
| Workflow & routing | FUN-03..06, ARC-02, REL-06 | M | Route sau khi kiểm state; owner workflow theo pid + start time; deadline cho lần thử slot đầu; mã lỗi có cấu trúc thay cho so chuỗi |
| Lưu trữ & chẩn đoán | REL-04, REL-05, ARC-03, PERF-04, PERF-03 | M | `health` bắt lỗi DB và chạy `quick_check`; không chmod khi mở read-only; `PRAGMA user_version`; index `spec_hash`, `created_at`, `workflow_id`; cache có giới hạn |
| Settings tiện dụng | CFG-03..07, CFG-08, TEST-05 | S–M | `config-check` kiểm `home`, cảnh báo key legacy bị bỏ qua; lệnh in cấu hình hiệu lực; `AGENT_BRIDGE_HOME` dời registry (hoặc sửa tài liệu); `conftest` xoá `AGENT_BRIDGE_HOME` |
| Bảo mật bổ sung | SEC-06, SEC-08, SEC-12, SEC-13, AUTH-05, AUTH-07 | S–M | Theo dõi tiến trình `setsid` (giới hạn đã biết); chặn ignored file ngoài allowed path cả khi có review; cookie tên theo cổng; pin action theo SHA, `uv sync --locked`; Read deny Codex cho homes khác (cần probe seatbelt) |
| UX còn lại | UX-04..12 | S–M | Thống nhất thuật ngữ (approve review vs human approve; run/execute), trang link hết hạn có hướng dẫn, khoá form khi offline, phân trang board, TUI 80×24 |
| Kiến trúc | ARC-01, ARC-04, TEST-02 | M | Thu hẹp giao diện giữa `Bridge`/`TaskRunner`/`AccountGate` khi đụng tới (không rewrite); dọn quyền actor thừa và kiểm `account_limit` thừa (mutant M6); `pytest-timeout` hoặc `timeout-minutes` cho CI |
| Chính sách | AUTH-08, BILL-02 | — | Ghi rõ trong README rủi ro điều khoản khi tự động hoá bằng subscription và luân phiên account; API adapter trực tiếp chỉ khi có nhu cầu thật |

### Thứ tự & phụ thuộc

1. P0-1 (độc lập, nhỏ nhất) → P0-2 bước 1.
2. P1-1 trước P1-2 (cùng chạm tập file bảo vệ / snapshot). P1-3 độc lập. P1-4 trước P0-2 bước 2 nếu verification được đưa vào sandbox của Codex (env đi theo).
3. P1-6 và P1-7 cùng chạm `progress.py`/`app.js`: làm cùng một đợt để tránh xung đột.
4. P1-9 chạy song song: mỗi mục ở trên mang test riêng.

Không đề xuất rewrite: các vấn đề nằm ở điểm cụ thể, kiến trúc hiện tại đủ để sửa tại chỗ.
