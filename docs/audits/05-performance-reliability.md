# Phase 5 — Hiệu năng và độ tin cậy (performance & reliability)

## Phạm vi, phương pháp, môi trường

- **Phạm vi:** dashboard web (`bridge/webui.py`, `bridge/progress.py`, `bridge/static/app.js`), các view CLI và TUI, SQLite (`bridge/state.py`, `bridge/storage.py`), dispatch task, slot tài khoản giữa nhiều process, sổ cái usage, retry/timeout/cancel, crash recovery, lỗi lưu trữ.
- **Môi trường:** macOS 26.5 (25F71), Apple M2 8 nhân, 16 GiB RAM; Python 3.12.11 (`.venv`); SQLite 3.54.1 (module `sqlite3`); HEAD `0541153` (nhánh `main`).
- **Cô lập:** mọi thí nghiệm chạy qua wrapper `run.sh` với `HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `AGENT_BRIDGE_HOME` trỏ vào thư mục scratch (dưới đây gọi là `$W`), `PATH=<repo>/tests/fakes:/usr/bin:/bin`, `unset AGENT_BRIDGE_AGENT`, repo git dùng một lần tạo trong `$W/repos/*`. Không CLI agent thật nào chạy. Không gọi tool `mcp__agent-bridge__*`.
- **Sự cố cô lập đã kiểm tra:** một lần chạy `agent-bridge config-check` lỡ chạy với cwd là repo kit, nên đã **đọc** `.ai/orchestrator.yaml` thật. Lệnh này chỉ load config (`bridge/cli.py:172-181`, không tạo `Bridge`). Sau đó `git status --porcelain` không có thay đổi nào ngoài các file của user và các báo cáo audit. mtime của `.git/agent-bridge/bridge.db` và `bridge.db-wal` vẫn là ngày 2/11, tức không có ghi nào. Báo cáo không chép tên agent hay tài khoản nào từ thông báo lỗi đó.
- **Seed dữ liệu:** `seed.py` tạo task bằng API thật `Bridge.create_task` (`bridge/service.py:563`). Sau đó dùng **SQL trực tiếp** (đúng các cột bridge ghi) để phân phối state (70% DONE, 10% CANCELLED, 5% mỗi loại REVIEW/BLOCKED/PAUSED/READY), thêm 6 event `transition` và 1 attempt cho mỗi task. Bộ dữ liệu: `p100`, `p1000`, `p5000` (100/1.000/5.000 task) và `s01..s10` (30 task mỗi project), tổng 13 project, khoảng 6.400 task, khoảng 45.000 event. Các thí nghiệm sau thêm project vào registry (33 project lúc đo endpoint log).
- **BLOCKED (theo brief, vì tốn quota thật):** `scripts/check.sh --e2e`, `--with-health`, `CODEX_SMOKE=1`, `tests/e2e/test_real_*`. Hành vi Landlock (Linux) chỉ được đọc code, chưa chạy thật trên host này.
- **Không đo được hoặc không mô phỏng:** đĩa đầy (không mô phỏng), tính nhất quán khi quota failover (không chạy), độ công bằng của `slot_wait` (chỉ đọc code), hủy task trong pha verification (không đo lại).

## 1. Hiệu năng

### 1.1 Dashboard web

Client đo (`bench_web.py`) chạy `agent-bridge web --port 0 --read-only`, đọc launch code từ stdout, gọi `GET /?code=…` để nhận cookie `bridge_session` (303), sau đó gọi các endpoint bằng `http.client` với thread pool.

| Endpoint (13 project, ~6.400 task) | Concurrency | n | p50 | p95 | p99 | Throughput | Payload |
|---|---|---|---|---|---|---|---|
| `/api/overview` (lần đầu, cache lạnh) | 1 | 1 | 1018 ms | | | | 504 KiB |
| `/api/overview` | 1 | 30 | 758 ms | 1056 | 1162 | 1,3 req/s | 516.203 B |
| `/api/overview` | 10 | 60 | 3160 ms | 4028 | 4152 | 3,0 req/s | 516.203 B |
| `/api/projects/<p5000>/tasks/T0042` | 1 | 30 | 419 ms | 600 | 786 | 2,3 req/s | 1.047 B |
| idem | 10 | 60 | 1352 ms | 1522 | 1538 | 7,4 req/s | 1.047 B |
| `…/dispatch/tasks/T0001/log?n=1&offset=0` (33 project) | 1 | 30 | 502 ms | 861 | 863 | 1,9 req/s | 470 B |
| `…/dispatch/tasks/T0001` (33 project) | 1 | 30 | 471 ms | 548 | 624 | 2,1 req/s | 907 B |
| `/static/app.js` | 10 | 50 | 2 ms | 31 | 34 | 1277 req/s | 91.879 B |
| `/static/app.css` khi có 10 luồng gọi overview liên tục | 1 | 20 | 62 ms | 261 | 269 | 13 req/s | 34.407 B |

RSS server: 101 MiB sau request đầu, 174–175 MiB sau loạt tải (`ps -o rss=`). CPU 92% khi bị gọi dồn.

Gọi `progress.snapshot()` trực tiếp (`bench_snapshot.py`, trung vị 5 lần):

| Roots | Lạnh | Ấm (cache hit) | Payload |
|---|---|---|---|
| chỉ `p100` | 38 ms | 32 ms | 43 KiB |
| chỉ `p1000` | 95 ms | 57 ms | 340 KiB |
| chỉ `p5000` | 233 ms | 130 ms | 460 KiB (cắt còn 1.000 task) |
| tất cả (13 project, `discover_roots`) | 893 ms | 696 ms | 504 KiB |

cProfile một lần snapshot lạnh (0,954 s): 39 lần `subprocess.run` mất 0,520 s cộng dồn (`select.poll` 0,429 s). `_read` mất 0,337 s. `find_store → git_dir` mất 0,193 s. `discover_roots → with_worktrees` mất 0,156 s. `_tasks` mất 0,156 s. Khi cache ấm vẫn có 78 lần `poll` tổng 0,580 s. Như vậy cache theo mtime **không** bao các lệnh git.

Cách UI poll (`bridge/static/app.js:1337-1352`): gọi `/api/overview` mỗi 3 s (5 s khi mất kết nối) và dừng khi tab bị ẩn. Lần nào cũng nhận **toàn bộ** payload, không có ETag hay dữ liệu tăng dần (`webui.py:39` đặt `Cache-Control: no-store`). Log của attempt đang chạy được poll mỗi 2 s (`app.js:966`). Một tab đang mở chi tiết một task đang chạy tạo khoảng 0,33 req/s overview cộng 0,5 req/s log, tức khoảng 0,25 + 0,25 s CPU server mỗi giây ở quy mô trên. Chỉ cần vài tab là chạm mức trần ~1,3–3 req/s.

### 1.2 CLI và TUI

`bench_cli.sh`: trung vị 5 lần chạy, wall-clock, với fake trên PATH.

| Lệnh | p100 | p1000 | p5000 |
|---|---|---|---|
| `list --json` | 296 ms | 301 | 289 |
| `list --limit 200 --json` | 271 | 292 | 322 |
| `status T0042` (`rerun.sh`, rc=0) | 289–601 (trung vị 316) | — | 262–333 (trung vị 296) |
| `usage --json` | 393 | 269 | 263 |
| `health --json` | 494 | 525 | 597 |
| `agents --json` | 441 | 385 | 348 |

`tasks --json` trên 13 project mất 676 / 924 / 1412 ms, RSS tối đa 44 MB (`/usr/bin/time -l`). Riêng `import bridge.cli` mất 0,17 s, trong đó `bridge.orchestrator` chiếm 106 ms (`-X importtime`). Kết luận: thời gian các view CLI gần như **không đổi theo số task**. Chi phí chủ yếu là khởi động Python và các lệnh git. TUI headless (`tui_start.py`, `BridgeApp.run_test`): import mất 707 ms, render đầy đủ lần đầu mất 693 ms (p100) và 329 ms (p5000, 200 dòng). Mỗi lần `refresh_tasks` (chạy mỗi 3 s, `tui.py:342`) mất 11–30 ms.

### 1.3 SQLite

Pragma đọc trên một kết nối mới mở như `_connect` (`eqp.py`): `journal_mode=wal`, `busy_timeout=10000` (từ `timeout=10`, `state.py:230`), `wal_autocheckpoint=1000`. `synchronous=2` (FULL) là **mặc định của bản build SQLite**; `Store` không tự đặt giá trị này. Index chỉ có `idx_tasks_state`, `idx_events_task`, `idx_account_usage_key_ts` cùng các autoindex PK (`state.py:77-78,99`).

EXPLAIN QUERY PLAN trên bản sao của DB p5000 (5.000 task, 35.000 event), trung bình 20 lần:

| Truy vấn | Plan | ms |
|---|---|---|
| create dedup `WHERE spec_hash=? AND state NOT IN …` | `SCAN tasks` | 0,73 |
| `list` `ORDER BY created_at DESC LIMIT 50` | `SCAN tasks; USE TEMP B-TREE FOR ORDER BY` | 7,79 |
| `overview` `ORDER BY updated_at DESC` | `SCAN tasks; TEMP B-TREE` | 1,27 |
| `workflow_tasks` `WHERE workflow_id=?` | `SCAN tasks USING INDEX sqlite_autoindex_tasks_1` | 0,99 |
| `get`, `events(task)`, `attempts(task)`, `active_on_account` | `SEARCH … USING INDEX` | ≤0,01 |
| `_read` của dashboard: `SELECT * FROM events` | `SCAN events` | 52,37 |

Tốc độ `create_task` qua API: 0,59 ms/task (100 và 1.000 task), 0,93 ms/task (5.000), khoảng 3,6 ms/task (từ 5.000 lên 10.001 task: 18,1 s). Nguyên nhân là `COUNT(*)` và quét `spec_hash` mỗi lần tạo (`state.py:587-597`).

### 1.4 Chi phí dispatch

`dispatch.sh`: 5 lần `create` + `execute` với fake codex (scenario `ok`). Mốc thời gian lấy từ bảng `events`:

```
T0003 created:READY@0ms transition:RUNNING@401ms … transition:VERIFYING@1090ms attempt_finished@1625ms transition:REVIEW@1626ms | agent run 1223 ms
T0004 … RUNNING@454ms … VERIFYING@829ms … REVIEW@1052ms | agent run 596 ms
fake codex runs: 5 durations ms: [8, 34, 10, 2, 2]
/usr/bin/time -l: 1.02–3.20 real, 0.44–0.55 user, ~41 MB max RSS
```

cProfile một lần `python -m bridge execute` (`prof_exec.sh`): tổng 1,233 s, trong đó 38 lần `subprocess.run` mất 0,690 s và 29 lệnh `review._git` mất 0,582 s (`snapshot` ×3 mất 0,494 s, `diff` ×2 mất 0,403 s). Trên repo nhỏ, phần chi phí của bridge (0,4–2,4 s) lớn hơn nhiều so với thời gian chạy của agent giả (2–34 ms). Agent thật chạy hàng phút nên chi phí này không phải nút thắt.

## 2. Đồng thời (concurrency)

- **Slot tài khoản giữa các process (đã xác nhận đúng):** `race.sh` cho 6 process `agent-bridge execute` ở 6 project tranh tài khoản `work` có `max_concurrency: 2`, fake agent `slow` 3 s. Kết quả: 2 task lên REVIEW, 4 task bị từ chối với `account work is busy in another bridge process`. Độ chồng lấn tối đa của các process agent (đo bằng `t_start`/`t_end` trong log của fake) là **2**. Cơ chế là `flock` không chặn trên N file slot (`accounts.py:405-427`, `osutil.py:30-41`).
- **Sổ cái ngân sách (đã xác nhận đúng):** `contend.py` cho N process gọi `Store.reserve_usage` vào cùng một `accounts.db` với ngân sách $100 và mỗi lần giữ $1, kèm một `store.log` vào DB riêng.

  | N process | ops | wall | p50 | p99 | granted | lỗi lock |
  |---|---|---|---|---|---|---|
  | 1 | 60 | 0,25 s | 0,4 ms | 0,8 ms | 60 | 0 |
  | 4 | 240 | 1,57 s | 19,4 ms | 71,9 ms | 100 | 0 |
  | 8 | 480 | 4,05 s | 55,6 ms | 123,7 ms | 100 | 0 |
  | 16 | 960 | 15,64 s | 232,9 ms | 450,1 ms | 100 | 0 |

  Không có lần chi vượt nào (đúng 100 dòng, $100.0) và không có lỗi `database is locked`. Độ trễ tăng theo N vì `_dead_owners` chạy `ps` cho mỗi owner còn sống trước khi giao dịch (`state.py:393-418`). Trong thực tế số owner bị giới hạn bởi `max_concurrency ≤ 8`.
- **Idempotency (đã xác nhận đúng):** `idem.sh` chạy hai `execute T0001` đồng thời. Một lệnh vào REVIEW, lệnh kia trả về `task T0001 is RUNNING, expected one of [READY]`. Chỉ có 1 lần chạy agent. `resume` khi task đang RUNNING bị từ chối. Tạo lại cùng spec trả về `"duplicate": true`. Quy tắc một writer cho mỗi worktree được thực thi trong `transition` (`state.py:654-669`).
- **Độ công bằng của `slot_wait`:** chỉ đọc code (REL-06).
- **Quota failover:** không chạy (INSUFFICIENT EVIDENCE).

## 3. Độ tin cậy

- **Retry mạng:** `net.sh` và `rerun.sh`. Với `network,ok`: 2 lần chạy agent, wall 11,4 s (backoff cố định 10 s, `runner.py:197-211`, `config.py:56-57`), task vào REVIEW, `attempts: 1`. Với `network,network` (project mới): 2 lần chạy, wall 10,9 s, task thành `PAUSED`, `failure: NETWORK: stream disconnected…`, `attempts: 0`. Lỗi môi trường không tiêu ngân sách attempt, đúng như thiết kế.
- **Timeout:** `termignore.sh` mục 1, `timeout_s: 30`, agent bỏ qua SIGTERM. Runner kết thúc sau 34,1 s, task `FAILED`, `TIMEOUT:`, cả agent lẫn process con `sleep 600` đều chết (TERM, 5 s grace, rồi KILL, `osutil.py:51-66`).
- **Cancel:** `cancel.sh` (agent `hang`). CLI `cancel` mất **1189 ms**, runner thoát sau 1257 ms, process agent và con `sleep 600` đều chết (`ps` trống), event `attempt_finished` có `exit_code -15`. Nhận định "~1 s" được **revalidated** cho agent tôn trọng SIGTERM. Với agent bỏ qua SIGTERM, cancel mất 6471 ms (REL-07). Cancel trong pha verification không được đo lại.
- **Crash recovery:** `crash.sh` và `termignore.sh` mục 3 (REL-01, REL-02, REL-03). Việc kiểm tra start time của runner và agent trước khi coi pid là hợp lệ hoặc trước khi kill có trong code (`service.py:1020-1024`, `executor.py:671-675`). Phát hiện REL-04 "trusts a bare pid" của audit cũ trông như đã được sửa (chỉ đọc code).
- **Độ bền ghi:** WAL với `synchronous=FULL` (mặc định build) nên mỗi commit đều fsync WAL. Trên macOS, SQLite mặc định `fullfsync=0`, nên `fsync` không xả cache của ổ. Rủi ro mất commit cuối khi mất điện là Hypothesis, chưa kiểm chứng và không thành finding riêng.
- **Lỗi lưu trữ:** `ro.sh` và `rerun.sh` (REL-04, REL-05). Đĩa đầy không được mô phỏng.

## Phát hiện

### PERF-01 — `/api/overview` quét lại mọi project và chạy 3 lệnh git cho mỗi project trong mọi request; UI poll toàn bộ payload mỗi 3 s
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: `bridge/webui.py:159` gọi `progress.snapshot(roots)` với `roots=None`. `progress.py:32-39` (`discover_roots` gọi `registry.with_worktrees`, mỗi root một `git worktree list`, `registry.py:73-85`). `progress.py:78-84` (`_read` gọi `storage.find_store`, rồi `install.git_dir` chạy `git rev-parse`, `install.py:43-44`). `progress.py:431` (thêm một `git worktree list` cho mỗi project). `app.js:1352` (`setTimeout(poll, 3000)`). Đo bằng `bench_web.py`: với 13 project và ~6.400 task, c=1 p50 758 / p99 1162 ms, c=10 p50 3160 / p99 4152 ms, 1,3–3,0 req/s, mỗi phản hồi 516.203 B. cProfile: 39 lần `subprocess.run` = 0,520 / 0,954 s. Khi cache ấm vẫn mất 696 ms. Static file p95 tăng từ 31 lên 261 ms khi có 10 luồng gọi overview.
- Expected vs actual: Mong đợi một lần poll rẻ hơn nhiều so với chu kỳ 3 s và tăng theo lượng dữ liệu thay đổi. Thực tế một request tốn ~0,7–1 s CPU server, tăng tuyến tính theo số project và không được cache, payload ~0,5 MB luôn gửi đầy đủ.
- Root cause & impact: Các thông tin topology git (`git_dir`, danh sách worktree) được tính lại trong mọi request. Cache chỉ bao phần đọc SQLite. Trong lúc có run đang chạy, WAL của project đó thay đổi sau mỗi event, nên project này gần như luôn cache miss: con số "ấm" là trường hợp tốt nhất. Với khoảng 3 tab, hoặc khi số project tăng, độ trễ vượt chu kỳ poll, UI bị chậm và server chiếm một nhân CPU.
- Options & trade-offs: (a) Cache `find_store`/`git_dir`/worktree list theo TTL (ví dụ 30 s) hoặc theo mtime của `.git/worktrees`. Rẻ nhất, nhưng worktree mới xuất hiện chậm tối đa một TTL. (b) Thêm `ETag` hoặc `If-None-Match` dựa trên chữ ký mtime của mọi DB để trả 304: tiết kiệm băng thông nhưng vẫn tốn chi phí git nếu không làm (a). (c) Gửi dữ liệu tăng dần (`since=`) cho `tasks`: phức tạp hơn và cần thay đổi app.js.
- Acceptance criteria / verification method: Chạy lại `bench_web.py` trên bộ seed 13 project: `/api/overview` c=1 p50 < 150 ms khi cache ấm, số `subprocess.run` mỗi request bằng 0 khi topology không đổi (cProfile), và c=10 p95 < 1 s.

### PERF-02 — Endpoint chi tiết task và log chạy `discover_roots()` và dựng lại toàn bộ task của project cho mỗi request
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: `progress.py:604` (`task_detail`) và `progress.py:524` (`task_log`) gọi `discover_roots()`, tức `git worktree list` cho mỗi project đã đăng ký. `task_detail` còn gọi `_tasks(data)` trên mọi task và duyệt mọi event của project (`progress.py:609, 626-630`). `task_log` duyệt mọi attempt (`progress.py:526-528`). UI poll log mỗi 2 s khi attempt còn chạy (`app.js:966`). Đo: với 13 project, `GET /api/projects/<p5000>/tasks/T0042` (1.047 B) có p50 419 ms, c=10 p50 1352 ms. Với 33 project, `…/dispatch/tasks/T0001/log?n=1` (470 B) có p50 502 / p95 861 ms, và `…/tasks/T0001` có p50 471 ms.
- Expected vs actual: Mong đợi tra cứu theo khóa (`pid`, `tid`) mất vài ms. Thực tế mất nửa giây cho phản hồi dưới 1 KiB, và chi phí tăng theo số project trên máy chứ không theo task được hỏi.
- Root cause & impact: Map `pid → root` được tính lại bằng git subprocess mỗi lần gọi. Khi người dùng mở một task đang chạy, mỗi 2 s có thêm ~0,5 s CPU, cộng dồn với PERF-01.
- Options & trade-offs: Dùng chung cache `pid → root/store` với PERF-01. Lấy task và attempt bằng truy vấn có `WHERE id=?` (đã có index) thay vì dựng toàn bộ `_tasks`.
- Acceptance criteria / verification method: Endpoint log và detail có p50 < 50 ms với 33 project (`bench_log.py`), và không chạy subprocess khi topology không đổi.

### PERF-03 — Cache của dashboard giữ toàn bộ bảng của mọi project trong RAM, không bao giờ evict, và mọi ghi đều làm mất cache của cả project
- Severity: Low      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: `progress.py:105-110` đọc `SELECT *` của `tasks`, `workflows`, `events`, `account_quota`, `accounts` cùng các cột chính của `attempts`, rồi lưu vào `_CACHE` toàn cục (`progress.py:25`), không có giới hạn hay evict. Khóa của chữ ký là mtime và kích thước của DB và WAL (`progress.py:86-96`). Đo: RSS của server tăng từ 101 lên 175 MiB sau loạt request trên ~45.000 event. Snapshot của p5000 mất 233 ms khi lạnh so với 130 ms khi ấm. `SELECT * FROM events` trên 35.000 dòng mất 52 ms.
- Expected vs actual: Mong đợi bộ nhớ có giới hạn và chỉ đọc lại phần thay đổi. Thực tế bộ nhớ tăng theo tổng số event của mọi project (bảng `events` không có retention), và mỗi event mới buộc đọc lại toàn bộ project.
- Root cause & impact: Thiết kế cache "đọc hết rồi chiếu (projection) trong Python". Ở quy mô hiện tại vẫn chấp nhận được. Server chạy lâu với lịch sử lớn sẽ tăng RAM tuyến tính.
- Options & trade-offs: Chỉ đọc các event cần (`kind='transition'` trong 24 h, `account_auto_switched` trong 7 ngày) thay vì `SELECT *`. Chặn số entry của `_CACHE` bằng LRU. Bù lại là thêm vài truy vấn có điều kiện.
- Acceptance criteria / verification method: RSS ổn định (< 120 MiB) sau 1.000 lần gọi `/api/overview` trên bộ seed. Cold snapshot của p5000 < 100 ms.

### PERF-04 — Thiếu index cho `spec_hash`, `created_at`/`updated_at`, `workflow_id`; chi phí `create_task` tăng theo số task
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement
- Priority: P2
- Evidence: Schema tại `state.py:22-79` chỉ có `idx_tasks_state` và `idx_events_task`. EXPLAIN trên 5.000 task: dedup trả về `SCAN tasks`, `list` trả về `SCAN tasks; USE TEMP B-TREE FOR ORDER BY` (7,79 ms), `workflow_tasks` quét toàn bảng. `create` tốn 0,59 ms/task ở 1.000 task, 0,93 ms ở 5.000, ~3,6 ms ở mức 5.000→10.001 (`seed.py`, `recover_limit.py`). `tid = COUNT(*)+1` nằm ở `state.py:597`.
- Expected vs actual: Mong đợi truy vấn nóng là `SEARCH`. Thực tế là `SCAN`, tuy chỉ vài ms ở quy mô đã đo.
- Root cause & impact: Schema tối giản. Chưa phải nút thắt (dưới 10 ms), nhưng tăng tuyến tính và chạy trong `BEGIN IMMEDIATE` (tức giữ write lock lâu hơn).
- Options & trade-offs: Thêm `CREATE INDEX IF NOT EXISTS` cho `tasks(spec_hash)`, `tasks(created_at)`, `tasks(workflow_id)`. Chi phí là ghi chậm hơn một chút và DB lớn hơn.
- Acceptance criteria / verification method: `eqp.py` cho ra `SEARCH`/`USING INDEX` với cả ba truy vấn, và `create` ở 10.000 task < 1 ms/task.

### PERF-05 — Chi phí dispatch cố định ~0,4–2,4 s mỗi lần chạy, chủ yếu từ ~29 lệnh git
- Severity: Low      - Confidence: Confirmed
- Kind: Intentional limitation
- Priority: P2
- Evidence: Đo bằng `dispatch.sh`: từ RUNNING đến VERIFYING mất 596–2605 ms trong khi fake agent chỉ chạy 2–34 ms. Trong cProfile (`prof_exec.sh`), `review._git` chiếm 29 lần gọi / 0,582 s trên tổng 1,233 s (`review.py:29, 83, 173, 291`).
- Expected vs actual: Chi phí này là cái giá của việc thu bằng chứng (snapshot trước và sau, diff, recheck). Nó không đáng kể so với thời gian chạy hàng phút của agent thật. Trên repo lớn, thời gian của `_worktree_tree` và `diff` sẽ tăng theo kích thước repo; điểm này chưa được đo.
- Root cause & impact: Mỗi bước kiểm tra chạy git riêng. Ảnh hưởng chủ yếu đến các workflow có nhiều task nhỏ.
- Options & trade-offs: Gộp các lệnh `git status`/`ls-files` dùng chung trong một attempt. Không nên đổi lấy việc làm yếu đi bằng chứng.
- Acceptance criteria / verification method: Đo lại trên repo có ≥10.000 file trước khi quyết định tối ưu.

### REL-01 — `kill -9` runner để lại agent mồ côi vẫn chạy, slot tài khoản được nhả, và view chỉ-đọc báo RUNNING vô thời hạn
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: Chạy `crash.sh` (fake `hang`, `max_concurrency: 1`):
  ```
  after kill -9 runner: agent/child alive?
  47929     1 …Python   (agent, PPID 1)
  47953 47929 sleep 600
  read-only views: "state": "RUNNING" (status), "state": "RUNNING" (list)
  web overview running list: [('T0001', 'build')]
  second execute in another project on the same account while the orphan runs: "state": "REVIEW"
  writer command (create) → T0001 "PAUSED", "INTERRUPTED: runner process exited during execution"
  ```
  `recover()` chỉ chạy trong các lệnh CLI không chỉ-đọc (`cli.py:258-265`), lúc khởi động MCP server (`server.py:42`) và TUI (`tui.py:725`). Web dashboard và các lệnh `status`/`list`/`health`/`usage` không chạy nó. Slot được giữ bằng `flock` (`accounts.py:419`), và kernel tự nhả lock khi process chết.
- Expected vs actual: Mong đợi agent không vượt quá `max_concurrency` của tài khoản và trạng thái hiển thị phản ánh việc runner đã chết. Thực tế, sau khi runner chết, agent mồ côi vẫn chạy (vẫn tiêu quota, vẫn có thể ghi vào worktree) trong khi lock đã nhả. Một run khác trên cùng tài khoản đã khởi động được, nên có 2 agent chạy đồng thời trên một tài khoản có `max_concurrency: 1`. Các view tiếp tục báo RUNNING cho đến khi có một lệnh ghi trong đúng project đó.
- Root cause & impact: Lock thuộc về runner chứ không thuộc về process agent. Việc dọn dẹp chỉ xảy ra khi có lệnh ghi tiếp theo trong cùng worktree. Nguyên nhân có thể là OOM, `kill -9`, phiên Claude hoặc MCP bị đóng đột ngột. Hệ quả là vượt rate limit hoặc quota của tài khoản, và người dùng bị dashboard đánh lừa.
- Options & trade-offs: (a) Khi cần lock, `AccountGate`/`AccountLock` kiểm tra thêm các task ACTIVE có runner đã chết (trên mọi project đã đăng ký) và `kill_stale` chúng trước. (b) Truyền fd của lock sang process agent (`pass_fds`) để lock chỉ nhả khi agent cũng chết. Cách này đơn giản và đúng ngữ nghĩa, nhưng agent con có thể giữ lock lâu hơn dự kiến. (c) Đánh dấu "runner dead" ngay trong view chỉ-đọc (`progress.py` đã có `runner_pid`), không cần ghi.
- Acceptance criteria / verification method: Chạy lại `crash.sh`: lệnh execute thứ hai trên cùng tài khoản bị từ chối (busy) hoặc chỉ chạy sau khi orphan đã bị kill; `status` và web báo runner đã chết.

### REL-02 — `recover()` chỉ gửi SIGTERM cho nhóm process mồ côi; agent bỏ qua TERM vẫn sống trong khi task đã được đánh dấu PAUSED và có thể resume
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: `executor.py:671-678` (`kill_stale` gọi `osutil.kill_group(pid, force=False)`, không chờ và không leo thang lên KILL). Kết quả được ghi là `killed_orphan: true` (`service.py:1048-1051`). Chạy `termignore.sh` mục 3 với wrapper **tổng hợp** `trap "" TERM; exec tests/fakes/codex`:
  ```
  "state": "PAUSED"
  survivors 2 s after recover: 21768 …Python;21883 sleep 600;
  ```
  Đối chiếu: đường timeout (`osutil.end_group`, TERM → 5 s → KILL) đã kill được cùng agent đó (mục 1).
- Expected vs actual: Mong đợi một task đã được "recover" không còn process nào chạy. Thực tế agent vẫn sống, task ở trạng thái PAUSED nên `resume` có thể khởi động agent thứ hai trong cùng worktree, tức hai writer.
- Root cause & impact: `kill_stale` là đường đồng bộ, không có bước grace hay KILL. Chưa xác minh codex hoặc claude thật có bỏ qua SIGTERM hay không (kiểm thử chỉ dùng wrapper giả). Mức ảnh hưởng phụ thuộc vào hành vi đó và vào các process con mà agent sinh ra (shell, test runner).
- Options & trade-offs: Dùng lại logic `end_group` (TERM → grace → KILL) trong `kill_stale`. Như vậy `recover` có thể chặn tối đa vài giây. Có thể đánh dấu BLOCKED thay vì PAUSED nếu `group_alive` vẫn đúng sau KILL.
- Acceptance criteria / verification method: Chạy lại `termignore.sh` mục 3: sau `recover` không còn survivor nào; có test đơn vị với agent bỏ qua TERM.

### REL-03 — `recover()` (và `health`) chỉ xét 10.000 task mới nhất; task ACTIVE cũ hơn không bao giờ được recover và chặn worktree
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `service.py:1029` (`self.store.list(limit=10_000)`, `ORDER BY created_at DESC`, `state.py:642-647`). Tương tự tại `service.py:409, 411` (health). Chạy `recover_limit.py` với 10.001 task, đặt `T0001` và `T5000` sang RUNNING với runner pid 999999 (đã chết):
  ```
  recover() returned ['T5000'] in 138 ms
  T0001: RUNNING | T5000: PAUSED
  T0002 -> RUNNING refused: account default is already running 1 task(s)
  ```
- Expected vs actual: Mong đợi mọi task ACTIVE có runner chết đều được recover. Thực tế `T0001` kẹt ở RUNNING và mọi run mới trong worktree bị từ chối (thông báo còn đổ lỗi cho tài khoản).
- Root cause & impact: Truy vấn có `LIMIT` và sắp theo thời điểm tạo thay vì lọc theo state. Điều kiện xảy ra hiếm: hơn 10.000 task trong một worktree, và một task cũ được execute muộn rồi crash, vì gần như mọi lệnh ghi đều chạy `recover` trước. Có thể gỡ bằng `agent-bridge cancel`.
- Options & trade-offs: Dùng `SELECT … WHERE state IN (ACTIVE)`. Truy vấn này đã có `idx_tasks_state`, vừa nhanh hơn vừa đúng. Không có trade-off đáng kể.
- Acceptance criteria / verification method: Chạy lại `recover_limit.py`: cả hai task đều vào PAUSED.

### REL-04 — DB hỏng hoặc chỉ-đọc làm CLI (kể cả `health`) in traceback Python; `tasks` lặng lẽ bỏ qua project hỏng; không có kiểm tra integrity hay backup
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `ro.sh` cắt `bridge.db` còn 4 KiB và xóa WAL/SHM, sau đó chạy `agent-bridge health`: `rc=1`, có `Traceback`, dòng cuối là `sqlite3.DatabaseError: database disk image is malformed` (phát sinh từ `Store.__init__ → _use_wal`, `state.py:250`). `list` và `create` cũng in traceback. `agent-bridge tasks --json` vẫn trả rc 0 nhưng project hỏng (`ro`) biến mất khỏi danh sách (`service.py:1084-1087`, `except sqlite3.Error: continue`). Dashboard thì xử lý đúng: `'error': 'unreadable'` (`progress.py:403-407`). Với DB chmod 400, `create` thất bại với traceback `attempt to write a readonly database`.
- Expected vs actual: Mong đợi `health` là công cụ chẩn đoán và báo "DB của project X hỏng" cùng hướng xử lý, còn `tasks` hiển thị project lỗi. Thực tế công cụ chẩn đoán tự crash, còn view đa project che mất lỗi.
- Root cause & impact: Không có lớp xử lý `sqlite3.DatabaseError` ở biên CLI và không chạy `PRAGMA quick_check`. Khi DB hỏng (đĩa đầy giữa checkpoint, sao chép file đang mở), người dùng chỉ thấy stack trace và không có đường khôi phục được tài liệu hóa.
- Options & trade-offs: Bắt `sqlite3.DatabaseError`/`OperationalError` ở `cli.main` và in một dòng lỗi có đường dẫn DB. `health` mở DB ở chế độ `mode=ro` và báo `quick_check`. `tasks` liệt kê project với `error: unreadable` giống dashboard. Có thể thêm `storage backup` (dùng `sqlite3.backup`).
- Acceptance criteria / verification method: Chạy lại `ro.sh`: `health` trả JSON có mục lỗi DB, không có traceback; `tasks` hiển thị project lỗi.

### REL-05 — `Store.__init__` luôn `chmod 0600` DB khi mở, kể cả với lệnh chỉ-đọc; lần ghi đầu thất bại, lần sau lặng lẽ thành công
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `state.py:251-252` (`path.chmod(0o600)` sau khi connect). Chạy `rerun.sh`:
  ```
  mode before create: 400
  create rc=1 sqlite3.OperationalError: attempt to write a readonly database
  mode after create: 600
  create again rc=0 "task_id": "T0002"
  list rc=0 mode after list: 600     (list là lệnh chỉ-đọc, Bridge(create=False))
  ```
- Expected vs actual: Mong đợi một DB mà người dùng cố ý đặt chỉ-đọc (ví dụ để đóng băng) vẫn giữ nguyên, và lệnh chỉ-đọc không thay đổi metadata. Thực tế quyền bị nới ra 0600 một cách lặng lẽ, và lần ghi đầu tiên có kết quả khác lần thứ hai.
- Root cause & impact: Việc siết quyền (chặn quyền đọc của group và other) được làm bằng chmod tuyệt đối. Ảnh hưởng nhỏ, nhưng làm vô hiệu một biện pháp bảo vệ thủ công và khiến chẩn đoán khó hơn.
- Options & trade-offs: Chỉ bỏ các bit group/other (`mode & ~0o077`) thay vì đặt 0600, và không chmod khi mở ở chế độ chỉ-đọc.
- Acceptance criteria / verification method: `stat -f %Lp` vẫn là 400 sau `list` và sau một `create` thất bại.

### REL-06 — `slot_wait` của workflow là backoff riêng từng process (5→60 s + jitter), không có hàng đợi giữa các process: có thể chờ lâu hoặc bị bỏ đói
- Severity: Low      - Confidence: Hypothesis
- Kind: Hypothesis
- Priority: P2
- Evidence: Chỉ đọc code. `service.py:43-47` (`SLOT_WAIT_FIRST_S = 5.0`, `SLOT_WAIT_MAX_S = 60.0`). `service.py:960-1000`: mỗi lần thất bại thì delay gấp đôi, tối đa 60 s, kết thúc khi đạt `routing.slot_wait_s` (mặc định 1800, `orchestrator.py:223`). Lock là `flock` không chặn (`accounts.py:419-427`), không có thứ tự FIFO. Một CLI `execute` mới có thể giành slot vừa nhả trong lúc workflow đang ngủ tới 60 s.
- Expected vs actual: Mong đợi task chờ lâu nhất được ưu tiên và slot không bị bỏ trống. Theo code, slot có thể trống tới ~61 s, và một workflow có thể hết `slot_wait_s` trong khi các process khác liên tục giành slot.
- Root cause & impact: Thiết kế cố ý đơn giản (comment `ponytail` ở `service.py:913`). Tác động chỉ rõ khi có nhiều process tranh một tài khoản.
- Options & trade-offs: Dùng `flock` chặn có timeout trong một thread, hoặc thêm một file vé (ticket) FIFO trong `locks/`. Cách nào cũng phức tạp hơn và cần test đa process.
- Acceptance criteria / verification method: Thí nghiệm hai workflow cùng một CLI execute lặp tranh một slot: đo thời gian chờ tối đa và số lần `slot_wait_expired`.

### REL-07 — Cancel với agent bỏ qua SIGTERM mất ~6,5 s, không phải "~1 s"
- Severity: Low      - Confidence: Confirmed
- Kind: Intentional limitation
- Priority: P2
- Evidence: `cancel.sh` (agent tôn trọng TERM): CLI mất 1189 ms. `termignore.sh` mục 2 (wrapper bỏ qua TERM): `cancel CLI wall ms 6471`, sau đó không còn survivor. Grace 5 s nằm ở `osutil.py:51-66`. `CANCEL_WAIT_S = 15` (`service.py:43-44`).
- Expected vs actual: Tài liệu dự án (`.claude/rules/orchestration.md`) viết "within ~1 s". Thực tế điều này chỉ đúng khi agent thoát vì TERM. Ngược lại phải cộng thêm 5 s grace.
- Root cause & impact: Grace TERM→KILL là có chủ đích. Chỉ cần nêu rõ trong tài liệu.
- Options & trade-offs: Sửa tài liệu thành "~1 s, tối đa ~6 s nếu agent bỏ qua SIGTERM".
- Acceptance criteria / verification method: Tài liệu khớp với số đo.

## Kết luận theo từng hạng mục

| Hạng mục | Verdict | Lý do ngắn |
|---|---|---|
| Hiệu năng dashboard web | NEEDS IMPROVEMENT | PERF-01/02/03: ~0,5–1 s mỗi request, git subprocess không được cache, payload đầy đủ mỗi 3 s |
| Hiệu năng CLI/TUI | GOOD | 0,26–0,6 s, không đổi theo số task. TUI refresh 11–30 ms |
| SQLite (schema, WAL, giao dịch) | GOOD | WAL + `BEGIN IMMEDIATE` + busy 10 s, 16 process không lỗi lock. Thiếu index là vấn đề nhỏ (PERF-04) |
| Dispatch | GOOD | Chi phí cố định 0,4–2,4 s, có chủ đích (PERF-05) |
| Slot tài khoản và sổ cái ngân sách | GOOD | Peak 2/2 với 6 process. Đúng $100/$100 với 16 process |
| Quota failover | INSUFFICIENT EVIDENCE | Không chạy |
| Độ công bằng `slot_wait` | INSUFFICIENT EVIDENCE | Chỉ đọc code (REL-06) |
| Cancel, timeout, retry, idempotency | GOOD | Cancel 1,2 s, timeout kill cả nhóm process, retry mạng không tiêu ngân sách, execute song song được tuần tự hóa |
| Crash recovery | NEEDS IMPROVEMENT | REL-01, REL-02 (orphan vượt slot hoặc sống sót), REL-03 |
| Lỗi lưu trữ và chẩn đoán | NEEDS IMPROVEMENT | REL-04, REL-05. Đĩa đầy: INSUFFICIENT EVIDENCE (không mô phỏng) |

## Phụ lục: tóm tắt script tái lập (trong `$W`, xóa sau phiên)

- `env.sh` / `run.sh`: thiết lập môi trường cô lập (HOME, CODEX_HOME, CLAUDE_CONFIG_DIR, AGENT_BRIDGE_HOME trong `$W`; PATH = `tests/fakes`, `/usr/bin`, `/bin`).
- `seed.py <name> <n> h`: tạo task qua `Bridge.create_task`, rồi dùng SQL trực tiếp cho state, event và attempt (mô tả ở phần phương pháp).
- `bench_snapshot.py`, `bench_web.py`, `bench_log.py`: đo `progress.snapshot`, HTTP p50/p95/p99 và cProfile.
- `bench_cli.sh`, `tui_start.py`, `eqp.py`, `dispatch.sh`, `prof_exec.sh`.
- `mkproj.sh <name> [max_concurrency] [timeout_s]`: repo cùng `.ai/orchestrator.yaml` (tài khoản `work`, agent `codex-work` là fake, reviewer `client`).
- `race.sh`, `contend.py`, `idem.sh`, `cancel.sh`, `crash.sh`, `termignore.sh`, `recover_limit.py`, `net.sh`, `rerun.sh`, `ro.sh`.
- Sau mọi thí nghiệm, `ps -axo pid,ppid,command | grep -E "sleep 600|tests/fakes|agent-bridge (web|execute)"` trả về rỗng: không còn process sót lại.
