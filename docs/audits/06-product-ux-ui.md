# Phase 6 — UI/UX & trải nghiệm sản phẩm

## Phạm vi, phương pháp, môi trường

- **Phạm vi:** web dashboard (`bridge/webui.py`, `bridge/static/index.html`, `app.js`, `app.css`), terminal dashboard (`bridge/tui.py`, Textual 8.2.8), CLI output (`bridge/render.py`, `bridge/cli.py`: `list`, `tasks`, `status`, `usage`, `account-list`, `health`, `agents`), MCP tool surface (`bridge/server.py`) xét như một sản phẩm: cách đặt tên và thông báo lỗi.
- **Môi trường:** macOS (Darwin 25.5.0), Python 3.12.11 (`.venv`), HEAD `0541153`, Chromium headless qua Playwright toàn cục (`/opt/homebrew/lib/node_modules/playwright`), axe-core 4.10.2 cài vào thư mục scratch (repo không có axe-core nên `tests/qa` bỏ qua bước này).
- **Dữ liệu seed (cô lập):** dùng lại `scripts/screenshots.py` (`setup()` + `run_tasks()`), với `DEMO` chuyển sang thư mục scratch. HOME, `AGENT_BRIDGE_HOME` và PATH (chỉ `tests/fakes` được copy) là giả; không có CLI agent thật nào chạy. Có 3 project: `acme-api` gồm 5 task chạy thật qua fake codex (approve, review, quota, cancel) cộng 260 task tạo bằng `Bridge.create_task`; `web-shop` (legacy config) gồm 40 task; `empty-proj` không có DB. Trạng thái các task được phân bố bằng SQL trên DB scratch, tổng cộng **305 task**: DONE 132, BACKLOG 47, READY 26, REVIEW 13, REWORK 21, PAUSED 12, FAILED 10, BLOCKED 7, CANCELLED 21, APPROVED 7, RUNNING 7, VERIFYING 2. Có 5 account: `work` (fake codex trả QUOTA nên bị đánh dấu `quota`; rollout 38% đã dùng), `personal` (rollout 91% đã dùng), `spare` (không có rollout, quota UNKNOWN), `studio` (google, chưa đăng nhập) và `default` (legacy).
- **Cách chạy:** `agent-bridge web --port 0`, đăng nhập bằng link dùng một lần; Playwright chạy ở 1440 px và 390 px (isMobile, hasTouch), `colorScheme` light/dark, `reducedMotion` reduce/no-preference; thử điều hướng chỉ bằng bàn phím; chạy axe với các tag `wcag2a/2aa/21a/21aa/22aa/best-practice` trên 6 view × 4 tổ hợp; đo latency; tắt server giữa phiên. TUI được chạy bằng `BridgeApp.run_test()` ở kích thước 140×36 và 80×24. CLI được chạy dưới pseudo-TTY (`script -q /dev/null`) để thấy bản render Rich.
- **BLOCKED / không làm:** không gọi `mcp__agent-bridge__*`; không chạy `--e2e`, `--with-health` hay test real; không thử bằng trình đọc màn hình thật (VoiceOver), chỉ dùng axe và đọc mã; không đo trạng thái "loading" bằng mạng chậm (chỉ đọc mã skeleton trong `index.html`); không đối chiếu docs/qa hay audit cũ (brief yêu cầu không tin chúng, nên mọi kết luận dưới đây đều dựa trên mã hiện tại và lần chạy này).
- Ảnh chụp nằm trong scratch và sẽ mất sau phiên, nên mỗi ảnh dùng làm bằng chứng đều được mô tả trong báo cáo.

## Kết quả đo được (không phải finding)

| Hạng mục | Kết quả |
|---|---|
| axe-core, 24 lượt quét (overview/tasks/board/accounts/settings/drawer × 1440/390 × light/dark) | **0 vi phạm** ở mọi lượt, kể cả contrast của dark mode |
| Tràn ngang ở 390 px | Không có: `scrollWidth == clientWidth` ở mọi view. Dải tab trạng thái cuộn ngang bên trong vùng của nó |
| Tap target ở 390 px (pointer coarse) | Không có control nào nhỏ hơn 24 px; CSS đặt 44 px (`app.css:431-435`) |
| Latency với 305 task (Chromium headless, máy cục bộ) | Mở drawer bằng click: 57/49/55/48/53 ms. Lọc "export" 55 ms (44 kết quả); lọc rỗng 62 ms (305 kết quả). Đổi project 30–94 ms. Render lại view tasks/all 23 ms. DOM có 4 676 node |
| Focus visible | Mọi điểm dừng của Tab có `outline: solid 2px` (`app.css:42`) |
| Drawer | `aria-modal`, nền phía sau `inert` (`app.js:1217`), focus vào nút Close khi mở (`app.js:1255`), Tab bị giữ trong drawer (`app.js:1401`). Mở bằng `]` thì focus nằm ở `BUTTON#close` (đã đo) |
| Refresh 3 s | Focus và vị trí cuộn được giữ qua 2 lần refresh (scrollY 3000 → 3000; focus vẫn ở cùng hàng) |
| Reduced motion | `app.css:437-439` tắt mọi animation/transition; `document.getAnimations()` trả về rỗng ở cả hai chế độ |
| Offline | Sau khi kill server: `#live` = "Disconnected · retrying in 5 s"; banner hiện "Cannot reach agent-bridge. Showing data from 2:34:53 PM; retrying every 5 s." Dữ liệu cũ vẫn được giữ |
| Empty state khi lọc | "No task matches these filters" + nút "Clear filters" |
| Tên accessible của project trong sidebar | Badge số có `aria-hidden`, nên tên đọc lên là "acme-api 33 need you" (`app.js:838-839`). Giả thuyết ban đầu về lỗi đọc trùng số **không tái hiện** |

**Mô tả ảnh chụp chính:**
- (a) *overview-1440-light*: hàng 4 thẻ tóm tắt (36 Needs action / 13 Awaiting review / 9 Running / 1 Done in 24 h), danh sách "Waiting on you (49)" hiện 6 mục kèm lệnh `agent-bridge approve …` và nút copy, lưới "In progress" 9 thẻ, bảng Accounts (cột phải ghi "No quota reported yet" cho **cả 5** account), và thẻ Projects hiện đường dẫn tuyệt đối bị cắt bằng dấu "…".
- (b) *drawer-1440-light*: drawer bên phải chứa tiêu đề task, stepper 7 bước (bước "7 Done" rớt xuống dòng thứ hai), khối "Next step" đặt lệnh trong một textarea có thể resize, radio Approve/Rework/Block (không chọn sẵn), ô Notes bắt buộc, nút "Submit review" rộng toàn khung và "Cancel task" ngay bên dưới.
- (c) *m-tasks-open (390 px)*: danh sách dạng thẻ, mỗi thẻ có nhãn trạng thái kèm glyph. Phía phải là chữ "Resume", "View result" hoặc "Approve" đứng cạnh nút copy.
- (d) *board-1440-light*: trang cao 10 155 px vì lane "Queued" có khoảng 94 thẻ. Mục "Finished" nằm ở đáy trang.
- (e) *tasks-1440-dark*: bảng 305 hàng, trang cao 14 389 px, không phân trang.
- (f) *reused-link*: vùng nội dung trống, chỉ có chữ đỏ nhỏ ở góc phải trên và chữ "View only" ở chân trang.
- (g) *TUI 80×24*: ở các ô "Needs you" và "Tasks" chỉ còn thấy mã task và trạng thái, cột objective bị đẩy ra ngoài.

## Findings

### UX-01 — Enter/Space trên hàng task không mở drawer (bàn phím bị hỏng sau khi hàng được patch)
- Severity: High      - Confidence: Confirmed
- Kind: Confirmed defect (Functional defect; WCAG 2.1.1 Keyboard)
- Priority: P1
- Evidence: `app.js:320-324` (`activate()` gắn handler `event.target === row` với `row` là node *mới*); `app.js:241-250` (`syncNode()` giữ node DOM cũ nhưng chép `current.onkeydown = next.onkeydown`, nên closure vẫn so sánh với node `next` đã bị bỏ đi); `app.js:766` (hàng task) và `app.js:894` (hàng worktree trên overview, cùng cơ chế, chỉ đọc mã). Tái hiện (`kbd2.cjs`): tải `#view=tasks&s=all`, chờ 1.5 s, focus vào hàng thứ 4 (`tabindex=0`, `onkeydown` là function), rồi nhấn `Enter` → `drawer.hidden = true`, hash không có `t=`; nhấn `Space` cho kết quả giống hệt. Click chuột vào cùng hàng thì mở drawer trong khoảng 50 ms.
- Expected vs actual: Enter hoặc Space trên một hàng đang focus phải mở chi tiết task. Thực tế không có gì xảy ra. Người dùng chỉ dùng bàn phím chỉ mở được task qua hàng đợi `[`/`]` (tức chỉ các task trong "Waiting on you"); các task khác (DONE, RUNNING, READY…) không mở được.
- Root cause & impact: so sánh danh tính DOM trong closure không còn đúng khi node được tái sử dụng. Lỗi xuất hiện sau lần patch đầu tiên, tức gần như ngay khi trang tải xong, và lặp lại sau mỗi lần poll 3 s.
- Options & trade-offs: (1) dùng `event.currentTarget` thay cho `row`; đây là sửa một dòng và đúng gốc. (2) để `syncNode` không chép handler mà gắn delegation trên `tbody`. (1) đủ.
- Acceptance criteria / verification method: test Playwright phải focus một hàng, chờ ít nhất 1 chu kỳ poll, nhấn Enter, rồi assert drawer mở và focus nằm ở `#close`; làm tương tự với Space và với hàng worktree trên overview.

### UX-02 — Dashboard báo "No quota reported yet" cho account OpenAI trong khi CLI/TUI/routing thấy quota
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect (Functional defect: hai bề mặt mâu thuẫn)
- Priority: P1
- Evidence: `progress.py:342-355` chỉ đọc bảng DB `account_quota`; `service.py:184-200` (`_account_quota`) đọc rollout của Codex **trước** (`read_quota(codex_home_for(account))`) rồi mới đến DB. Với cùng dữ liệu: `agent-bridge account-list` cho `work │ quota │ 5h 62% left · week 88% left` và `personal │ available │ 5h 9% left · week 36% left`; `health` cho `work 62% left / personal 9% left`; footer TUI ghi `executor work · 5h 62% left`. Web (ảnh a và trang Accounts) lại ghi "No quota reported yet. It appears after the first run on this account." cho `work`, dù account này đã chạy 3 task.
- Expected vs actual: các bề mặt phải hiện cùng một con số, vì routing và quota fallback ra quyết định dựa trên rollout. Thực tế web nói không có dữ liệu, và câu "appears after the first run" sai khi account đã chạy rồi.
- Root cause & impact: `progress.snapshot` không dùng chung nguồn quota với `Bridge`. Người vận hành nhìn web sẽ không thấy `personal` chỉ còn 9%, trong khi routing thì có tính đến con số đó.
- Options & trade-offs: (1) để `progress` gọi `quota.read_quota` cho account openai (chỉ đọc, đã được giới hạn ở field `rate_limits`); chi phí là I/O mỗi lần poll 3 s, nên cache theo mtime. (2) để bridge ghi rollout quota vào DB mỗi lần chạy; cách này vẫn trễ khi người dùng dùng codex bên ngoài.
- Acceptance criteria / verification method: seed rollout như `scripts/screenshots.py`, gọi `/api/overview`, rồi assert `accounts[work].remaining_pct == 62` và trang Accounts không hiện chuỗi `noQuotaLong`.

### UX-03 — Giới hạn 200 task bị cắt im lặng ở TUI, CLI `list` và MCP `bridge_list_tasks`
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: `service.py:826-829` (`min(limit, 200)`); `tui.py:357` (`list_tasks(limit=200)`, cả ô "Needs you" và ô tìm kiếm đều chỉ lọc trên 200 task này); `cli.py:409-411`. Tái hiện với `acme-api` có 265 task: TUI hiện `Tasks (200)`, hàng cuối là `T0066`; tìm `T0010` cho **0 hàng**. `agent-bridge list --limit 500 --json` trả về 200 phần tử, phần tử cuối là T0066. Bản TTY không có dòng nào báo "showing N of M" (grep "more|showing|of 26" ra 0 kết quả). `list` mặc định (limit 50) cũng không báo bị cắt.
- Expected vs actual: tiêu đề "Tasks (N)" phải là tổng số task, hoặc phải nói rõ là danh sách đã bị cắt; tìm theo mã task phải ra task đó. Thực tế task cũ "biến mất", và người dùng không có cách nào biết.
- Root cause & impact: phân trang được cài ở tầng service nhưng các tầng hiển thị không biết tổng số. Task FAILED hoặc BLOCKED cũ (mã thấp) bị loại khỏi "Needs you" của TUI.
- Options & trade-offs: (1) trả kèm `total`, rồi hiện "200 of 265" kèm gợi ý `--state`/`--limit`. (2) TUI tìm theo task id phía server (`status`) khi không thấy trong danh sách. (3) "Needs you" truy vấn riêng theo trạng thái thay vì lọc trên 200 task mới nhất.
- Acceptance criteria / verification method: seed hơn 200 task; tiêu đề TUI phải phản ánh tổng số; tìm `T0010` phải ra 1 hàng; `list --limit 500` phải in 265 task hoặc in thông báo đã cắt; "Needs you" phải gồm cả task FAILED cũ.

### UX-04 — "Needs you" có nghĩa và con số khác nhau trên từng bề mặt
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect (Confirmed usability problem: thuật ngữ và số đếm)
- Priority: P2
- Evidence: cùng project `acme-api` và cùng thời điểm: sidebar web ghi "acme-api 33 need you" (33 = APPROVED 5 + BLOCKED 7 + FAILED 9 + PAUSED 12, không tính REVIEW); TUI ghi `Needs you (35)` và tiêu đề `acme-api · 35 need you` (`tui.py:358-364`, tính cả REVIEW theo `render.ATTENTION` tại `render.py:21`, nhưng chỉ trong 200 task); CLI `list` in "● needs you:" kèm 8 dòng (chỉ trong 50 hàng). Ngay trong web cũng có ba nhãn cho các tập khác nhau: "Needs action (36)", "Needs you" (nhãn trên từng mục), "Waiting on you (49)" (= needs + review). Code comment `app.js:201-202` thừa nhận sự lẫn lộn này.
- Expected vs actual: một khái niệm thì một tên và một con số. Thực tế người dùng chuyển giữa web và TUI sẽ thấy 33 ở một nơi và 35 ở nơi kia cho cùng một câu hỏi.
- Root cause & impact: mỗi bề mặt tự định nghĩa tập "cần người" (`progress.NEEDS_KINDS` so với `render.ATTENTION`) và tự giới hạn số task.
- Options & trade-offs: thống nhất hai khái niệm "Needs you" (gồm review) và "Needs action" (không gồm review) trong một module dùng chung, rồi dùng cùng nhãn ở web, TUI và CLI. Cách này đổi chữ trên UI nên cần cập nhật i18n vi/en.
- Acceptance criteria / verification method: trên dữ liệu seed, ba bề mặt phải in cùng con số cho cùng nhãn; thêm một test so sánh kết quả `progress` với `render`.

### UX-05 — Mở lại link đã dùng hoặc hết hạn: trang trống, chân trang "View only" gây hiểu nhầm
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect (Confirmed usability problem)
- Priority: P2
- Evidence: `webui.py:126-141` (link chỉ dùng một lần và hết hạn sau 600 s); mọi `/api/*` trả 401. Tái hiện bằng context Chromium mới mở lại link đã dùng (ảnh f): `#live` = "Open the link printed by agent-bridge web again (restart it if the link was used)." chỉ là chữ đỏ cỡ nhỏ ở góc phải trên; `#content` trống; footer ghi "View only" vì `overview?.actions` không xác định (`app.js:690`). Trong DOM banner offline chứa "Cannot reach agent-bridge yet; retrying every 5 s", dù server vẫn đang chạy.
- Expected vs actual: phải có một empty state ở giữa trang, nói rõ "phiên đã hết hoặc link đã dùng; chạy lại `agent-bridge web` để lấy link mới". Thực tế trang trông như một dashboard rỗng ở chế độ chỉ xem. Tình huống này xảy ra mỗi lần bookmark hoặc reload sau khi server khởi động lại (session chỉ nằm trong bộ nhớ).
- Root cause & impact: trạng thái 401 được xử lý như một biến thể của offline; footer suy ra "View only" từ dữ liệu vắng mặt.
- Options & trade-offs: thêm một trạng thái `auth` riêng chiếm vùng nội dung, ẩn footer và banner offline. Phần này chỉ thuộc UI, không cần đổi mô hình bảo mật.
- Acceptance criteria / verification method: mở lại link đã dùng thì vùng main phải hiện thông điệp xác thực kèm lệnh; footer không được ghi "View only"; banner offline phải ẩn.

### UX-06 — Mất kết nối: form thao tác vẫn bật, lỗi gửi đi là chuỗi thô "Failed to fetch"
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect (Confirmed usability problem)
- Priority: P2
- Evidence: `app.js:1110-1125`. Kill server khi đang mở drawer T0002 (REVIEW): banner offline hiện đúng, nhưng `fieldset` của form vẫn enabled (`OFFLINE submit enabled? true`). Chọn Approve, điền notes rồi Submit thì vùng thông báo là `Failed to fetch` (thông điệp nguyên văn của trình duyệt, không qua `tr()`, nên ở giao diện tiếng Việt vẫn là tiếng Anh; phần này chỉ đọc mã).
- Expected vs actual: khi offline, nút thao tác phải bị vô hiệu hoặc báo "Mất kết nối, chưa gửi", và notes đã nhập phải được giữ lại. Thực tế người dùng chỉ thấy một chuỗi kỹ thuật.
- Root cause & impact: `catch` dùng `error.message` cho mọi lỗi; form không biết trạng thái `disconnected`.
- Options & trade-offs: map `TypeError` (lỗi mạng) sang `tr('offline…')`; disable fieldset khi `disconnected`. Thay đổi nhỏ.
- Acceptance criteria / verification method: dùng Playwright để kill server rồi submit; thông điệp phải là chuỗi đã bản địa hoá, và nút phải bị vô hiệu hoặc có giải thích.

### UX-07 — Account "out of quota" vẫn hiện "62% left"; footer TUI không báo account executor đang hết quota
- Severity: Low      - Confidence: Likely
- Kind: Grounded risk (Visual inconsistency)
- Priority: P2
- Evidence: `account-list` cho `work │ yes │ quota │ 5h 62% left · week 88% left`; footer TUI ghi `project acme-api · executor work · 5h 62% left · week 88% left` với thanh tiến trình ở 62%, không có dấu hiệu hết quota, trong khi ô Accounts của TUI ghi `work quota` và `agents` ghi `account work quota`. Trạng thái `quota` ở đây đến từ fake codex in thông báo usage limit trong khi rollout vẫn ghi 38% đã dùng; với codex thật, độ trễ giữa rollout và thông báo lỗi là có thể xảy ra nhưng chưa được đo (vì vậy chỉ ở mức Likely).
- Expected vs actual: khi account bị đánh dấu `quota`, mọi chỗ hiện phần trăm phải đi kèm "out of quota" hoặc "stale". Thực tế người dùng thấy đồng thời "hết quota" và "còn 62%".
- Root cause & impact: phần trăm và trạng thái được render độc lập (`render.accounts_view`, footer quota trong `tui.py`).
- Options & trade-offs: khi `state == quota`, hiện trạng thái trước, phần trăm làm mờ đi kèm "(last reading)".
- Acceptance criteria / verification method: chạy scenario `quota` của fake codex; footer TUI và `account-list` phải hiện "out of quota" cạnh phần trăm.

### UX-08 — Lỗi CLI lộ repr enum Python và không liệt kê giá trị hợp lệ
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `service.py:590-591`: `agent-bridge resume T0003` in ra `error: T0003 is DONE; resume needs one of [<State.FAILED: 'FAILED'>, <State.PAUSED: 'PAUSED'>, <State.REWORK: 'REWORK'>]`. `service.py:827` cùng `cli.py:407,410`: `list --state paused` in `error: 'paused' is not a valid State`; ở đây phân biệt hoa thường, không gợi ý các giá trị hợp lệ, và argparse không có `choices`. Thông báo lỗi này cũng đi qua MCP `ToolError` (`server.py:35`) tới agent.
- Expected vs actual: phải là `resume needs FAILED, PAUSED or REWORK` và `--state` phải chấp nhận chữ thường hoặc liệt kê lựa chọn. Thực tế người dùng nhận được chi tiết nội bộ của Python.
- Root cause & impact: format `sorted(expect)` trên đối tượng Enum; kiểm tra `State(state)` thô.
- Options & trade-offs: `", ".join(s.value for s in sorted(expect))`; `choices=[s.value for s in State]` cộng `type=str.upper`.
- Acceptance criteria / verification method: hai lệnh trên phải in thông điệp không chứa `<State.`, và `--state paused` phải chạy được.

### UX-09 — Tasks và Board không phân trang: trang cao 10–14 nghìn px với 305 task
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement (Confirmed usability problem về mật độ; hiệu năng vẫn tốt)
- Priority: P2
- Evidence: `#task-rows` render đủ 305 `tr` (4 676 node DOM); ảnh e cao 14 389 px; ảnh d (Board) cao 10 155 px vì lane "Queued" khoảng 94 thẻ, và "Finished" nằm ở đáy. Latency vẫn tốt (drawer khoảng 50 ms, lọc 10–62 ms), nên đây **không** phải lỗi hiệu năng ở quy mô này. Chưa đo ở quy mô 2 000+ task.
- Expected vs actual: lane dài nên cuộn độc lập hoặc gộp lại ("+84 more"), để các lane cần người luôn nằm trong tầm nhìn. Thực tế phải cuộn rất xa mới tới "Finished", và tiêu đề các lane cuộn mất.
- Root cause & impact: lane là flow layout thường, không có chiều cao tối đa và không giới hạn số thẻ.
- Options & trade-offs: `max-height` kèm `overflow:auto` cho từng lane (CSS thuần), hoặc giới hạn N thẻ kèm "Show all". Cách đầu là rẻ nhất.
- Acceptance criteria / verification method: với 305 task, trang Board không cao quá 2× viewport; mọi tiêu đề lane vẫn thấy được ở 1440×900.

### UX-10 — Thuật ngữ và nhận diện sản phẩm không nhất quán (approve, run/execute, waiting/queued, tên sản phẩm)
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement (Visual inconsistency / terminology)
- Priority: P2
- Evidence:
  - "Approve" có hai nghĩa. Một là quyết định review: radio "Approve" trong web drawer (`app.js:1135`), `bridge_review_task(approve)`, `agent-bridge review --decision approve`. Hai là phê duyệt cuối của con người, APPROVED→DONE: phím `a` "Approve" trong TUI hỏi "Approve and close {id}?" (`tui.py:550`), và `agent-bridge approve`. Trên thẻ mobile (ảnh c), chữ "Approve" đứng cạnh nút copy nhưng web không thực hiện được thao tác này (nó chỉ copy lệnh terminal).
  - Web dùng "Run" (`actionRun`) trong khi CLI/MCP dùng `execute`. Tab "Waiting (94)" nhưng lane Board cùng tập lại tên "Queued" (`app.js:200`).
  - Tên sản phẩm: web ghi "Agent Orchestration Kit" (`app.js:28`), còn TUI (`tui.py:240` `TITLE = "agent-bridge"`), CLI và MCP server ghi "agent-bridge".
- Expected vs actual: một động từ cho một thao tác, và một tên sản phẩm hoặc quan hệ tên được nói rõ (ví dụ "Agent Orchestration Kit · agent-bridge"). Thực tế người dùng có thể hiểu nhầm "Approve" trên mobile là nút phê duyệt. Điểm này chưa được kiểm chứng với người dùng thật.
- Root cause & impact: mỗi bề mặt được viết riêng, không có bảng thuật ngữ chung.
- Options & trade-offs: đổi nhãn bước cuối thành "Final approval (terminal)"; đổi nhãn Next step trên thẻ thành "Copy: approve"; đồng bộ Waiting/Queued. Chỉ đổi chuỗi i18n.
- Acceptance criteria / verification method: grep i18n và TUI để chắc mỗi khái niệm có đúng một nhãn; snapshot các nhãn.

### UX-11 — TUI ở 80×24: mất cột objective, tiêu đề detail bị cắt
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect (Visual inconsistency / responsive)
- Priority: P2
- Evidence: `tui.py:242-253` (`#side` có `min-width: 38`, `#left` có `min-width: 26`). Ảnh g (`run_test(size=(80,24))`): ô "Needs you" và "Tasks" chỉ hiện `T0265  PAUSED` mà không có objective; tiêu đề detail là `T0265 · P…`; ở 140×36 objective vẫn hiện nhưng bị cắt ở khoảng 13 ký tự ("Add the produ"). Cột "Agents/Accounts" giữ 38 cột ngay cả ở kích thước nhỏ.
- Expected vs actual: ở 80 cột, danh sách task phải được ưu tiên hơn ô Agents. Thực tế người dùng không đọc được task là gì nếu không mở detail.
- Root cause & impact: tỉ lệ cố định `2fr/2fr/3fr` cùng `min-width` cho ô phụ.
- Options & trade-offs: ẩn hoặc thu gọn `#side` khi chiều rộng dưới 100 (Textual hỗ trợ media query theo kích thước qua class); hoặc bỏ `min-width: 38`.
- Acceptance criteria / verification method: `run_test(size=(80,24))` phải cho ít nhất 30 ký tự objective trên mỗi hàng task.

### UX-12 — Khối "Next step" đặt lệnh trong textarea có thể resize; stepper rớt dòng
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement (Subjective preference / Visual inconsistency)
- Priority: P2
- Evidence: ảnh b: lệnh `agent-bridge result T0002` nằm trong textarea cao khoảng 70 px có tay nắm resize, trong khi danh sách "Waiting on you" và cột Next step dùng `code` cùng nút copy dạng icon. Stepper 7 bước rớt "7 Done" sang dòng 2 ở 1440 px (drawer rộng khoảng 560 px).
- Expected vs actual: một kiểu hiển thị lệnh duy nhất (code kèm copy). Thực tế có hai kiểu.
- Root cause & impact: drawer dùng textarea để chọn và copy văn bản (`app.js:296` `input.select()`), nhiều khả năng làm vậy để copy được khi Clipboard API không có sẵn. Ảnh hưởng ở mức thẩm mỹ.
- Options & trade-offs: dùng `readonly` kèm `resize:none`, chiều cao 1 dòng. Giữ textarea nếu cần cơ chế copy dự phòng.
- Acceptance criteria / verification method: ảnh drawer phải cho thấy lệnh trên một dòng, không có tay nắm resize.

## Ghi chú thêm (đã kiểm, không thành finding)

- `agent-bridge status` luôn in JSON kể cả trên TTY (`status` không có trong `VIEWS`, `cli.py:49-50`), trong khi `list`, `usage` và `health` có view Rich. Đây là bất nhất nhỏ, có thể là chủ ý vì `status --wait` phục vụ máy đọc. Ghi nhận ở mức Subjective.
- `health` nối chuỗi "…try again at 5:05 PM.: wait for the quota window…" (`render.py:201` ghép `message: fix`), tạo ra dấu chấm và dấu hai chấm liền nhau. Đây là lỗi hình thức nhỏ.
- Đường dẫn tuyệt đối dài làm `health` và `list` bọc dòng xấu ở 120 cột, còn thẻ project trên web cắt đường dẫn bằng "…". Mức Subjective.

## Kết luận theo khu vực

| Khu vực | Verdict | Lý do |
|---|---|---|
| Web dashboard: accessibility, theming, responsive | **NEEDS IMPROVEMENT** | axe sạch, dark/light, 390 px và reduced-motion đều tốt, nhưng UX-01 (bàn phím không mở được task) là lỗi WCAG 2.1.1 thật |
| Web dashboard: các trạng thái và luồng thao tác | NEEDS IMPROVEMENT | UX-05, UX-06; offline và empty state cơ bản thì tốt |
| Tính nhất quán dữ liệu giữa các bề mặt | **NEEDS IMPROVEMENT** | UX-02, UX-03, UX-04, UX-07 |
| TUI | NEEDS IMPROVEMENT | UX-03, UX-11 |
| CLI output và lỗi, MCP naming | NEEDS IMPROVEMENT | UX-08, UX-10; về cấu trúc thì tốt (gợi ý "Next:" và "To fix:" rõ ràng) |
| Hiệu năng UI ở khoảng 300 task | GOOD | Mọi tương tác đo được đều dưới 100 ms |
