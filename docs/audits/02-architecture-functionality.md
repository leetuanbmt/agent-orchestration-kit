# Phase 2 — Kiến trúc & Tính đúng chức năng (`bridge/`)

## Phạm vi, phương pháp, môi trường

- **Phạm vi:** package `bridge/` (~9.2k LOC): ranh giới module, phân lớp, state machine, persistence, lifecycle task/workflow, routing đa agent/đa account, multi-bridge, tính nhất quán giữa CLI / MCP / TUI / web.
- **Phương pháp:** đọc code (models.py, state.py, service.py, runner.py, gate.py, accounts.py, orchestrator.py, server.py, cli.py, webactions.py, tui.py) và chạy 9 script thử nghiệm với `Bridge` API và các tiến trình con thật (không phải mock), dùng fake CLI trong `tests/fakes`. Mỗi script tạo HOME, AGENT_BRIDGE_HOME và git repo mới trong scratchpad, unset `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `AGENT_BRIDGE_AGENT`, và đặt `tests/fakes` đứng đầu PATH. Không binary agent thật nào được chạy.
- **Môi trường:** macOS 26.5 (darwin), Python 3.12.11, HEAD `0541153`.
- **BLOCKED / không chạy:** `scripts/check.sh --e2e`, `--with-health`, `tests/e2e/test_real_*` (sẽ tốn quota thật, theo brief). Lần chạy `pytest tests/unit tests/integration` riêng của tôi bị permission hook từ chối, nên báo cáo này **không** khẳng định trạng thái của test suite. Landlock (`verify_sandbox`) chỉ có trên Linux: phần đó chỉ được đọc code.
- Không dựa vào các báo cáo cũ trong `docs/audits/*.md`.

## 1. Kiến trúc

### Phân lớp

```
CLI (cli.py) ─┐
MCP (server.py) ─┼─> Bridge (service.py) ─┬─> TaskRunner (runner.py) ─> executor.py adapters
TUI (tui.py) ─┘                         ├─> AccountGate (gate.py)  ─> quota.py, accounts.AccountLock
Web (webui.py) ─> webactions.py ─> subprocess `python -m bridge <cmd>` (CLI)
progress.py ─> đọc SQLite trực tiếp (mode=ro) cho web/overview
                                   Store (state.py) ─> bridge.db (per worktree) + accounts.db (machine-wide)
```

- **Điểm tốt:** mọi thao tác thay đổi trạng thái đi qua `Bridge`. Web dashboard không gọi service trực tiếp mà gọi CLI qua subprocess (`webactions.py:104`), nên CLI, MCP, TUI và web dùng cùng `execute_task` / `resume_task` / `review_task` / `cancel_task`, cùng kiểm tra trạng thái và cùng actor `CLIENT`. Riêng việc human approve chỉ có ở CLI (`approve` với `_human_confirm`) và TUI (`tui.py:552`), không có trên MCP và web. Phần này đúng thiết kế.
- **State machine khai báo tập trung:** `models.TRANSITIONS` (`models.py:38-69`), và `Store.transition` (`state.py:649`) kiểm tra transition cùng actor, ghi event trong một `BEGIN IMMEDIATE`. Tôi đã thử các transition bất hợp lệ và tất cả đều bị từ chối (xem mục 2).
- **Persistence:** SQLite WAL, một DB cho mỗi worktree và một `accounts.db` dùng chung cho cả máy (trạng thái account, quota, usage ledger). Migration chỉ có loại "add column" (`_add_columns`, `state.py:289`) trong một transaction. Không có số phiên bản schema (xem ARC-03).
- **Điểm yếu:** `Bridge`, `TaskRunner` và `AccountGate` gọi private method của nhau theo cả hai chiều (ARC-01). `progress.py` là read model thứ hai, tự viết SQL trên schema (ARC-03). Logic retry của workflow dựa vào việc so khớp chuỗi thông báo lỗi (ARC-02).
- **Extensibility của adapter:** `executor.ADAPTERS` là registry theo tên, mỗi agent có một adapter. Routing (`gate._unavailable`) chỉ cần `available()` và `can(need)`. Thêm adapter mới không phải sửa routing, trừ khi adapter có quy tắc riêng như `_unscopable` của claude-cli (`gate.py:223`), vốn được hard-code theo tên adapter. Mức này chấp nhận được.

### Điểm phức tạp tập trung

`service.py` (1102 dòng) gồm health, accounts, review, workflow driver và recover. `_drive` (`service.py:~912-1006`) là hàm phức tạp nhất: một vòng lặp với slot backoff, phân loại lỗi và cập nhật trạng thái workflow.

## 2. Chức năng: kết quả thực nghiệm

Tất cả lệnh được chạy bằng `.venv/bin/python <script>` với harness cô lập như trên. Đầu ra dưới đây trích nguyên văn.

| # | Kịch bản | Kỳ vọng (`.claude/rules/orchestration.md`) | Thực tế |
|---|---|---|---|
| e1 | review/resume/human_approve trên READY; execute trên REVIEW; cancel DONE | từ chối | `TransitionError: T0001 is READY; review needs REVIEW`; `... resume needs one of [FAILED, PAUSED, REWORK]`; `illegal transition DONE -> CANCELLED` ✔ |
| e1 | depends_on không tồn tại / id tương lai (có thể tạo vòng) | từ chối | `NotFound: unknown dependency: T9999` / `T0003` ✔. Không thể tạo vòng phụ thuộc vì dependency phải tồn tại trước. |
| e1 | execute task BACKLOG | từ chối | `T0004 has unfinished dependencies` ✔ |
| e1 | dependency bị CANCELLED | (không quy định) | Task phụ thuộc ở **BACKLOG mãi mãi** (xem FUN-02) |
| e1 | risk_level high: approve → APPROVED → `human_approve` → DONE | đúng | `('APPROVED', 'Human approval required…')` → `DONE` ✔ |
| e2 | max_attempts=2, verification luôn fail | REWORK → BLOCKED | `exec#1: REWORK attempts 1` → `resume#2: BLOCKED attempts 2` ✔ |
| e2 | reviewer chọn rework ở attempt cuối, sau đó resume | REWORK → BLOCKED (bridge) | Đúng: `REVIEW→REWORK (client)`, `REWORK→BLOCKED (bridge)`, `BLOCKED: attempt budget exhausted`. Resume trả về summary BLOCKED thay vì lỗi. |
| e2 | crash, max_attempts=2 | FAILED → BLOCKED | `FAILED attempts 1` → `BLOCKED attempts 2` ✔ |
| e3 | quota → PAUSED, attempt được trả lại; account bị đánh dấu | đúng | `PAUSED attempts 0 QUOTA:`. Account `state: quota`. Task khác trên cùng account bị từ chối `account default unavailable (quota…)`. Sau `clear_account`: auth → `PAUSED attempts 0 AUTH:`, rồi ok → `REVIEW attempts 1` ✔ |
| e3 | resume task không ở trạng thái PAUSED (REVIEW) | từ chối | ✔ |
| e4 | pin agent không tồn tại / agent `client` (chỉ đọc) | từ chối | `unknown agent 'nope'`, `agent me cannot execute tasks` ✔ |
| e4 | execute task CANCELLED chưa có route | từ chối, không thay đổi gì | Bị từ chối, **nhưng row đã bị gắn `agent=ca, account=a` và có event `agent_routed`** (xem FUN-03) |
| e4 | workflow: T1 BLOCKED, T2 depends_on T1 | INCOMPLETE | **`WAITING`**, `waiting: {}` (xem FUN-02) |
| e5 | 2 tiến trình `run_workflow` cùng một workflow | một driver | Không có run trùng: guard ở `transition` giữ được. Nhưng driver 0 kết thúc `WAITING` trong khi T0002 vẫn `RUNNING` dưới driver 1. Chuỗi trạng thái: `RUNNING→WAITING→DONE` (xem FUN-04) |
| e6 | cancel khi RUNNING từ tiến trình khác; đồng thời execute từ 2 tiến trình | CANCELLED trong ~1 s, giải phóng lock, không đổi worktree | `cancel returned after s: 0.61`. Kết quả: `CANCELLED`, `runner_pid None`, attempt `cancelled`, `worktree: x = 1`, `lock free: True`. Tiến trình thứ hai: `ConflictError account default is busy in another bridge process` ✔ |
| e7 | SIGKILL runner, `recover()` ở tiến trình mới, sau đó resume | PAUSED (INTERRUPTED), giết agent mồ côi, giữ session, **không tốn attempt** | PAUSED, giết orphan, giữ session ✔. Nhưng `attempts` vẫn là 1. Với `max_attempts=1`: `resume: ('BLOCKED', 1)` (xem FUN-01) |
| e8 | multi-bridge: account bị repo khác giữ, `slot_wait_s=3` / `30` | chờ slot rồi WAITING / DONE | `(5.2s, WAITING, waiting: …busy in another bridge process)`, events `slot_wait`, `slot_wait_expired` ✔. Với 30: `(16.6s, DONE)` ✔. Hết hạn trễ hơn cấu hình (xem FUN-05). `plan` khi account planner bận thì lỗi ngay (xem FUN-06) |

## 3. Findings

### FUN-01 — Runner chết (recover) làm tiêu một attempt, trái quy tắc "PAUSED không tốn budget"
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: `service.py:1026-1050`: `recover()` chuyển sang PAUSED với `failure="INTERRUPTED: runner process exited during execution"` nhưng không đặt `attempts=used-1`. Ngược lại, `runner.py:338` (`_park_if_stopping`), `runner.py:250` (quota/auth) và `runner.py:329` (evidence error) đều trả lại attempt. Tài liệu `.claude/rules/orchestration.md` ghi "RUNNING / VERIFYING → PAUSED … interrupted runner (stop signal or dead runner)" và "Environment failures (PAUSED) do not consume the attempt budget". Tái hiện (e7b): task `max_attempts=1`, scenario `slow,ok`, `SIGKILL` tiến trình runner sau 2.5 s, rồi `Bridge(root).recover()` và `resume_task(wait=True)`:
  ```
  after recover: ('PAUSED', 'INTERRUPTED: runner process exited during execution', 1, True)
  resume: ('BLOCKED', 1)
  ```
  Với cấu hình mặc định (3 attempts), e7 cho `resume: ('REVIEW', 2)`: một attempt đã bị tính.
- Expected vs actual: kỳ vọng attempts=0 sau recover và resume chạy được. Thực tế attempts=1, resume chuyển ngay sang BLOCKED "attempt budget exhausted" mà agent không chạy.
- Root cause & impact: hai đường dẫn đến PAUSED (INTERRUPTED) xử lý budget khác nhau. Một lần crash, đóng phiên Claude hay kill MCP server sẽ ăn mất budget. Khi `max_attempts` nhỏ, task bị BLOCKED vĩnh viễn và người dùng phải tạo task mới.
- Options & trade-offs: (a) trong `recover()` đặt `attempts=max(row.attempts-1, 0)` khi chuyển PAUSED, nhất quán với `_park_if_stopping`. (b) Nếu cố ý (để chống vòng crash vô hạn), sửa tài liệu và ghi rõ lý do. Cách (a) có rủi ro: agent làm sập runner lặp lại sẽ không bao giờ hết budget. Có thể giới hạn bằng số event `runner died`.
- Acceptance criteria / verification method: test integration: kill runner, `recover()`, khẳng định `attempts == 0` và `resume` chạy agent với `max_attempts=1`.

### FUN-02 — Dependency CANCELLED/BLOCKED làm task phụ thuộc kẹt BACKLOG; workflow báo WAITING thay vì INCOMPLETE
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: `state.py:698` (`_promote_dependents`) chỉ chạy khi `dst == DONE`, và không có transition `BACKLOG → BLOCKED/CANCELLED` nào do bridge thực hiện. `service.py:996-1000`: `INCOMPLETE` chỉ khi **mọi** task đều terminal, còn lại là `WAITING`. Tài liệu ghi: "INCOMPLETE (some task BLOCKED or CANCELLED)". Tái hiện e4: planner trả về 2 task, task 1 crash với `max_rework: 0`, task 2 `depends_on [0]`:
  ```
  workflow after run: ('WAITING', {'tasks': {'T0002': 'BLOCKED', 'T0003': 'BACKLOG'}, 'waiting': {}}, …)
  ```
  e1 (task lẻ): `C after A cancelled: BACKLOG`.
- Expected vs actual: kỳ vọng INCOMPLETE, và task phụ thuộc phải được đánh dấu hoặc báo là không thể chạy. Thực tế là WAITING với `waiting: {}`. Workflow trông như đang chờ người dùng mà không nói chờ gì. Gọi `run_workflow` lại cũng cho cùng kết quả.
- Root cause & impact: không có cơ chế lan truyền thất bại qua dependency. Client (Claude) dễ poll hoặc chạy lại vô ích. Task BACKLOG mồ côi chỉ thoát được bằng cancel thủ công.
- Options & trade-offs: (a) khi một task vào trạng thái terminal không phải DONE, chuyển các task BACKLOG phụ thuộc vào nó sang BLOCKED (bridge) với `failure="dependency T… CANCELLED"`. Cần thêm `(BACKLOG, BLOCKED)` vào TRANSITIONS. (b) Không đổi state machine, chỉ sửa `_drive` để coi BACKLOG có dependency terminal-không-DONE là "dead", trả về INCOMPLETE và liệt kê lý do trong `waiting`. (b) nhỏ hơn. (a) đúng ngữ nghĩa hơn cho task ngoài workflow.
- Acceptance criteria / verification method: chạy lại kịch bản e4 thì workflow `INCOMPLETE` và T0003 có lý do rõ ràng. Cancel dependency của một task lẻ thì task phụ thuộc không còn BACKLOG vô thời hạn, hoặc có thông báo.

### FUN-03 — `execute_task` gắn route (ghi agent/account và event) trước khi kiểm tra trạng thái, kể cả với task terminal
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `service.py:567-574`: chỉ chặn BACKLOG, sau đó gọi `_quota_fallback` và `_route` trước khi `_start` kiểm tra `expect={READY}`. `gate.py:353`: `self.store.update(task_id, agent=name, account=account)`. `Store.update` (`state.py:706`) không kiểm tra trạng thái. `_unroute` chỉ chạy với `ConflictError`/`LimitExceeded`, không chạy với `TransitionError` hay ValueError "account unavailable" (`runner.py:60-70`). Tái hiện e4 (orchestrator 2 executor, không pin):
  ```
  execute CANCELLED: TransitionError: task T0001 is CANCELLED, expected one of [READY]
  cancelled row after execute: ('CANCELLED', 'ca', 'a')
  events: ['created', 'transition', 'agent_routed']
  ```
- Expected vs actual: một yêu cầu bị từ chối không được làm thay đổi row hoặc audit log. Thực tế task terminal bị gắn agent và có event `agent_routed` giả.
- Root cause & impact: kiểm tra trạng thái nằm sau tác dụng phụ. Audit log sai lệch và dashboard hiển thị sai agent. Một READY task bị từ chối vì account unavailable cũng bị gắn cứng vào agent đó, mất khả năng chuyển sang executor khác khi chạy lại (thông qua `_route` khi `_agent_of` khác None).
- Options & trade-offs: kiểm tra `row["state"] in expect` ở đầu `execute_task`/`resume_task` (resume đã có), và `_unroute` cho mọi exception khi `routed`. Diff nhỏ, không ảnh hưởng đường đi đúng.
- Acceptance criteria / verification method: execute task CANCELLED thì row không đổi, không có event mới. Account unavailable thì task chưa pin vẫn `agent=None`.

### FUN-04 — Không có quyền sở hữu workflow giữa các tiến trình: hai driver cùng chạy, một driver ghi WAITING khi workflow vẫn đang chạy
- Severity: Low      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: `service.py:898`: guard `if wid not in self._workflows` chỉ có tác dụng trong một tiến trình. `state.set_workflow` ghi đè `runner_pid` mà không kiểm tra chủ sở hữu. Tái hiện e5: 2 subprocess cùng gọi `run_workflow(wid, wait=True)`, 2 task, scenario `slow` 4 s:
  ```
  drv0 5.0 WAITING {'tasks': {'T0001': 'DONE', 'T0002': 'RUNNING'}, 'waiting': {}}
  drv1 9.1 DONE …
  workflow_state: READY→RUNNING, RUNNING→WAITING, WAITING→DONE
  ```
  Không có run trùng hay chạy song song trong worktree, vì `Store.transition` (`state.py:~670-678`) và `AccountLock` giữ được.
- Expected vs actual: kỳ vọng tiến trình thứ hai bị từ chối ("being driven by pid X") hoặc chỉ quan sát. Thực tế driver thứ hai chạy song song, và trong lúc workflow vẫn RUNNING, có thời điểm trạng thái hiển thị `WAITING` với `runner_pid=None`.
- Root cause & impact: không có kiểm tra quyền sở hữu khi bắt đầu drive. Client đang poll có thể đọc được WAITING và chạy thêm một driver nữa. Không mất dữ liệu.
- Options & trade-offs: trong `run_workflow`, từ chối khi `state == RUNNING` và chủ sở hữu (`runner_pid` + `runner_started`) còn sống, giống quy tắc `recover()` đang dùng.
- Acceptance criteria / verification method: chạy lại e5 thì tiến trình thứ hai nhận lỗi rõ ràng, và workflow không đi qua WAITING.

### FUN-05 — `slot_wait_s` nhỏ hơn 5 s hết hạn trễ (lần thử đầu không bị chặn bởi deadline)
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `service.py:976`: `"next": now + SLOT_WAIT_FIRST_S + random.uniform(0, 1)` không có `min(…, deadline)`, khác với nhánh sau (`slot.update(... next=min(..., slot["deadline"]))`), dù comment khẳng định "expiry is never late". Đo trong e8 với `slot_wait_s: 3`: `run_workflow slot_wait_s=3: (5.2, 'WAITING', …)`.
- Expected vs actual: WAITING sau khoảng 3 s, thực tế khoảng 5–6 s.
- Root cause & impact: thiếu `min()` ở lần thử đầu. Ảnh hưởng nhỏ (độ trễ tối đa khoảng 3 s so với cấu hình).
- Options & trade-offs: `next=min(now + SLOT_WAIT_FIRST_S + jitter, deadline)`.
- Acceptance criteria / verification method: test với `slot_wait_s=1` thì workflow WAITING sau dưới 2 s.

### FUN-06 — `plan` không có cơ chế chờ slot: account planner bận ở tiến trình khác làm plan lỗi ngay
- Severity: Low      - Confidence: Confirmed
- Kind: Intentional limitation (chưa có tài liệu)
- Priority: P2
- Evidence: `service.py:837`: `_pick("planner","read")` ném lỗi ngay. e8: `plan while account busy elsewhere: ValueError: no eligible planner agent (ca: account a busy in another bridge process)`. `slot_wait_s` chỉ áp dụng cho task trong `_drive`.
- Expected vs actual: tài liệu chỉ nói `slot_wait_s` áp dụng cho task của workflow, nên hành vi này không sai so với tài liệu. Nhưng trong cấu hình phổ biến (planner và executor dùng chung một account), người dùng phải tự thử lại.
- Root cause & impact: tính năng chờ slot chưa được áp dụng cho planner.
- Options & trade-offs: ghi rõ trong tài liệu, hoặc áp dụng cùng backoff trước `_consult`.
- Acceptance criteria / verification method: tài liệu nêu rõ, hoặc kịch bản e8 cho plan thành công sau khi slot được giải phóng.

### ARC-01 — Bridge / TaskRunner / AccountGate gọi private method của nhau hai chiều; lớp delegate tồn tại để phục vụ monkeypatch trong test
- Severity: Medium      - Confidence: Confirmed
- Kind: Improvement
- Priority: P2
- Evidence: `service.py:~199-260` có khoảng 20 delegate private (`_reserve`, `_pick`, `_route`, `_start`, `_run`, …) với comment "so a method replaced on a Bridge instance applies to every caller". `runner.py` gọi khoảng 25 thuộc tính private của Bridge (`b._settle_open`, `b._apply_review`, `b._consult`, `b._stopping`, …). `gate.py` gọi `bridge._key`, `_account_quota`, `_rebind`, `_slots`. Quy ước delegate không được giữ nhất quán: `runner.py:59` và `runner.py:66` gọi thẳng `b.gate._refresh_account_marks` và `b.gate._unscopable`, còn `service.py` gọi `self.gate._…` 23 lần. Test monkeypatch các internal: `monkeypatch.setattr(b, "_start", …)` (5 chỗ), `_reserve`, `_pick`, `_account_quota`, `_account` (19 chỗ trong tests).
- Expected vs actual: kỳ vọng ranh giới module rõ ràng, chỉ phụ thuộc một chiều. Thực tế là phụ thuộc vòng qua private API. Cấu trúc production bị định hình bởi điểm patch của test.
- Root cause & impact: tách `service.py` thành gate và runner nhưng không tách các phụ thuộc. Khi sửa một module, cách đáng tin duy nhất để biết caller nào bị ảnh hưởng là grep private name. Quy ước "patch trên Bridge thì áp dụng ở mọi nơi" sai một phần (các lời gọi thẳng `gate._…` bỏ qua patch), nên test có thể không kiểm tra đúng đường đi mà nó tưởng.
- Options & trade-offs: không cần viết lại. Có thể (a) cho gate và runner nhận một interface hẹp (store, orch, cfg, account resolver) thay vì toàn bộ Bridge, hoặc (b) tối thiểu là đổi các delegate được dùng thật thành public và ghi lại hợp đồng. Chi phí: phải sửa test đang dựa vào monkeypatch.
- Acceptance criteria / verification method: `runner.py`/`gate.py` không truy cập `b._*` ngoài một danh sách đã ghi. Không còn lời gọi `b.gate._*` từ runner.

### ARC-02 — Retry trong workflow phân loại lỗi bằng so khớp chuỗi thông báo
- Severity: Low      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: `service.py:926`: `why.endswith((" busy", " busy in another bridge process"))` và `str(e).startswith("no eligible executor agent")` trong `_drive`. Các chuỗi này sinh ra ở `gate.py:213`, `gate.py:220`, `gate.py:252`.
- Expected vs actual: kỳ vọng phân loại lỗi tạm thời theo kiểu exception hoặc mã lỗi. Thực tế, chỉ cần sửa câu chữ ở gate là slot_wait âm thầm ngừng hoạt động (task vào `waiting` ngay).
- Root cause & impact: `_unavailable` trả về chuỗi thay vì lý do có cấu trúc.
- Options & trade-offs: trả về `(code, message)`, hoặc ném `ConflictError` cho trường hợp "busy". Cần sửa ở vài chỗ.
- Acceptance criteria / verification method: test e8 vẫn qua sau khi đổi câu chữ thông báo trong gate.

### ARC-03 — Schema không có version; `progress.py` là read model thứ hai tự viết SQL
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement
- Priority: P2
- Evidence: `state.py:289` (`_add_columns`) là cơ chế migration duy nhất: chỉ thêm cột, không có `user_version`. `progress.py:98` và `progress.py:118` mở `sqlite3.connect(f"file:{path}?mode=ro")` và tự viết SELECT trên `tasks`/`attempts`. ID task và workflow được tạo bằng `COUNT(*)+1` (`state.py:596`, `state.py:543`). Cách này an toàn trong `BEGIN IMMEDIATE` vì row không bao giờ bị xoá, nhưng sẽ hỏng nếu sau này có prune.
- Expected vs actual: hiện tại hoạt động đúng. Rủi ro xuất hiện khi cần migration không chỉ thêm cột (đổi kiểu, tách bảng) hoặc khi thêm retention cho task.
- Root cause & impact: tiến hóa schema theo từng bước nhỏ. Hai nơi cùng hiểu schema thì phải sửa cả hai mỗi khi đổi.
- Options & trade-offs: thêm `PRAGMA user_version` khi cần migration đầu tiên không chỉ thêm cột (YAGNI cho đến lúc đó). Chuyển SELECT của `progress.py` vào hàm read-only trong `state.py` (đã có `overview`).
- Acceptance criteria / verification method: chỉ `state.py` chứa SQL trên các bảng task.

### ARC-04 — Quyền actor trong TRANSITIONS rộng hơn những gì code sử dụng
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement
- Priority: P2
- Evidence: `models.py:57-59`: `REWORK/FAILED/PAUSED → BLOCKED` cho phép `CLIENT`, nhưng không API nào của client thực hiện các transition này (chỉ `runner._start` dùng `Actor.BRIDGE`, `runner.py:53`). `models.py:67-69`: vòng `setdefault((ACTIVE, PAUSED))` thừa vì `models.py:47` và `models.py:52` đã khai báo. Đối chiếu với bảng trong `.claude/rules/orchestration.md`: mọi transition trong tài liệu đều có trong code. Code có thêm `RUNNING→BLOCKED` (leftover ngoài scope, `runner.py:237`) và các nhánh `→BLOCKED` theo budget, hợp lý nhưng không có trong bảng tài liệu. Thiếu `(BACKLOG, BLOCKED)` (liên quan FUN-02).
- Expected vs actual: kỳ vọng bảng transition đúng bằng các quyền được dùng. Thực tế có quyền thừa nhưng chưa bị khai thác.
- Root cause & impact: phòng xa. Quyền thừa mở rộng bề mặt nếu sau này có API client mới.
- Options & trade-offs: bỏ `CLIENT` khỏi các cặp đó, bỏ vòng `setdefault`, bổ sung tài liệu cho `RUNNING→BLOCKED`.
- Acceptance criteria / verification method: test `test_transitions_enforce_actor_and_legality` khớp với bảng đã thu hẹp.

### Ghi chú thuộc khu vực khác (mỗi mục một dòng)
- Hai project trên cùng một account nhưng `lock_dir` hoặc `max_concurrency` khác nhau sẽ dùng tập lock file khác nhau (`accounts.py:389-397`), nên giới hạn concurrency giữa các project không được đảm bảo. Giả thuyết, chưa chạy; giao cho CFG/REL.
- Thực thi đồng thời cùng một task từ hai tiến trình bị chặn bởi account lock với thông báo "account busy" thay vì "task already RUNNING" (e6). Vấn đề UX nhỏ.
- Web giới hạn `notes` 4000 ký tự, MCP/CLI cho phép 8000 (`webactions.py:74` so với `models.py:155`). Không nhất quán nhưng vô hại (UX).

## 4. Kết luận theo khu vực

| Khu vực | Verdict |
|---|---|
| Phân lớp & tính nhất quán CLI/MCP/TUI/web | **GOOD**: một service duy nhất, web đi qua CLI |
| State machine & transition bất hợp lệ | **GOOD**: mọi thử nghiệm bất hợp lệ đều bị chặn trong transaction |
| Attempt budget / PAUSED / resume / recover | **NEEDS IMPROVEMENT** (FUN-01) |
| depends_on & lifecycle workflow | **NEEDS IMPROVEMENT** (FUN-02, FUN-04) |
| Cancellation (cùng tiến trình và khác tiến trình) | **GOOD**: 0.61 s, giải phóng lock, worktree không bị đổi |
| Routing / quota_fallback / slot_wait / multi-bridge | **GOOD** với lỗi nhỏ (FUN-03, FUN-05, FUN-06). Không có thử nghiệm runtime cho `strategy: quota` và quota_fallback (chỉ đọc code `gate.py:254-334`) |
| Coupling / cohesion module | **NEEDS IMPROVEMENT** (ARC-01, ARC-02) |
| Persistence / migration | **GOOD** ở hiện tại, nợ kỹ thuật thấp (ARC-03) |

**Tổng thể Phase 2: NEEDS IMPROVEMENT.** Không có lỗi Critical hay High. Lõi state machine và cơ chế chống chạy song song hoạt động đúng. Các lỗi tìm được nằm ở đường phục hồi sau crash và ở cách lan truyền trạng thái dependency và workflow.
