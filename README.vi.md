<h1 align="center">claude-codex-project-kit</h1>

<p align="center">
  Harness cục bộ theo từng project: <b>Claude Code lập kế hoạch và review</b>, <b>Codex CLI viết code</b>.<br>
  Kết nối qua một MCP server nhỏ (<code>codex-bridge</code>) giữ trạng thái task, chạy Codex an toàn và tự kiểm chứng lại kết quả.<br>
  Mọi thứ nằm trong repository, không đụng tới cấu hình global.
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Tiếng Việt</b>
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey">
  <img alt="python" src="https://img.shields.io/badge/python-3.12-blue">
  <img alt="dependencies" src="https://img.shields.io/badge/dependencies-mcp-brightgreen">
  <img alt="tests" src="https://img.shields.io/badge/tests-97%20passed%20%7C%201%20opt--in-brightgreen">
</p>

---

Khi để một AI agent vừa viết code vừa tự đánh giá, không có ai kiểm tra lại: agent báo "xong" là xong, kể cả khi test chưa chạy hay nó sửa ra ngoài phạm vi được giao.

`claude-codex-project-kit` tách hai vai trò ra:
- **Claude Code (advisor)**: phân tích, chia việc thành task, review patch và quyết định approve / rework / block.
- **Codex CLI (executor)**: implement, viết test và sửa lỗi cho từng task một.
- **`codex-bridge`**: giữ trạng thái task trong SQLite, chạy Codex trong sandbox, kiểm tra phạm vi file thay đổi và tự chạy lại lệnh verification. Lời tự báo cáo của Codex không được tính là bằng chứng.

```
user ─▶ Claude Code ──MCP stdio──▶ codex-bridge ──argv, own process group──▶ codex exec --json
          (plan, review)           (SQLite state, scope check,                (implement, test)
                                    verification, audit log)
```

Mọi thứ đều cục bộ theo project: `.mcp.json`, `.claude/`, `CLAUDE.md`, `.ai/config.json`, và trạng thái
runtime trong git directory của repository (`.git/codex-bridge/`). Không có gì dưới `~/.claude`,
`~/.codex` hay cấu hình MCP global bị sửa.

---

## Cài đặt

**Yêu cầu:**
- macOS hoặc Linux, git, [uv](https://docs.astral.sh/uv/) (uv tự tải Python 3.12).
- Claude Code (đã test 2.1.303) và Codex CLI (đã test 0.163.0), mỗi bên đăng nhập bằng subscription
  riêng (`codex login status` → "Logged in using ChatGPT"). Không cần OpenAI API key.

```bash
git clone https://github.com/leetuanbmt/claude-codex-project-kit.git
cd claude-codex-project-kit
uv sync                                   # .venv cục bộ của project
scripts/check.sh --with-health            # chạy test + kiểm tra Codex có kết nối được không
claude                                    # một lần: trust thư mục → codex-bridge kết nối
claude mcp list                           # codex-bridge: ✔ Connected
```

`.mcp.json` đăng ký server với đường dẫn `${CLAUDE_PROJECT_DIR:-.}` và `.claude/settings.json` bật nó
(`enabledMcpjsonServers`). Claude Code bỏ qua các approval của project cho tới khi thư mục được trust,
nên lần khởi động interactive đầu tiên là bắt buộc; `claude -p` thì load không cần hỏi.

### Dùng trong project khác

```bash
scripts/setup.sh /path/to/project [--force]     # merge .mcp.json, settings, block CLAUDE.md, rules, skill
scripts/uninstall.sh /path/to/project [--purge] # gỡ đúng những gì setup đã thêm
```

- `.mcp.json` và `.claude/settings.json` có sẵn được merge theo từng key (server khác, deny rule và
  setting khác được giữ nguyên); `CLAUDE.md` nhận một block có đánh dấu (`<!-- codex-bridge:start/end -->`).
  Mọi file setup sửa đều được sao lưu trước vào `.ai/backup-<UTC timestamp>/`.
- Rules được cài thành `.claude/rules/codex-*.md`, skill thành `.claude/skills/codex-delegation/`.
  Nếu đã có file khác ở các đường dẫn đó, hoặc một server `codex-bridge` khác, setup dừng lại trừ khi có `--force`.
- `.mcp.json` của project đích trỏ tới kit này bằng **đường dẫn tuyệt đối** (`uv run --project <kit>`):
  nó phụ thuộc máy; di chuyển kit thì phải chạy setup lại.
- Install manifest nằm ở `<git-dir>/codex-bridge/install-manifest.json`, ngoài tầm với của Codex.
  Uninstall chỉ gỡ những gì setup đã thêm và giữ lại file bạn đã sửa sau đó (kèm cảnh báo).
  `--purge` xoá luôn thư mục `.ai/` của project đích, kể cả config.
- Mỗi project có bridge database riêng trong git directory của nó, nên trạng thái task và session
  Codex không bao giờ lẫn giữa các project.

---

## Bắt đầu nhanh

### 1. Giao việc qua Claude

Chỉ cần yêu cầu Claude thực hiện thay đổi; skill `codex-delegation` sẽ điều phối:

```
bridge_create_task → bridge_execute_task → bridge_task_status(wait_s)
  → bridge_task_result → bridge_review_task (→ bridge_resume_task khi rework)
```

### 2. Kiểm tra trạng thái

```bash
uv run codex-bridge health        # bridge + trạng thái Codex, % quota còn lại của từng account
uv run codex-bridge account-list  # account, trạng thái login, home/profile của codex-switch
uv run codex-bridge list          # danh sách task, mới nhất trước
```

### 3. Thêm account Codex riêng

```bash
codex-switch home add work          # đăng nhập mới vào ~/.codex-accounts/homes/work (người dùng làm)
uv run codex-bridge account-list    # hiện home và đoạn config dùng ngay được
# thêm vào .ai/config.json:  "accounts": {"work": {"codex_home": "~/.codex-accounts/homes/work"}}
```

---

## Vòng đời task

Trạng thái: `BACKLOG → READY → RUNNING → VERIFYING → REVIEW → APPROVED → DONE`, cùng với `REWORK`,
`FAILED`, `PAUSED`, `BLOCKED`, `CANCELLED`.

| Chuyển trạng thái | Ai thực hiện | Khi nào |
|---|---|---|
| `RUNNING → VERIFYING` | bridge | Codex thoát bình thường |
| `VERIFYING → REVIEW` / `REWORK` | bridge | scope sạch và mọi lệnh verification pass / có check fail |
| `RUNNING → PAUSED` | bridge | quota, auth, network, thiếu CLI, runner bị ngắt (không tính vào số lần thử) |
| `REVIEW → APPROVED` / `REWORK` / `BLOCKED` | Claude | `bridge_review_task` |
| `APPROVED → DONE` | bridge, hoặc **người** nếu high risk | quality gate được kiểm tra lại |

Task `risk_level: high` hoặc sửa đường dẫn nhạy cảm (migrations, auth, deploy, CI, secrets) dừng ở
`APPROVED` cho tới khi người dùng chạy `codex-bridge approve <id>` trong terminal.

---

## Bảng lệnh

### MCP tools (Claude dùng)

| Tool | Mô tả |
|---|---|
| `bridge_health` | Đường dẫn/phiên bản/login của Codex, task đang chạy, % quota còn lại của từng account |
| `bridge_list_accounts` | Account, trạng thái login, snapshot quota (cửa sổ 5h / 7 ngày), home/profile của codex-switch (không có credential) |
| `bridge_create_task` | Hợp đồng task đã được validate (paths, criteria, verification, deps, risk, account) |
| `bridge_execute_task` | Chạy Codex ở background |
| `bridge_task_status` | Trạng thái rút gọn; long-poll `wait_s ≤ 55` |
| `bridge_task_result` | Spec, các attempt, JSON cuối của Codex, diff stat + patch, kết quả check của bridge |
| `bridge_review_task` | approve / rework / block (Claude) |
| `bridge_resume_task` | Rework hoặc khôi phục, cùng session Codex và cùng account |
| `bridge_cancel_task` | Kill process group của lần chạy và huỷ task |
| `bridge_list_tasks` | Mới nhất trước, lọc theo state tuỳ chọn |

### CLI (`uv run codex-bridge <cmd>`)

| Lệnh | Mô tả |
|---|---|
| `health` | Trạng thái bridge và Codex |
| `create <spec.json>` | Tạo task từ file spec |
| `execute` / `status` / `result` | Chạy task, xem trạng thái, xem kết quả |
| `review` / `resume` / `cancel` | Review, chạy tiếp, huỷ task |
| `list` | Danh sách task |
| `account-list` | Account, trạng thái login, home/profile codex-switch |
| `account-clear <name>` | Gỡ trạng thái unavailable của account sau khi người dùng xác nhận |

**Chỉ dành cho người** (cần terminal interactive và gõ xác nhận):

| Lệnh | Mô tả |
|---|---|
| `approve <id>` | Duyệt task high-risk / đường dẫn nhạy cảm |
| `reassign <id> <account>` | Chuyển task sang account khác (session Codex mới) |
| `account-login <name>` | Đăng nhập mới cho một `codex_home` tự chọn |

---

## Cấu hình (`.ai/config.json`, tuỳ chọn)

Xem `.ai/config.example.json`. Key không xác định sẽ bị từ chối.

| Key | Mặc định | Ý nghĩa |
|---|---|---|
| `sandbox` | `workspace-write` | hoặc `read-only`; không chấp nhận `danger-full-access` |
| `default_timeout_s` / `max_timeout_s` | 1800 / 7200 | giới hạn cho mỗi lần chạy |
| `max_attempts` | 3 | lần chạy đầu + 2 lần rework; sau đó BLOCKED |
| `network_retries` / `retry_backoff_s` | 1 / 10 | retry có giới hạn, chỉ cho lỗi network |
| `verify_timeout_s` | 900 | cho mỗi lệnh verification |
| `verify_allowlist` | pytest, uv, python, … | các executable mà verification được phép chạy |
| `protected_paths` | `.env*`, keys, `.git/**`, `.ai/**`, … | task không bao giờ được ghi |
| `sensitive_paths` | migrations, auth, deploy, CI, secrets | có thay đổi ⇒ cần người duyệt |
| `env_deny` / `env_allow` | `*_API_KEY`, `*_TOKEN`, … | bị xoá khỏi env của Codex/verify |
| `accounts` / `default_account` | `{}` / `default` | xem phần Account |
| `auto_switch` | `{"enabled": false, "min_remaining_pct": 10, "fallback_accounts": []}` | xem Account → Tự chuyển account |
| `lock_dir` | system temp | lock account giữa các process |

### Trạng thái và file

| Đường dẫn | Commit | Nội dung |
|---|---|---|
| `.ai/config.json` | có | config của project; Codex sửa file này là vi phạm scope |
| `.ai/config.example.json` | có | ví dụ có chú thích |
| `$(git rev-parse --absolute-git-dir)/codex-bridge/` | không bao giờ | `bridge.db` (task, attempt, audit log), output schema, file tạm của từng lần chạy, `install-manifest.json` (setup) |

Trạng thái nằm trong git directory vì sandbox `workspace-write` của Codex ghi được mọi nơi trong
worktree, kể cả file bị git-ignore, nhưng không ghi được dưới `.git/` (đã kiểm chứng trên codex-cli 0.163).
Database nằm trong worktree sẽ cho phép Codex tự sửa trạng thái task của chính nó. Bản cài cũ dùng
`.ai/bridge.db` được tự động migrate ở lần khởi động đầu tiên.

---

## Account

`default` chạy Codex với môi trường kế thừa (`~/.codex`), bridge chỉ đọc. Các account có tên là những
Codex home riêng, mỗi cái đăng nhập độc lập (xem *Bắt đầu nhanh → 3*).

`codex-bridge account-login <name>` làm điều tương tự cho một `codex_home` tự chọn. Cả hai cách đều không
sao chép token từ profile khác, và đều báo lỗi rõ ràng nếu `~/.codex/auth.json` global thay đổi trong lúc
login. Các snapshot profile của `codex-switch` được liệt kê nhưng không bao giờ được dùng, vì dùng chúng
nghĩa là phải sao chép refresh token.

- Task ở yên trên account của nó. Retry và resume dùng cùng `CODEX_HOME` và session Codex.
  Người dùng chuyển task bằng `codex-bridge reassign <id> <account>` (session mới), hoặc bridge tự
  chuyển khi bật `auto_switch` (xem bên dưới).
- QUOTA hoặc AUTH đánh dấu account unavailable. Lần chạy mới trên account đó bị từ chối cho tới khi
  chạy `codex-bridge account-clear <name>` sau khi người dùng xác nhận.
- Quota được đọc từ event `rate_limits` mới nhất trong file session của Codex
  (`<codex_home>/sessions/**/rollout-*.jsonl`; không mở file nào khác trong home). Còn lại =
  `100 − max(% đã dùng 5h, % đã dùng 7 ngày)`; cửa sổ đã qua thời điểm reset tính là 0 % đã dùng.
  Đây là snapshot mới bằng lượt Codex gần nhất của account, và là `UNKNOWN` nếu account chưa chạy lần nào.
- Đồng thời: mỗi account chỉ một task active (`flock` giữa các process trong `lock_dir`) và mỗi worktree
  chỉ một task `workspace-write`; task `read-only` có thể chạy song song. Muốn nhiều writer song song thì
  cần repository hoặc worktree riêng, mỗi cái có bridge state riêng.

### Tự chuyển account (opt-in)

```json
"auto_switch": {"enabled": true, "min_remaining_pct": 15, "fallback_accounts": ["work", "default"]}
```

Trước khi task bắt đầu (`execute`) hoặc chạy lại (`resume`), bridge kiểm tra account của nó. Nếu
account đang ở trạng thái QUOTA hoặc quota còn lại dưới `min_remaining_pct`, task được chuyển sang
account đầu tiên trong `fallback_accounts` thoả mãn: available, đã login, không chạy task khác, và
quota còn lại từ ngưỡng trở lên (hoặc `UNKNOWN`).

- Việc chuyển giống `reassign`: bỏ session Codex cũ, task chạy lại với prompt đầy đủ kèm feedback
  review; file Codex đã sửa vẫn nằm trong worktree.
- Mỗi lần chuyển là một event `account_auto_switched` (từ, sang, lý do, % còn lại); không có fallback
  phù hợp thì ghi `auto_switch_skipped` và task ở nguyên account.
- Không bao giờ kích hoạt vì AUTH, hay với account có quota `UNKNOWN`. Mặc định tắt; chỉ người dùng
  sửa `.ai/config.json`.

---

## Cách hoạt động & Bảo mật

1. **Codex:** chỉ sandbox `workspace-write` hoặc `read-only`, không network, `/tmp` và `$TMPDIR` không ghi
   được, bỏ qua `~/.codex/config.toml` của người dùng (`--ignore-user-config`), prompt truyền qua stdin.
2. **Process:** argv list, không qua shell; process group riêng, SIGTERM rồi SIGKILL khi timeout hoặc huỷ;
   process group Codex mồ côi từ runner đã chết bị kill ở lần khởi động sau (chỉ khi PID đúng là Codex).
3. **Môi trường:** các biến có dạng credential (`*_API_KEY`, `*_TOKEN`, `*_SECRET*`, …) bị xoá.
4. **Đường dẫn:** kiểm tra lúc tạo task (tuyệt đối, `..`, `~`, symlink thoát ra ngoài, protected, quá rộng)
   và sau mỗi lần chạy (hash snapshot của file dirty + protected, diff toàn bộ worktree qua một index tạm,
   `HEAD` không được di chuyển).
5. **Verification:** bridge tự chạy lại, chỉ argv, executable trong allowlist, có timeout.
6. **Approval:** Codex không thể approve. Claude approve qua MCP. Task high-risk hoặc sửa đường dẫn nhạy
   cảm cần người duyệt ở terminal interactive (`approve` từ chối khi không có TTY). `.claude/settings.json`
   cũng chặn Claude chạy các lệnh này, `sqlite3` và sửa file dưới `.ai/`.
7. **Audit:** mọi chuyển trạng thái, attempt, review, thay đổi account và migration đều là một event trong
   `bridge.db`. Output được lưu đều đi qua bước che secret.

---

## Kiểm thử

```bash
scripts/check.sh                            # toàn bộ test suite (codex giả: kết quả xác định)
CODEX_SMOKE=1 uv run pytest -m real_codex   # một lần chạy Codex thật, opt-in, tốn quota của bạn
```

Kết quả:
```console
97 passed, 1 skipped
```

`tests/fakes/codex` giả lập đúng giao thức JSONL thật và bao phủ: thành công, blocked, quota, auth,
network, crash, treo, commit, ghi ra ngoài scope, session theo từng `CODEX_HOME`.

---

## Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `codex-bridge: ⏸ Pending approval` | mở `claude` trong thư mục một lần và trust nó |
| `health.ok = false` | thiếu `codex` hoặc chưa login: `codex login` (người dùng làm) |
| task PAUSED `QUOTA` / `AUTH` | chờ hoặc login lại, rồi `codex-bridge account-clear <name>` và resume; khi bật `auto_switch`, resume task bị QUOTA sẽ chuyển sang fallback |
| event `auto_switch_skipped` | không có fallback phù hợp: tất cả đang bận, chưa login, unavailable hoặc dưới ngưỡng |
| task PAUSED `INTERRUPTED` | runner đã chết; `bridge_resume_task` chạy tiếp session |
| `ConflictError … already writing` | mỗi worktree một writer; chờ hoặc dùng worktree khác |
| `account … busy in another bridge process` | project khác đang dùng account đó |
| REWORK `outside allowed paths` | Codex sửa file ngoài spec; đọc patch rồi rework |
| pytest chỉ fail bên trong Codex | sandbox cấm `$TMPDIR`, `ps`; lần chạy của bridge mới là gate |

---

## Gỡ cài đặt

Trong project khác: `scripts/uninstall.sh /path/to/project` (thêm `--purge` để xoá `.ai/`).
Bridge database trong `.git/codex-bridge/` có thể xoá bằng tay. Với chính kit, xoá thư mục là xong;
không có gì bên ngoài nó được dùng (ngoài các `~/.codex-accounts/homes/*` do bạn tự tạo).

## Giới hạn đã biết

Xem `docs/REPORT.md` → *Limitations*.

## Lịch sử thay đổi

Xem [CHANGELOG.md](CHANGELOG.md) (bằng tiếng Anh).
