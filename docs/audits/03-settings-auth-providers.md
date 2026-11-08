# Audit giai đoạn 3: Settings, Authentication và Provider

## Phạm vi, phương pháp, môi trường

- **Phạm vi:** mọi nguồn cấu hình (`.ai/orchestrator.yaml`, `.ai/config.json`, registry máy
  `~/.agent-bridge/accounts.json`, biến môi trường, cờ CLI); các phương thức xác thực Claude Code,
  Codex, opencode, agy; API key, billing, budget USD, quota, routing.
- **Phương pháp:** đọc mã nguồn hiện tại (trích `file:line`), chạy thử trong project git tạm với
  HOME, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` và `AGENT_BRIDGE_HOME` giả trong scratchpad, `tests/fakes`
  đứng đầu PATH, `AGENT_BRIDGE_AGENT` đã unset. Agent được thay bằng một binary giả `envdump`, chỉ ghi
  **tên** biến môi trường nó nhận được. Mọi giá trị "credential" trong thí nghiệm là chuỗi
  `FAKE-not-a-secret`. Tài liệu chính thức được đọc qua web ngày 2026-11-08.
- **Môi trường:** macOS 26.5 (Darwin 25.5.0), Python 3.12.11 (`.venv`), HEAD `0541153`, nhánh `main`.
- **BLOCKED / không chạy:** CLI thật (`codex`, `claude`, `opencode`, `agy`), `scripts/check.sh --e2e`,
  `--with-health` và `tests/e2e/test_real_*` (tốn quota thật). Vì vậy hành vi của CLI thật (chuỗi lỗi
  thực tế, định dạng `resetsAt`, việc `claude auth status` có tính credential từ env hay không, chính
  sách đọc file của sandbox Codex trên macOS) đều chỉ là **Unverified/Hypothesis**. Landlock chỉ có
  trên Linux: phần này được đọc mã, không chạy được trên máy này.
- **Tài liệu tham khảo:** README, docs, tests và các audit trước không được dùng làm bằng chứng. Mọi
  điểm trích từ chúng đều đã được đối chiếu lại với mã.

### Nguồn chính thức (truy cập 2026-11-08)

| Mã | URL | Nội dung dùng trong báo cáo |
|---|---|---|
| D1 | https://code.claude.com/docs/en/authentication | Thứ tự ưu tiên credential: cloud provider → `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` → `apiKeyHelper` → `CLAUDE_CODE_OAUTH_TOKEN` → … → OAuth `/login` của gói subscription. Ở chế độ `-p`, "the key is always used when present". `claude setup-token` tạo "a one-year OAuth token" và "does not save the token anywhere". macOS lưu credential trong Keychain; với `CLAUDE_CONFIG_DIR`, Claude Code "keys the macOS Keychain entry to that directory too". Lưu ý: "Separate directories don't keep two Claude Console sign-ins without an API key apart". |
| D2 | https://code.claude.com/docs/en/env-vars | `ANTHROPIC_API_KEY` "is used instead of your … subscription even if you are logged in"; `ANTHROPIC_BASE_URL` định tuyến lại endpoint. |
| D3 | https://code.claude.com/docs/en/costs | "Claude Code computes the dollar figure locally from token counts at list price … The figure is an estimate". Với Pro/Max/Ultra, con số này "isn't relevant for billing purposes". |
| D4 | https://code.claude.com/docs/en/legal-and-compliance | OAuth "is designed to support ordinary use of Claude Code and other native Anthropic applications". Ngoại lệ: không cấm "an end user from signing in to the unmodified Claude Code binary with their own Claude subscription". Hạn mức quảng cáo "assume ordinary, individual usage". |
| D5 | https://www.anthropic.com/legal/consumer-terms | Mục 3 cấm truy cập "through automated or non-human means, whether through a bot, script, or otherwise", "Except when you are accessing our Services via an Anthropic API Key or where we otherwise explicitly permit it". |
| D6 | https://learn.chatgpt.com/docs/auth (chuyển hướng 308 từ developers.openai.com/codex/auth) | *Tóm tắt do model viết, mức Likely:* hai cách đăng nhập (ChatGPT hoặc API key qua `codex login --with-api-key`); credential cache ở `$CODEX_HOME/auth.json` hoặc kho credential của OS (`cli_auth_credentials_store`); token tự làm mới; có `--device-auth`. |
| D7 | https://learn.chatgpt.com/codex/non-interactive-mode | *Tóm tắt, mức Likely:* `codex exec` dùng lại auth đã lưu; khuyên dùng API key cho tự động hoá; ChatGPT auth trong CI chỉ nên dùng trên runner tin cậy. |

Không fetch được hoặc không tìm thấy, nên coi là Unverified: mục đầy đủ của `apiKeyHelper` trong
settings-reference, `preferred_auth_method`, định dạng `total_cost_usd` ở `-p` với subscription, và
chính sách đọc file của sandbox Codex.

## 1. Settings

### 1.1 Nguồn cấu hình và thứ tự ưu tiên (đã xác minh trong mã)

| Nguồn | Nạp ở | Ghi chú |
|---|---|---|
| `.ai/config.json` (`BridgeConfig`, `extra="forbid"`) | `config.py:94-98` | Luôn được nạp, kể cả khi có `orchestrator.yaml`. Mỗi list đặt trong file **thay thế** list mặc định. Theo `.ai/.gitignore`, file này **được track** trong git. |
| `.ai/orchestrator.yaml` (`OrchestratorConfig`, `extra="forbid"`) | `orchestrator.py:403-411` | Nếu có `accounts` hoặc `auto_switch.enabled` trong `config.json` thì báo lỗi. Các key legacy khác bị bỏ qua mà không báo (CFG-03). Mặc định **bị git ignore** (CFG-02). |
| Registry `~/.agent-bridge/accounts.json` | `accounts.py:190-197`, `orchestrator.py:391-393` | Được trộn vào `accounts` của yaml, account khai báo trong project thắng. Đường dẫn cứng `Path.home()` (`registry.py:17-18`), bỏ qua `AGENT_BRIDGE_HOME` (CFG-05). |
| Biến môi trường | xem bảng 1.3 | |
| Cờ CLI | `cli.py` | Không cờ nào ghi đè config. `config-init`/`account-*` chỉ chạy được qua TTY (`cli.py:193-196`). |

### 1.2 Settings Gap Analysis

Cột "Áp dụng" cho biết setting có được dùng lúc chạy hay không, kèm nơi dùng. "Ranh giới" là tác
động bảo mật nếu setting bị nới.

| Setting | Parse | Áp dụng (nơi dùng) | Validate | Ranh giới / ai được sửa | Gap |
|---|---|---|---|---|---|
| `sandbox` | ✔ Literal | `runner.py:145`, `gate.py:227` | không nhận `danger-full-access` | an toàn | — |
| `model` (config.json) | ✔ | chỉ Codex: `executor.py:400` | — | thấp | Ở chế độ yaml vẫn áp dụng cho agent Codex không có `model`, Claude thì không (CFG-03) |
| `codex_bin`/`claude_bin`/`opencode_bin`/`agy_bin` | ✔ | `executor.py:236`, `accounts.py:123` | không có (đường dẫn tương đối, file trong repo đều được nhận) | **chạy code không sandbox**; nằm trong file được track | CFG-01 |
| `ignore_user_config` | ✔ | `executor.py:390` | bool | `false` nạp hook/MCP của `~/.codex/config.toml` | nằm trong file được track |
| `claude_hook_settings` | ✔ | `service.py:628-632`, `security.py:24-42` | tuyệt đối, ngoài project, chỉ chứa `hooks` | hook chạy không sandbox | tốt |
| `default_timeout_s`/`max_timeout_s` | ✔ | `service.py:168-170, 552` | ge=30 | — | — |
| `max_attempts` | ✔ | chỉ legacy (`orchestrator.py:378`); ở chế độ yaml bị bỏ qua | ge1 le5 | — | Bị bỏ qua mà không cảnh báo (CFG-03, repro 1) |
| `network_retries`/`retry_backoff_s` | ✔ | `runner.py:197, 211` | ge/le | — | — |
| `verify_timeout_s` | ✔ | `review.py:353` | ge=5 | — | — |
| `verify_allowlist` | ✔ | `review.py:274` | không có | thay được hoàn toàn | CFG-01 |
| `protected_paths` | ✔ | `security.py:77-103`, `review.py:223-254` | **không có mức sàn** | thay được hoàn toàn, có thể mở `.git/**`, `.ai/**`, `.mcp.json` | **CFG-01** (repro 6) |
| `sensitive_paths` | ✔ | `review.py:263-264` | không có (chỉ `AGENT_INSTRUCTIONS` là cứng) | `[]` bỏ cửa duyệt của người | CFG-01 |
| `env_deny`/`env_allow` | ✔ | `executor.py:277`, `review.py:334` | `ALWAYS_DENY_ENV` cứng (`security.py:144`) | `env_deny: []` làm lộ mọi `*_API_KEY`/`*_TOKEN`; `env_allow` là toàn cục | CFG-01, AUTH-01 |
| `lock_dir` | ✔ | `accounts.py:394`, `gate.py:216` | phải nằm ngoài home của account | đặt trong project thì lock không còn chung toàn máy | nằm trong file được track |
| `accounts`/`default_account`/`auto_switch` | ✔ | chỉ legacy | yaml + accounts thì báo lỗi | — | — |
| `accounts.<n>.home` (yaml) | ✔ chuỗi | `service.py:60-67` → `resolve_profile` | **chỉ kiểm khi chạy**, `config-check` không kiểm | home trùng với login global | CFG-04 (repro 4) |
| `max_concurrency` | ✔ (chuỗi `'3'` được ép kiểu) | `service.py:137-139`, `accounts.py:395` | 1..8 | — | ép kiểu lỏng (repro 5) |
| `limits.*` (pct, budget, reserve, pricing, cooldown) | ✔ | `gate.py`, `quota.py:226-281` | reserve ≤ budget nhỏ nhất | an toàn cho chi tiêu | BILL-01..04 |
| `agents.*.allow_write`/`allow_bash` | ✔ | `executor.py:455-461` | `allow_bash` chỉ cho claude-cli | Bash không bị giới hạn đường dẫn | nằm trong file bị ignore (CFG-02) |
| `agents.*.enabled` | ✔ (`'off'` → False) | `gate.py:192` | — | — | repro 5 |
| `routing.quota_fallback.*`, `slot_wait_s` | ✔ | `gate.py:280-331`, `service.py:918` | — | việc dời account do người cho phép | — |
| `workflow.verify_sandbox`/`verify_writable` | ✔ | `runner.py:277`, `review.py:306-325` | đường dẫn tuyệt đối | `none` tắt Landlock | file bị ignore (CFG-02); chỉ chạy trên Linux |
| `agents.*.timeout_s`/`capabilities`/`provider` | ✔ | `service.py:168-170`, `progress.py:219`; `orchestrator.py:139-174` | ge=30; provider phải khớp adapter | — | — |
| `roles.*.strategy`/`requires`/`default` | ✔ | `orchestrator.py:177-200, 338-342`, `gate.py:240+` | `default` không đi cùng `explicit`/`quota` | — | — |
| `workflow.stages`/`max_rework` | ✔ | `orchestrator.py:226-253, 350-352`, `service.py:165-166` | có thứ tự, bắt buộc có cửa kiểm | bỏ `review` thì bridge tự approve | file bị ignore (CFG-02) |
| `security.*` | ✔ `Literal[True]` | không có (để khai báo) | không tắt được | — | tốt |

### 1.3 Biến môi trường mà bridge đọc

| Biến | Nơi | Ghi chú |
|---|---|---|
| `AGENT_BRIDGE_HOME` | `storage.py:21-26` | Dời state, `accounts.db`, `locks/`. **Không** dời `accounts.json`, `projects.json`, `homes/` (CFG-05) |
| `BRIDGE_PROJECT_ROOT` | `registry.py:23` | Đặt trong `.mcp.json`; không có trong `docs/configuration.md` |
| `CODEX_HOME` | `quota.py:30` | Account không có home đọc quota từ `CODEX_HOME` kế thừa |
| `AGENT_BRIDGE_AGENT` | `cli.py:434`, `server.py:200`, `service.py:73` | Đặt cho agent (`executor.py:279`), **không** đặt cho lệnh verification (xem mục tham chiếu chéo) |
| `SHELL`, `VISUAL`/`EDITOR` | `cli.py:245`, `tui.py:702` | — |

### 1.4 Trải nghiệm settings

`config-check` chỉ in source, stages, tên account, agent, role, cộng cảnh báo budget
(`cli.py:172-181`). Lệnh này không in giá trị hiệu lực của `config.json` (`protected_paths`,
`env_allow`, binary), không cho biết key nào bị bỏ qua, và không kiểm `home`. Người dùng không có
cách nào xem cấu hình hiệu lực đã trộn từ ba nguồn (CFG-07).

## 2. Authentication Support Matrix

Quy ước: **S** = Supported, **P** = Partial, **U** = Unsupported, **?** = Unverified.

| Phương thức | Mức | Thiết lập với kit | Ưu tiên credential | Vòng đời | Headless | Cô lập account | Đồng thời | Rủi ro chính |
|---|---|---|---|---|---|---|---|---|
| (a) Claude subscription OAuth (`/login`) | **S** | `account-add/-login` chạy `claude auth login` trong `CLAUDE_CONFIG_DIR` (`accounts.py:160-168`); kiểm global login không bị đổi (`accounts.py:295-308`, `cli.py:96-102`) | Thấp nhất theo D1: mọi env key/token ở trên đều thắng nó | Hết hạn thì có thông báo "Login expired · Please run /login" (D1), được phân loại `auth` → PAUSED | Có; trên macOS cần Keychain mở khoá (D1: khi bị từ chối thì fallback sang `.credentials.json`) | Theo D1, mỗi `CLAUDE_CONFIG_DIR` có entry Keychain riêng. Nếu là Console sign-in **không có API key** thì không cô lập được (D1) | `max_concurrency` + lock file | Chính sách D4/D5 (AUTH-08). Agent đọc Keychain qua Bash: Hypothesis |
| (b) `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` | **P (chỉ toàn cục)** | Không có trong tài liệu. Bị `*_TOKEN` lọc bỏ (`config.py:75`, repro A). Chỉ đi qua được khi thêm `env_allow` | Theo D1 thắng `/login`: mọi account Claude sẽ dùng cùng một token | 1 năm, không tự làm mới, đổi token phải khởi động lại (D1) | Đây là mục đích của nó | **U**: không có env riêng cho từng account | — | AUTH-01, AUTH-02 |
| (c) Anthropic API key (`ANTHROPIC_API_KEY`) | **P (chỉ toàn cục)** | Bị `*_API_KEY` lọc. `env_allow: ["ANTHROPIC_API_KEY"]` làm key lộ cho mọi adapter **và** lệnh verification (repro A) | D1: ở `-p` "always used when present", đè login của home | Do người dùng quản lý; không có secret ref | Có | **U** theo từng account | — | AUTH-01, AUTH-02, BILL-01 |
| (c') `apiKeyHelper` | **U (bị chặn)** | Agent chạy với `--setting-sources ""` (`executor.py:463-464`); file hook chỉ nhận đúng key `hooks` (`security.py:38-39`) | — | — | — | — | — | AUTH-03 |
| (c'') `ANTHROPIC_AUTH_TOKEN`, Bedrock/Vertex | **U / ?** | `*_TOKEN`, `AWS_*`, `*CREDENTIALS*` bị lọc. `CLAUDE_CODE_USE_BEDROCK` và `ANTHROPIC_BASE_URL` thì **đi qua** (repro A) | Cloud provider đứng đầu thứ tự ưu tiên (D1) | — | — | — | — | AUTH-04 |
| (d) Codex ChatGPT login | **S** | `account-login` → `codex login` (có `--device-auth`) với `CODEX_HOME`. Home riêng bị ép `cli_auth_credentials_store="file"` (`accounts.py:116-120, 230-232`) | — | Token tự làm mới (D6, Likely). Quota đọc từ rollout (`quota.py:79-132`) | Có; `--device-auth` (D6) | Có, qua `CODEX_HOME`. Token ở dạng file `auth.json` trong `~/.agent-bridge/homes/<n>` | `max_concurrency` > 1 trên cùng `auth.json`: tranh chấp refresh là Hypothesis | AUTH-05, AUTH-06 |
| (e) OpenAI API key | **P** | Biến `OPENAI_API_KEY`/`CODEX_API_KEY` bị lọc (repro A). Cách dùng được: người tự chạy `CODEX_HOME=<home> codex login --with-api-key` (D6). `account-login` không có tuỳ chọn này (`accounts.py:163`) | D6/D7: auth đã lưu được dùng lại | ? (D6/D7 không nói về vòng đời key) | Có | Có, mỗi home một `auth.json` | — | Không phân biệt được với gói ChatGPT (BILL-01). Không có rate window nên giới hạn pct không có tác dụng (BILL-03) |
| (f1) opencode | **P** | Chỉ login global, `opencode auth login` (`orchestrator.py:36-38`). Adapter read-only | Theo opencode | `opencode auth list` có chữ "stored" (`accounts.py:155-156`) | Có | **U**: chỉ có một account | — | Không cô lập được |
| (f2) agy / Google | **P** | Home = cả `HOME` (`accounts.py:24-28`). Chỉ kiểm kích thước token, không đọc nội dung (`accounts.py:132-139`) | — | ? | ? (Keychain "Not Found" dưới HOME khác, `accounts.py:133-134`) | Có, theo HOME | — | `GEMINI_API_KEY` bị lọc |

**Kết luận kiểm chứng then chốt (repro A, chạy thật qua `ClaudeAdapter.run`/`CodexAdapter.run`/`run_verification`):**

```
# env cha (giá trị đều là FAKE): ANTHROPIC_API_KEY OPENAI_API_KEY CODEX_API_KEY CLAUDE_CODE_OAUTH_TOKEN
#   ANTHROPIC_AUTH_TOKEN GEMINI_API_KEY GOOGLE_APPLICATION_CREDENTIALS AWS_PROFILE
#   ANTHROPIC_BASE_URL OPENAI_BASE_URL CLAUDE_CODE_USE_BEDROCK
env_allow=[]  claude -p : [..., "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_MESSAGING_SOCKET",
                           "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CONFIG_DIR",
                           "CODEX_HOME", "HOME", "OPENAI_BASE_URL", ...]   # không key/token nào
              codex exec: (cùng tập)          verification: (cùng tập, KHÔNG có AGENT_BRIDGE_AGENT)
env_allow=[ANTHROPIC_API_KEY]
              claude -p : + ANTHROPIC_API_KEY
              codex exec: + ANTHROPIC_API_KEY      # provider khác cũng nhận
              verification: + ANTHROPIC_API_KEY    # code do executor viết cũng nhận
```

## 3. API key và billing

- **Adapter gọi API trực tiếp:** không có. Có 4 adapter CLI và `client` (`executor.py:655-656`).
  Không chọn provider, base URL hay key theo từng account. Model chọn theo agent (`--model`/`-m`,
  `executor.py:400-402, 470-471`). Không có cơ chế nhiều key luân phiên hay secret ref.
- **Chi phí** (`quota.py:175-183`, `gate.py:159-165`): `actual` khi CLI báo cost. Với Claude đó là
  `total_cost_usd` (`executor.py:500-503`), với opencode là `cost` cộng dồn theo từng step. Khi không
  có thì `estimated` = token × `pricing` do người dùng nhập (kit không có bảng giá,
  `orchestrator.py:65-73`), hoặc lấy `reserve_usd`. Còn lại là `unavailable`, và budget fail closed
  (`quota.py:263-264`). Codex không báo cost; `cached_input_tokens` bị tính theo giá input đầy đủ,
  tức ước tính cao hơn thực tế (có ghi chú ở `quota.py:156-157`).
- **Token:** lưu `usage` của từng attempt. Không có tổng hợp token theo account ngoài số USD.
- **Retry/timeout:** chỉ retry lỗi network (`runner.py:197-211`, mặc định 1 lần). Lỗi 429/quota
  dẫn tới PAUSED, không retry.
- **Che secret:** `redact()` áp dụng cho output được lưu (`security.py:129-139`). Bridge không bao
  giờ đọc token (chỉ băm `auth.json` global, `accounts.py:288-292`).

## 4. Quota và routing

- **Nguồn quota:** với Codex là `rate_limits` mới nhất trong 20 rollout gần nhất (`quota.py:79-132`);
  với Claude là `rate_limit_event` của lần chạy trước (`executor.py:483-492`). Không có thì `UNKNOWN`.
  Giới hạn pct có `source: unavailable` thì **không chặn** (`quota.py:249-252`); đây là thiết kế và
  có trong tài liệu.
- **Đặt chỗ budget:** kiểm và ghi trong cùng một giao dịch `BEGIN IMMEDIATE` trên `accounts.db` dùng
  chung (`state.py:409-420, 483-500`). Test `test_reservation_race` (hai Store, hai thread) và 86 test
  về limit/ledger **pass** khi chạy cô lập. Lệnh:
  `pytest -q --basetemp=<scratch>/pt -p no:cacheprovider tests/unit/test_usage_ledger.py tests/unit/test_quota_limits.py tests/integration/test_limit_hardening.py tests/integration/test_account_limits.py`
  cho kết quả `86 passed in 26.83s`. Reservation của tiến trình đã chết được tính đủ số tiền đã
  giữ (`state.py:488-492`).
- **Cửa sổ reset:** budget tính theo kỳ lịch UTC (`quota.py:135-150`); cửa sổ của provider thì trượt.
  Dấu QUOTA tự gỡ khi cửa sổ đã reset hoặc khi hết `cooldown_s` (`quota.py:49-76`).
- **Failover:** opt-in, giới hạn bởi `max_switches`/`keep_sessions`, không bao giờ áp dụng cho AUTH
  (`gate.py:280-331`).
- **Credential hết hạn giữa lúc chạy:** chỉ nhận ra khi chuỗi lỗi khớp regex `auth`
  (`executor.py:88-91`), khi đó task chuyển PAUSED và account bị đánh dấu `auth` (`gate.py:155-157`).
  Các lỗi auth hoặc billing khác bị xếp vào `crash` và tốn attempt (AUTH-06).
- **Subscription hay tính phí API:** kit **không phân biệt**. `plan_type` được đọc (`quota.py:129-130`)
  nhưng không dùng vào quyết định nào; `subscriptionType` chỉ dùng để băm danh tính
  (`accounts.py:307`) (BILL-01).

## Findings

### CFG-01 — Các danh sách bảo vệ trong `.ai/config.json` (file được track) thay thế được hoàn toàn, không có mức sàn
- Severity: High      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P1
- Evidence: `config.py:60-76` (danh sách mặc định có thể thay); `security.py:101-103` và `review.py:223-230` chỉ dùng `cfg.protected_paths`; `.ai/.gitignore` có `!config.json`, nghĩa là file được track. Repro 6: `config.json = {"protected_paths": ["nothing"], "env_deny": []}` → `validate_pattern` trả về `['.ai/**', '.git/hooks/**', '.mcp.json']` (được chấp nhận làm `allowed_paths`), `env_deny []`. Binary (`codex_bin`…) cũng nằm trong cùng file và không được kiểm tra gì.
- Expected vs actual: `.claude/rules/delegation.md` nói file được bảo vệ "can never be allowed". Thực tế chỉ đúng khi config giữ danh sách mặc định.
- Root cause & impact: không có hằng số bảo vệ cứng tương tự `ALWAYS_DENY_ENV` (`security.py:144`). Trùng một phần với SEC-04: SEC-04 nói về agent sửa config, còn finding này nói về config độc hại được commit sẵn và việc không có mức sàn. Một repo clone về có thể chứa sẵn `config.json` mở `.git/hooks/**`, tắt lọc `*_API_KEY`, hoặc trỏ `claude_bin` tới một script trong repo (chạy không sandbox). `protected_dirty` không phát hiện, vì file đó trùng với HEAD.
- Options & trade-offs: (1) mức sàn cứng (`.git/**`, `.ai/**`, `.mcp.json`, `.claude/settings*.json`, cùng `*_API_KEY`/`*_TOKEN`) mà config chỉ cộng thêm; (2) chỉ nhận binary là tên lệnh trên PATH hoặc đường dẫn tuyệt đối ngoài repo; (3) cảnh báo khi `config.json` khác mặc định ở các key an ninh. Phương án (1) đơn giản nhất và không làm thay đổi hành vi mặc định.
- Acceptance criteria / verification method: lặp lại repro 6 thì `validate_pattern('.git/hooks/**')` phải raise `PathError`. Với `env_deny: []`, envdump vẫn không thấy `OPENAI_API_KEY`.

### CFG-02 — `orchestrator.yaml` bị git ignore mặc định, còn `config.json` được track
- Severity: Medium      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: `config.py:109-116` và `.ai/.gitignore` (`*`, `!config.json`). Repro: `ensure_ai_dir` rồi `git check-ignore -v` cho ra `.ai/.gitignore:1:*  .ai/orchestrator.yaml` và `.ai/.gitignore:3:!config.json  .ai/config.json`.
- Expected vs actual: đây là thiết kế có chủ ý và có trong tài liệu: `.ai/.gitignore` ghi "everything else here is local", và bảng lưu trữ trong `docs/configuration.md` ghi `.ai/orchestrator.yaml | No`. Dù vậy, các setting làm yếu an toàn (`allow_bash`, `verify_sandbox: none`, `quota_fallback`, home, budget) cần có dấu vết như `config.json`. Thực tế chúng không để lại lịch sử git, không được chia sẻ trong team, và `protected_dirty` (chỉ xét file được track, `review.py:249-254`) không thấy chúng.
- Root cause & impact: chính sách an ninh nằm rải ở hai file với hai cách lưu trữ khác nhau. Trùng một phần với SEC-04 (yaml bị sửa thì `health` không thấy).
- Options & trade-offs: thêm `!orchestrator.yaml`. Nếu muốn giữ đường dẫn home riêng tư, tách home sang registry máy (đã có sẵn).
- Acceptance criteria / verification method: `git check-ignore .ai/orchestrator.yaml` trả mã 1 trong project mới `init`.

### CFG-03 — Key legacy bị bỏ qua hoặc chỉ áp dụng một phần khi có `orchestrator.yaml`, không có cảnh báo
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `service.py:165-166` (`orch.max_attempts`); `orchestrator.py:408-410` chỉ từ chối `accounts`/`auto_switch`; `executor.py:400` (`cfg.model` chỉ dùng cho Codex). Repro 1: `config.json {"max_attempts": 5, "model": "gpt-legacy"}` cùng yaml → `config-check` exit 0, không cảnh báo; giá trị hiệu lực là `max_attempts 3`, codex argv `['-m', 'gpt-legacy', '-']`, `claude agent model None`.
- Expected vs actual: tài liệu (`configuration.md:154`) có ghi "Legacy setup only", nhưng setting vẫn được chấp nhận mà không báo gì.
- Root cause & impact: người dùng tưởng đã tăng số attempt. `model` toàn cục áp dụng cho Codex nhưng không cho Claude.
- Options & trade-offs: `orchestrator.load` từ chối hoặc cảnh báo khi `max_attempts` ≠ mặc định; `config-check` liệt kê các key bị bỏ qua.
- Acceptance criteria / verification method: lặp lại repro 1 thì `config-check` phải trả về warning hoặc lỗi nêu tên `max_attempts`.

### CFG-04 — `config-check` không kiểm `home` của account (nằm trong project, là `~/.claude`, hay là đường dẫn tương đối)
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `orchestrator.py:104-121, 287-293` không gọi `resolve_profile`; lần kiểm duy nhất là lúc chạy (`service.py:60-67`, `accounts.py:72-92`). Repro 4 với ba trường hợp (`<proj>/homes/c1`, `$HOME/.claude`, `homes/c1`): `config-check` exit 0 `"ok": true`. Lúc routing thì `gate._unavailable('cl','write')` trả về `account c1: home must be isolated from the project and the global login` / `home must be absolute`.
- Expected vs actual: một lệnh có tên là validate thì phải bắt được lỗi cô lập account.
- Root cause & impact: lỗi chỉ lộ ra khi task bị từ chối ("no eligible executor"). Đây là lỗi an toàn dễ cấu hình sai.
- Options & trade-offs: trong `config-check`, gọi `resolve_account` cho mọi account. Không nên làm trong validator pydantic, vì validator không biết `root`.
- Acceptance criteria / verification method: lặp lại repro 4 thì `config-check` phải exit ≠ 0 với cả ba trường hợp.

### CFG-05 — `AGENT_BRIDGE_HOME` không dời registry (`accounts.json`, `projects.json`, `homes/`), trái với tài liệu
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `registry.py:17-18` (`Path.home() / ".agent-bridge"`), `accounts.py:190-240` dùng `registry_dir()`; còn `storage.py:21-26` thì tôn trọng `AGENT_BRIDGE_HOME`. `docs/configuration.md:358` ghi `$AGENT_BRIDGE_HOME/accounts.db, accounts.json, homes/, locks/`. Repro 7: một `accounts.json` lỗi (field lạ) đặt dưới `AGENT_BRIDGE_HOME=<scratch>/abh2` → `config-check` exit 0 (bị bỏ qua). Cùng file đó đặt dưới `$HOME/.agent-bridge` → exit 1 `accounts.r1.extra: Extra inputs are not permitted`.
- Expected vs actual: dời toàn bộ state máy, hoặc tài liệu phải nói đúng.
- Root cause & impact: ai đặt `AGENT_BRIDGE_HOME` để cô lập (CI, thử nghiệm) vẫn đọc và ghi registry và các home login thật trong `~/.agent-bridge`. `account-add` tạo home ở đó.
- Options & trade-offs: cho `registry_dir()` dùng `storage.home()`. Cách này cần migration cho người đã có registry ở `~/.agent-bridge` khi đặt biến khác đi. Phương án còn lại là sửa tài liệu.
- Acceptance criteria / verification method: lặp lại repro 7 thì hai trường hợp phải cho kết quả ngược với hiện tại (hoặc tài liệu khớp với mã).

### CFG-06 — Một entry hỏng trong registry máy làm hỏng mọi project, và thông báo lỗi chỉ nhầm vào file
- Severity: Low      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `orchestrator.py:391-400` trộn registry vào `data["accounts"]` rồi gắn lỗi với `source` là `orchestrator.yaml`. Repro 7 (registry dưới HOME): `error: <proj>/.ai/orchestrator.yaml: accounts.r1.extra: Extra inputs are not permitted`, dù `r1` không có trong yaml. Chế độ legacy cũng gặp lỗi tương tự (`orchestrator.py:363-364`).
- Expected vs actual: lỗi phải nêu `~/.agent-bridge/accounts.json`; entry không dùng tới không nên làm hỏng project.
- Root cause & impact: một lần sửa tay registry làm dừng mọi project trên máy, và người dùng sẽ đi sửa nhầm file.
- Options & trade-offs: validate từng entry của registry riêng, bỏ qua kèm cảnh báo, hoặc báo lỗi kèm đường dẫn registry.
- Acceptance criteria / verification method: lặp lại repro 7 thì thông báo lỗi phải chứa `accounts.json`.

### CFG-07 — Không có cách xem cấu hình hiệu lực đã trộn
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement
- Priority: P2
- Evidence: `cli.py:172-181` chỉ in tên. Không lệnh nào in `protected_paths`, `env_deny`/`env_allow`, các binary, nguồn của từng account (yaml hay registry), hay key nào bị bỏ qua.
- Expected vs actual: người dùng phải tự kiểm được cấu hình hiệu lực trước khi chạy agent.
- Root cause & impact: CFG-01, CFG-03 và CFG-05 khó phát hiện vì lý do này.
- Options & trade-offs: thêm `config-check --effective` in JSON gồm giá trị, nguồn và các điểm khác mặc định ở key an ninh.
- Acceptance criteria / verification method: output chứa `protected_paths` và đánh dấu `source` cho từng account.

### CFG-08 — Test suite không cô lập `AGENT_BRIDGE_HOME`: chạy trong shell có biến này thì test ghi vào state thật và fail chập chờn
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence: `tests/conftest.py:24-25` đặt `HOME` và xoá `CODEX_HOME`, nhưng không xoá `AGENT_BRIDGE_HOME`. Repro: chạy 4 file limit/ledger với `AGENT_BRIDGE_HOME=<scratch>/abh3` → `3 failed, 83 passed`, mỗi lần một test khác fail, ví dụ `LimitExceeded … $0.60 spent+reserved` do ledger dùng chung giữa các test. Bỏ biến đi thì `86 passed`.
- Expected vs actual: test phải độc lập với môi trường của người dùng.
- Root cause & impact: lập trình viên đặt `AGENT_BRIDGE_HOME` sẽ thấy test fail giả. Tệ hơn, test ghi `accounts.db`/`locks` vào thư mục state thật của họ.
- Options & trade-offs: thêm `monkeypatch.delenv("AGENT_BRIDGE_HOME", raising=False)` vào fixture chung.
- Acceptance criteria / verification method: chạy lại lệnh trên khi có `AGENT_BRIDGE_HOME` phải được 86 passed và thư mục đó không bị ghi.

### AUTH-01 — `env_allow` áp dụng toàn cục: cho phép một key thì mọi adapter và mọi lệnh verification đều nhận
- Severity: High      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P1
- Evidence: `executor.py:276-277` và `review.py:334` dùng cùng `clean_env(os.environ, cfg.env_deny, cfg.env_allow)`; `security.py:148-153` (`k in allow`). Repro A với `env_allow=["ANTHROPIC_API_KEY"]`: biến này xuất hiện ở `claude -p`, ở **`codex exec`** và ở lệnh verification `envdump`.
- Expected vs actual: một key API của Anthropic chỉ nên tới agent claude-cli của đúng account đó. Thực tế nó tới cả Codex (một provider khác) và tới code do executor viết, được chạy như check.
- Root cause & impact: không có env theo account hay adapter. Đây là con đường **duy nhất** để dùng (b) và (c), nên ai dùng API key sẽ để lộ key cho code chưa được review. Landlock chỉ chặn ghi, không chặn đọc env; trên macOS không có Landlock.
- Options & trade-offs: thêm `accounts.<n>.env_from` (tên biến trong env của bridge, đổi tên khi chuyển vào agent) chỉ áp dụng cho adapter của account đó. Lệnh verification không bao giờ nhận các biến này.
- Acceptance criteria / verification method: lặp lại repro A thì key chỉ còn ở dòng `claude -p`.

### AUTH-02 — Không có API key hay setup-token theo từng account; token qua env đè login trong `CLAUDE_CONFIG_DIR` và làm mất cô lập account mà không báo
- Severity: Medium      - Confidence: Confirmed
- Kind: Intentional limitation
- Priority: P1
- Evidence: `accounts.py:109-113` chỉ đặt biến home. D1: `ANTHROPIC_API_KEY` (#3) và `CLAUDE_CODE_OAUTH_TOKEN` (#5) đứng trên `/login` (#7); ở `-p` thì key "always used when present". Khi đó lock và quota vẫn được tính theo home (`accounts.py:381-383`, `gate.py:79-81`).
- Expected vs actual: N account Claude phải là N danh tính. Nếu dùng `env_allow` thì cả N chạy chung một key hoặc token, trong khi bridge vẫn tính quota, lock và budget như N account riêng.
- Root cause & impact: `max_concurrency` và budget theo account bị vô hiệu, và chi phí API rơi vào một key. Kit không hướng dẫn gì về (b) và (c): grep `setup-token|CLAUDE_CODE_OAUTH_TOKEN` trong docs/bridge không có kết quả.
- Options & trade-offs: hỗ trợ credential tường minh theo account (như AUTH-01), hoặc ghi rõ trong tài liệu rằng chỉ hỗ trợ login lưu trong home và từ chối `env_allow` với các tên credential.
- Acceptance criteria / verification method: có tài liệu kèm test: hai account Claude với hai biến nguồn khác nhau, envdump cho thấy mỗi run nhận đúng biến của account mình.

### AUTH-03 — `apiKeyHelper` không dùng được
- Severity: Low      - Confidence: Confirmed
- Kind: Intentional limitation
- Priority: P2
- Evidence: `executor.py:463-466` (`--setting-sources ""`, `--settings` chỉ khi là file hook); `security.py:38-39` từ chối mọi key khác `hooks`. Chưa kiểm với CLI thật xem `settings.json` trong `CLAUDE_CONFIG_DIR` có bị bỏ qua hay không (Unverified, nhưng đúng với tên cờ).
- Expected vs actual: tài liệu không nói điều này.
- Root cause & impact: không dùng được credential ngắn hạn lấy từ vault (cách D1 khuyến nghị cho credential luân phiên).
- Options & trade-offs: cho phép thêm key `apiKeyHelper` trong file do chủ máy cung cấp. Script đó là code tin cậy, cùng mức với hook.
- Acceptance criteria / verification method: tài liệu ghi rõ là không hỗ trợ, hoặc có test cho thấy argv chứa `--settings` với `apiKeyHelper`.

### AUTH-04 — Bộ lọc env để lọt biến định tuyến và billing cùng biến của phiên Claude cha
- Severity: Medium      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: repro A. `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET`, `CLAUDE_CODE_ENTRYPOINT` và `CODEX_HOME`/`CLAUDE_CONFIG_DIR` kế thừa đều tới agent và lệnh verification. Danh sách lọc ở `config.py:75`, `security.py:144`.
- Expected vs actual: agent nên nhận một tập biến đã biết. Thực tế: (1) `ANTHROPIC_BASE_URL` gửi bearer OAuth của subscription tới host proxy mà shell cha chỉ định; (2) `CLAUDE_CODE_USE_BEDROCK=1` đứng đầu thứ tự ưu tiên (D1); `AWS_*` bị lọc nhưng `~/.aws/*` thì không, nên billing có thể bị chuyển sang AWS (Hypothesis); (3) agent Claude con nhận socket và session của phiên cha (tác động chưa rõ, Hypothesis); (4) account không có home kế thừa `CLAUDE_CONFIG_DIR` cha, không nhất thiết là `~/.claude` như tài liệu nói.
- Root cause & impact: lọc theo danh sách đen. Trùng với SEC-10 (cùng nguyên nhân, nhưng ở đây là các biến định tuyến và billing khác).
- Options & trade-offs: lọc thêm `*_BASE_URL`, `CLAUDE_CODE_USE_*` và `CLAUDECODE`/`CLAUDE_CODE_*` của phiên cha, trừ khi chúng có trong `env_allow`. Về lâu dài nên dùng danh sách trắng.
- Acceptance criteria / verification method: lặp lại repro A thì các biến trên không còn.

### AUTH-05 — Agent Codex không bị chặn đọc token dạng file của account khác (Claude thì có)
- Severity: Medium      - Confidence: Hypothesis
- Kind: Hypothesis
- Priority: P1
- Evidence: home riêng bị ép `cli_auth_credentials_store="file"` (`accounts.py:116-120, 230-232`), nên token nằm ở `~/.agent-bridge/homes/<n>/auth.json`. `read_deny` chỉ dùng trong `ClaudeAdapter.build` (`executor.py:453-454`); argv Codex (`executor.py:380-405`) không có giới hạn đọc nào. Rule `Read(~/.agent-bridge/homes/**)` (HEAD `.claude/settings.json`) chỉ áp dụng cho agent Claude. Chưa xác minh được sandbox `workspace-write` của Codex trên macOS có cho đọc ngoài workspace hay không (BLOCKED: cần CLI thật).
- Expected vs actual: một agent chỉ nên thấy credential của chính nó.
- Root cause & impact: nếu sandbox cho đọc rộng, một task bị prompt injection có thể chép token ChatGPT của account khác vào diff hoặc message cuối. Mạng bị tắt, nên token chỉ lộ qua artefact được lưu lại. `redact` có thể không nhận ra định dạng token.
- Options & trade-offs: kiểm bằng probe với CLI thật trên account thử. Nếu đọc được, đặt home ở nơi sandbox không đọc tới, hoặc dùng `keyring` (khi đó cần tính lại vấn đề Keychain dùng chung).
- Acceptance criteria / verification method: probe `codex exec -s workspace-write` đọc `~/.agent-bridge/homes/<other>/auth.json` phải bị từ chối.

### AUTH-06 — Lỗi billing, auth không chuẩn hoặc tạm thời bị xếp là `crash` và tốn attempt
- Severity: Medium      - Confidence: Likely
- Kind: Grounded risk
- Priority: P2
- Evidence: regex ở `executor.py:85-93`; chuỗi không khớp thì thành `crash` (`executor.py:129-133`). Probe `classify()` với chuỗi **tự viết để minh hoạ** (chuỗi thật của CLI là Unverified): `Credit balance is too low…` → crash; `refresh token was already used … sign in again` → crash; `This organization has been disabled` → crash; `403 permission_error OAuth …` → crash; `529 overloaded_error` → crash; `Your apiKeyHelper script is failing` → crash; `429 insufficient_quota` → quota.
- Expected vs actual: hết credit hay token hết hạn là lỗi môi trường, nên chuyển PAUSED và không tính attempt (`models.py:84-93`). Thực tế chúng tốn attempt, rồi chuyển BLOCKED sau `max_rework+1` lần. `insufficient_quota` (hết tiền) bị đánh dấu QUOTA, và cooldown hoặc reset sẽ gỡ dấu này dù billing không tự hồi phục theo thời gian.
- Root cause & impact: phân loại dựa trên văn bản, không có lớp `billing`. Task bị khoá oan, và quota fallback có thể dời task sang một account khác vì một lỗi thật ra là billing.
- Options & trade-offs: thêm lớp `billing` (402, credit, billing, insufficient_quota, organization disabled) → PAUSED và account `auth`-like. Thêm 403/refresh vào `auth`, và 529/500 vào `network`.
- Acceptance criteria / verification method: test đơn vị cho từng chuỗi, cộng một kịch bản giả trong `tests/fakes/claude` với `credit` → PAUSED, attempt được trả lại.

### AUTH-07 — Kiểm trạng thái login dùng env chưa lọc, khác với env của agent
- Severity: Low      - Confidence: Hypothesis
- Kind: Hypothesis
- Priority: P2
- Evidence: `accounts.py:145` (`account_env(acc, os.environ)`, không qua `clean_env`), trong khi agent chạy với env đã lọc (`executor.py:276-277`).
- Expected vs actual: `health` hoặc `account-list` có thể báo `logged_in: true` nhờ `ANTHROPIC_API_KEY` hay token trong shell (nếu `claude auth status` tính cả env, điều này chưa xác minh), trong khi agent lại không có credential nào.
- Root cause & impact: chẩn đoán sai khi gặp AUTH.
- Options & trade-offs: chạy `auth status` với cùng env đã lọc mà agent sẽ nhận.
- Acceptance criteria / verification method: một fake CLI báo `loggedIn` theo env cho kết quả giống nhau ở health và ở lúc chạy.

### AUTH-08 — Rủi ro chính sách khi tự động hoá bằng OAuth subscription và luân phiên nhiều account
- Severity: Medium      - Confidence: Likely
- Kind: Grounded risk
- Priority: P2
- Evidence: D5 cấm truy cập "through automated or non-human means … script" trừ khi qua API key "or where we otherwise explicitly permit it". D4 nói OAuth "designed to support ordinary use of Claude Code", và hạn mức "assume ordinary, individual usage". Tuy vậy D4 cũng **cho phép** người dùng cuối đăng nhập "the unmodified Claude Code binary with their own Claude subscription". D7 (Likely) khuyên dùng API key cho tự động hoá với Codex. Mã: luân phiên account khi hết quota (`gate.py:280-331`), chế độ mặc định là login subscription.
- Expected vs actual: kit chạy binary gốc, không đọc hay làm trung gian token (phù hợp D4). Tuy nhiên chạy hàng loạt không người theo dõi và **tự dời việc sang account khác khi hết quota** có thể bị coi là vượt mục đích "ordinary, individual usage". Báo cáo này không kết luận về mặt pháp lý.
- Root cause & impact: rủi ro bị giới hạn hoặc khoá account do enforcement "without prior notice" (D4).
- Options & trade-offs: tài liệu nêu rõ rủi ro và khuyên dùng API key hoặc Console cho tải nặng; khi bật `quota_fallback` giữa các account subscription thì cảnh báo.
- Acceptance criteria / verification method: tài liệu có mục "Điều khoản sử dụng" dẫn D4/D5/D7; `config-check` cảnh báo khi fallback giữa các account Claude/ChatGPT subscription.

### BILL-01 — `total_cost_usd` của Claude được ghi là `actual`, dù đó là ước tính theo giá niêm yết; kit không phân biệt subscription với API
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence: `executor.py:500-503` và `quota.py:178-179` (`"actual"`); `docs/configuration.md:295`. D3: con số được "computed locally from token counts at list price … an estimate", và với Pro/Max "isn't relevant for billing purposes". `plan_type` được đọc nhưng không dùng (`quota.py:129-130`); `subscriptionType` chỉ dùng để băm (`accounts.py:307`).
- Expected vs actual: `actual` ngụ ý số tiền thật bị tính. Thực tế đó là ước tính của CLI. Một account subscription có `budget_usd` sẽ bị chặn theo chi phí danh nghĩa không liên quan gì đến hoá đơn. Với API key, con số lệch so với hoá đơn khi có giảm giá hoặc giá riêng.
- Root cause & impact: báo cáo chi tiêu bị hiểu sai; budget chặn nhầm account subscription.
- Options & trade-offs: đổi nhãn thành `reported_estimate`; thêm `accounts.<n>.billing: subscription|api` (hoặc suy ra từ `plan_type`/`authMethod`) và cảnh báo khi đặt `budget_usd` cho account subscription.
- Acceptance criteria / verification method: lần chạy giả với `FAKE_CLAUDE_COST` ghi source khác `actual`; `config-check` cảnh báo khi có budget trên account subscription.

### BILL-02 — Không có adapter API trực tiếp, không chọn được base URL, provider hay key theo account; giá do người dùng tự nhập
- Severity: Low      - Confidence: Confirmed
- Kind: Intentional limitation
- Priority: P2
- Evidence: `executor.py:655-656`; `orchestrator.py:65-73` ("the kit ships no price table"); Codex `cached_input_tokens` bị tính theo giá đầy đủ (`quota.py:156-157`).
- Expected vs actual: phù hợp thiết kế "chỉ bọc CLI". Tài liệu cần nói rõ là API key không được hỗ trợ ở mức account.
- Root cause & impact: muốn dùng API key thì phải theo AUTH-01 hoặc AUTH-02.
- Options & trade-offs: giữ nguyên và ghi vào tài liệu, hoặc hiện thực credential theo account (AUTH-01).
- Acceptance criteria / verification method: có mục tài liệu "API key" kèm cách làm được hỗ trợ (ví dụ `codex login --with-api-key` trong home).

### BILL-03 — Giới hạn % không có tác dụng với account không có cửa sổ quota; việc gỡ dấu QUOTA của Claude phụ thuộc định dạng `resetsAt` chưa xác minh
- Severity: Low      - Confidence: Hypothesis
- Kind: Intentional limitation
- Priority: P2
- Evidence: `quota.py:249-252` (`unavailable` thì không chặn); `executor.py:489-490` lấy `resetsAt` nguyên trạng; `quota.numeric()` bỏ qua chuỗi ISO (`quota.py:33-35, 74`). Fake dùng epoch `9999999999` (`tests/fakes/claude:91-92`); định dạng thật là Unverified.
- Expected vs actual: account API key (Codex `--with-api-key`, Claude key) không bao giờ có `five_hour_pct`/`weekly_pct`. Nếu `resetsAt` thật là chuỗi thì dấu QUOTA của Claude chỉ được gỡ nhờ `cooldown_s`; không có `cooldown_s` thì chỉ còn `account-clear`.
- Root cause & impact: account có thể bị khoá lâu hơn cần thiết.
- Options & trade-offs: chuẩn hoá `resetsAt` (nhận cả ISO lẫn epoch); `config-check` cảnh báo khi giới hạn % đặt trên account không có cửa sổ quota.
- Acceptance criteria / verification method: test với `resetsAt` dạng ISO thì dấu được gỡ sau khi reset.

### BILL-04 — Có thể chi vượt budget khi chạy đồng thời với `reserve_usd: 0`
- Severity: Low      - Confidence: Confirmed
- Kind: Grounded risk
- Priority: P2
- Evidence: `quota.py:261-267`: với `reserve=0`, run được phép bắt đầu khi `spent < threshold`. Các run đồng thời đều qua kiểm, vì cost của Claude chỉ biết khi run kết thúc (`gate.py:159`).
- Expected vs actual: mức vượt tối đa bằng `max_concurrency` × chi phí một run. `config-check` chỉ cảnh báo khi thiếu cả `pricing` lẫn `reserve` (`cli.py:107-115`), không cảnh báo trường hợp có pricing nhưng `reserve = 0`.
- Root cause & impact: budget chỉ là giới hạn mềm.
- Options & trade-offs: ghi rõ trong tài liệu; cảnh báo khi `max_concurrency > 1` và `reserve_usd == 0`.
- Acceptance criteria / verification method: test hai run đồng thời với budget còn ít hơn chi phí một run thì run thứ hai bị từ chối khi có reserve.

### Tham chiếu chéo cho giai đoạn 4 (SEC), ngoài phạm vi chấm điểm ở đây

- Lệnh verification **không** có `AGENT_BRIDGE_AGENT` (repro A, dòng verification; `review.py:334-335`). Trùng với SEC-01/SEC-03.
  Vì vậy code do executor viết, khi chạy như check, có thể khởi động bridge. Trên macOS không có Landlock.
- Rule `Read(` của agent Claude (HEAD `.claude/settings.json`) không có `~/.ssh/**` hay `~/.aws/**`.

## Kết luận theo từng khu vực

| Khu vực | Kết luận | Lý do |
|---|---|---|
| Settings | **NEEDS IMPROVEMENT** | Validate chặt (`extra=forbid`, lỗi typo được báo rõ). Nhưng danh sách bảo vệ thay được trong file được track (CFG-01), chính sách nằm rải giữa file track và file ignore (CFG-02), `config-check` bỏ sót home (CFG-04), và `AGENT_BRIDGE_HOME` không khớp tài liệu (CFG-05). |
| Authentication | **NEEDS IMPROVEMENT** | Login subscription (a, d) được cô lập tốt và bridge không bao giờ đọc token. API key và setup-token chỉ dùng được ở mức toàn cục và rò sang provider khác cũng như sang lệnh verification (AUTH-01/02). Phân loại lỗi auth/billing hẹp (AUTH-06). |
| API key & billing | **NEEDS IMPROVEMENT** | Ledger và reservation đúng, nguyên tử (86 test pass). Nhưng nhãn `actual` sai bản chất, và kit không phân biệt subscription với API (BILL-01). |
| Quota & routing | **NEEDS IMPROVEMENT** | Điểm tốt: reservation nguyên tử qua nhiều tiến trình, budget fail closed, failover opt-in có giới hạn, AUTH không bao giờ tự dời account. Điểm cần sửa: không phân biệt subscription với API (BILL-01), lỗi billing hoặc credential hết hạn tốn attempt thay vì pause (AUTH-06), BILL-03/04. |

## Chưa xác minh được

Hành vi của CLI thật: chuỗi lỗi thực tế; định dạng `resetsAt`; `claude auth status` có tính
credential từ env hay không; `--setting-sources ""` có thực sự bỏ qua `apiKeyHelper` trong
`CLAUDE_CONFIG_DIR` hay không; sandbox Codex trên macOS có cho đọc ngoài workspace hay không
(AUTH-05); tranh chấp refresh token khi `max_concurrency > 1` trên cùng `auth.json`; agent có
`allow_bash` có đọc được Keychain của account khác hay không. Các fact về Codex chỉ dựa trên bản tóm
tắt trang (D6/D7). Landlock chỉ chạy trên Linux.
