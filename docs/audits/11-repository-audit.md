# Repository audit: cấu trúc, tài liệu build, Git và GAP (2026-11-09)

Phạm vi: audit read-only toàn repo `agent-orchestration-kit` tại `main` @ `15bd550` cộng các thay
đổi chưa commit trong working tree. Không sửa source, không xoá hay di chuyển file. File này là file
duy nhất được tạo.

**Giả định:** "6 hạng mục yêu cầu của dự án" là sáu nhóm ưu tiên ở PHASE 2 của `GOAL.md` (bảo mật và
toàn vẹn dữ liệu; bug chức năng và concurrency; auth/settings/quota/billing; hiệu năng và độ tin cậy;
UX/UI; maintainability). `prompts.md` có 11 mục nên không khớp.

**Bằng chứng đã chạy (ngày 2026-11-09):**

| Lệnh | Kết quả |
|---|---|
| `uv run --frozen pytest -q -p no:cacheprovider --basetemp=<scratchpad>` | 1 failed, 817 passed, 114 skipped (437 s). Fail duy nhất: `tests/unit/test_core.py::test_codex_argv`; chạy lại với basetemp mặc định (`.pytest_tmp` trong repo): 1 passed. Xem F9 |
| `uv run --frozen agent-bridge config-check` | exit 0, `ok: true`, không có `errors`/`deprecated`/`warnings` (4 executor, planner/reviewer = `client`) |
| `gitleaks git --redact --log-opts="--all"` | 5 hit, đều là fixture giả trong `tests/` |
| `gitleaks dir --redact` chạy riêng từng path: `.ai`, `.claude`, `prompts.md`, `temp`, `docs/plans/multi-bridge-accounts.md` | `no leaks found` cho cả 5 |
| `git status --ignored`, `git check-ignore -v` | xem mục 4 |
| `claude --version` | 2.1.319 |
| `python3 -c 'import sys;print(sys.stdin.isatty(), sys.stdout.isatty())'` trong Bash tool của Claude | `False False` |

Đối chiếu tài liệu chính thức Claude Code: memory, permissions, settings, MCP, skills, sub-agents
(code.claude.com/docs/en/{memory,permissions,settings,mcp,skills,sub-agents}.md).

## Tóm tắt phát hiện quan trọng

| # | Mức | Phát hiện | Bằng chứng |
|---|---|---|---|
| F1 | **High** | Diff chưa commit của `.claude/settings.json` xoá `Bash(*agent-bridge approve*)` và `Edit(/.ai/**)`. File này **không chỉ là settings của repo**: `bridge/install.py:158` đọc `permissions.deny` của nó và ghi vào mọi project chạy `agent-bridge init`; wheel đóng gói nó (`pyproject.toml`, `force-include`). Khi init lại, vòng "entries an older kit added and this one no longer ships" (`install.py:169-172`) **gỡ** hai rule đó khỏi các project đã cài. Commit diff này làm yếu mặc định của toàn bộ downstream. Mức High dựa trên việc mất `Edit(/.ai/**)`: phiên Claude client không bị scope check, nên có thể sửa `.ai/config.json` (`verify_sandbox`) ở project downstream. Mất deny `approve` ít tác động hơn vì TTY gate vẫn chặn (F3). | `git diff .claude/settings.json`; `install.py:156-177` |
| F2 | **High** | Diff chưa commit của `CLAUDE.md` tự mâu thuẫn và không thực thi được: (a) cho phép Claude chạy `agent-bridge approve <id>`, nhưng `cli.py:967-969` gọi `_human_confirm`, hàm này từ chối khi stdin/stdout không phải TTY (`cli.py:33-37`); Bash tool của Claude không có TTY (đo trong phiên này: `isatty()` = `False False`). (b) Rule 3 vẫn ghi "never edits `.ai/`" trong khi settings đã bỏ deny tương ứng, và memory ghi `.ai/` được sửa hẹp từ 2026-11-09. (c) Skill `agent-orchestration` §9 vẫn ghi "never run it yourself". | `git diff CLAUDE.md`; `SKILL.md` §9 |
| F3 | Medium | Deny rule Bash là so khớp văn bản, không phải ranh giới bảo mật (docs permissions: "Bash(curl *) does not stop /usr/bin/curl or sh -c"). Ranh giới thật của kit là TTY gate (`_human_confirm`, `HUMAN_ACCOUNT_CMDS` `cli.py:826`) và `AGENT_BRIDGE_AGENT=1`. Deny theo substring còn gây false positive: một lệnh `grep` chứa chuỗi `project-account-add` bị chặn trong phiên audit này. Dạng wildcard đầu (`Bash(*sqlite3*)`, `Bash(*account-add*)`) không được tài liệu hoá, nhưng thực tế hoạt động theo substring trên Claude Code 2.1.319: chính `Bash(*account-add*)` đã chặn lệnh `grep` nói trên. | phiên audit; docs permissions |
| F4 | Medium | Bỏ qua file phụ thuộc vào cấu hình máy: `.DS_Store` chỉ bị ignore qua `.git/info/exclude`; `.claude/settings.local.json` qua `~/.config/git/ignore` (do Claude Code tự thêm); `temp/` qua `temp/.gitignore` cục bộ. `.gitignore` của repo không có các mục này. Clone mới trên máy khác có thể commit nhầm. | `git check-ignore -v` |
| F5 | Medium | Tài liệu lỗi thời: `docs/plans/project-accounts.md` ghi "Nothing is implemented yet" nhưng `project-account-*`, `project-agent-*`, `account-show`, `account-verify`, `credential: keychain` đã có (`89baace`, `42a24e7`). Plan này trích nguồn `prompts.md` (chưa track). Phiên bản Claude: README 2.1.306, `PROBED_VERSIONS`/REPORT 2.1.308, máy đang chạy 2.1.319 nên `health` sẽ báo `cli_unprobed`. | `service.py:478`; `README.md:174` |
| F6 | Low | Thư mục rỗng `agent-bridge-check-8lkp2lfb/` (mode 700, 2026-11-09 15:03) ở gốc repo. Tạo bởi `tempfile.mkdtemp(prefix="agent-bridge-check-")` (`review.py:482`), nghĩa là `TMPDIR` lúc đó trỏ vào repo và `finally: rmtree` không chạy (tiến trình bị kill). Nguyên nhân **chưa xác minh**: hoặc tiến trình bị kill nên `finally` không chạy, hoặc `rmtree(..., ignore_errors=True)` nuốt lỗi. Thư mục rỗng nên git không thấy và không gây scope violation; chỉ là rác cần dọn. | `ls -ld`; `review.py:482-489` |
| F7 | Low | CI (`.github/workflows/ci.yml`) chỉ chạy pytest + e2e. Không có lint (dù có `.ruff_cache`), secret scan, dependency audit. Không có allowlist gitleaks nên mọi lần scan đều ra 5 false positive từ fixture. | `ci.yml` |
| F9 | Medium | `test_codex_argv` chỉ pass khi `tmp_path` nằm trong một git repo: `CodexAdapter(..., tmp_path)` gọi `git rev-parse --absolute-git-dir` trên `tmp_path`. Với `--basetemp` ngoài repo (đúng như `engineering-roles.md` yêu cầu agent làm) test fail với exit 128. Test phụ thuộc môi trường ngầm; gate mặc định không lộ lỗi vì `.pytest_tmp` nằm trong repo. | `tests/unit/test_core.py:196`; lần chạy ở trên |
| F8 | Low | `.claude/settings.local.json` có mục `"git commit"` không đúng cú pháp rule (thiếu `Bash(...)`). File cục bộ, không track. | file |

## 1. Sơ đồ tổng thể cấu trúc repository

232 file được track. Cột "Vai trò": **S** = nguồn build, **R** = runtime cho Claude ở repo này,
**T** = template được `init` phát hành sang project khác (định nghĩa bởi `pyproject.toml`
`force-include` + danh sách copy trong `bridge/install.py:182-185`), **D** = tài liệu,
**L** = cục bộ, không track.

```
agent-orchestration-kit/
├── bridge/                      S   26 file Python (~11.3k dòng) + static/ (web UI)
│   ├── cli.py, server.py        S   CLI `agent-bridge`, MCP stdio server
│   ├── service.py               S   lớp `Bridge` dùng chung cho CLI/MCP/TUI/web
│   ├── state.py, storage.py     S   SQLite state ngoài worktree (~/.agent-bridge/...)
│   ├── executor.py, runner.py   S   adapter Codex/Claude/opencode/agy, chạy agent
│   ├── review.py, gate.py       S   verify, scope check, quality gate
│   ├── sandbox.py, security.py  S   Landlock/Seatbelt, scrub env, path rules
│   ├── accounts.py, registry.py, quota.py   S   account, lock, quota
│   ├── orchestrator.py, config.py, models.py S  roles/agents/routing, config
│   ├── install.py               S   init/uninstall (copy template, merge JSON, manifest)
│   └── tui.py, webui.py, webactions.py, progress.py, render.py  S  giao diện
├── tests/                       S   unit 36 · integration 45 · e2e 4 · qa 9+web visual · fakes 6
├── scripts/                     S   check.sh (gate), setup.sh/uninstall.sh, screenshots.py
├── examples/*.yaml              S+T 6 cặp orchestrator/accounts mẫu
├── .ai/
│   ├── .gitignore               S   track: chỉ config.json, config.example.json
│   ├── config.json              S   verify_sandbox (thay đổi = thay đổi bảo mật)
│   ├── config.example.json      S+T
│   ├── orchestrator.yaml, accounts.yaml   L  cấu hình máy/người dùng
│   └── backup-<ts>/             L   backup do init/config-set tạo
├── .claude/
│   ├── settings.json            R+T deny list (nguồn của deny list mọi project)
│   ├── settings.local.json      L
│   ├── session_handoff.md       L   (ignore toàn cục)
│   ├── rules/orchestration.md, delegation.md, security.md   R+T
│   ├── rules/docs-sync.md, engineering-roles.md            R (chỉ repo này)
│   └── skills/agent-orchestration/SKILL.md                 R+T
├── .mcp.json                    R   server agent-bridge (uv run --project ${CLAUDE_PROJECT_DIR:-.})
├── .github/workflows/ci.yml     S   ubuntu+macOS pytest, e2e ubuntu
├── CLAUDE.md                    R   hard rules cho phiên Claude ở repo này
├── README.md / README.vi.md     D   đồng bộ song ngữ
├── CHANGELOG.md, CONTRIBUTING.md, LICENSE   D
├── GOAL.md                      D?  prompt phiên làm việc (đang track)
├── docs/
│   ├── cli-reference.md, configuration.md, configuration-guide.md, REPORT.md   D (hiện hành)
│   ├── design/web-dashboard.md  D
│   ├── audits/                  D   3 thế hệ: 11-04 (6 file), 11-08 (01–08, MASTER, TRACEABILITY), 11-09 (09, 10, 11)
│   │   └── evidence/            D   script + test tái hiện (không nằm trong testpaths)
│   ├── plans/                   D   bản ghi theo ngày
│   ├── qa/                      D   báo cáo/test case E2E 2026-11-05
│   ├── superpowers/{specs,plans}/  D  spec/plan do plugin tạo (web dashboard)
│   └── images/                  D   ảnh README
├── pyproject.toml, uv.lock, .python-version   S
└── (cục bộ) prompts.md, temp/, agent-bridge-check-*/, .venv, .pytest_tmp, *_cache, __pycache__, .DS_Store
```

Phụ thuộc giữa thành phần:

```
Claude session ── CLAUDE.md + .claude/rules/* (load lúc khởi động) + skill (khi gọi)
      │ MCP (.mcp.json, enabledMcpjsonServers)
      ▼
bridge/server.py ─► service.Bridge ◄── cli.py / tui.py / webui.py
      │                 │
      │   config.py + orchestrator.py ◄── .ai/config.json, orchestrator.yaml, accounts.yaml
      ▼                 ▼
executor.py/runner.py ─► agent CLI (codex/claude/opencode/agy) trong sandbox, account home riêng
      ▼
review.py/gate.py (scope + verify dưới Landlock/Seatbelt) ─► state.py (SQLite ở ~/.agent-bridge)
```

Lưu ý thuật ngữ: "agent" trong kit này là agent khai báo trong `.ai/orchestrator.yaml`, không phải
subagent Claude Code (`.claude/agents/`). Repo không có `.claude/agents/`, `.claude/commands/` hay
hook; không cần thêm (xem mục 5).

## 2. Tài liệu cần thiết để build Agent System

| Mục đích | Tài liệu (nguồn sự thật) | Trạng thái |
|---|---|---|
| Tổng quan, cài đặt, quick start | `README.md` (+ `README.vi.md`) | Hiện hành; lệch phiên bản Claude (F5) |
| Tham chiếu CLI/MCP | `docs/cli-reference.md` | Hiện hành; `test_docs_sync` kiểm tra đủ lệnh |
| Cấu hình (tham chiếu + hướng dẫn) | `docs/configuration.md`, `docs/configuration-guide.md` | Hiện hành; hai file có phạm vi khác nhau (reference vs guide), giữ cả hai |
| Kiến trúc, lựa chọn thiết kế | `README.md#architecture`, `docs/audits/09-architecture-redesign-audit.md`, `docs/design/web-dashboard.md` | Không có một file kiến trúc hiện hành duy nhất; kiến trúc rải trong README và audit |
| Vận hành, tương thích CLI đã probe | `docs/REPORT.md` | Hiện hành; cần cập nhật khi probe CLI mới |
| Mô hình bảo mật | `.claude/rules/security.md`, README "Security model" | Hiện hành |
| State machine, routing | `.claude/rules/orchestration.md` | Hiện hành |
| Đóng góp, kiểm thử | `CONTRIBUTING.md`, `scripts/check.sh` | Hiện hành |
| Lịch sử quyết định | `CHANGELOG.md`, `docs/audits/*`, `docs/plans/*` | Bản ghi theo ngày; vài plan lỗi thời (F5) |

Phân loại theo hình thức nên chuyển thành:

| Nội dung | Hình thức hiện tại | Đề xuất |
|---|---|---|
| Hard rules (delegation, review, human-only) | `CLAUDE.md` + rules | Giữ. Đây là instructions luôn load |
| Quy trình delegate từng bước | Skill `agent-orchestration` | Giữ là skill (load khi dùng) |
| Vai trò review (`engineering-roles.md`) | Rule luôn load | Giữ; có thể thêm `paths:` để chỉ load khi sửa `bridge/`, ưu tiên thấp |
| `docs-sync.md` | Rule luôn load | Giữ; tương tự, `paths:` là tuỳ chọn |
| Kết quả probe CLI, giới hạn đã biết | `docs/REPORT.md` | Knowledge (docs), không phải rule |
| Audit, plan | `docs/audits`, `docs/plans` | Knowledge, bản ghi theo ngày; thêm một chỉ mục `docs/audits/README.md` |
| `GOAL.md`, `prompts.md`, `temp/review.md` | prompt phiên làm việc | Không phải rule/skill; giữ cục bộ (mục 4) |

Thiếu để đáp ứng 6 hạng mục: xem mục 7.

## 3. Danh sách file cần commit (Required in Git)

Tất cả hiện **đã được track**, trừ ghi chú.

- **Nguồn:** `bridge/**` (gồm `static/`), `pyproject.toml`, `uv.lock`, `.python-version`,
  `scripts/*.sh`, `scripts/screenshots.py`.
- **Test:** `tests/**` (gồm `tests/fakes/*`, ảnh visual baseline `tests/qa/web/visual/darwin/*`).
- **Rules/Skill/Instructions:** `CLAUDE.md`, `.claude/rules/*.md`, `.claude/skills/agent-orchestration/SKILL.md`.
- **Cấu hình mẫu và cấu hình bảo mật được track có chủ ý:** `.claude/settings.json` (template deny),
  `.mcp.json`, `.ai/.gitignore`, `.ai/config.json` (tamper-evident qua scope check),
  `.ai/config.example.json`, `examples/*.yaml`.
- **CI:** `.github/workflows/ci.yml`.
- **Tài liệu kiến trúc/vận hành:** `README.md`, `README.vi.md`, `CHANGELOG.md`, `CONTRIBUTING.md`,
  `LICENSE`, `docs/cli-reference.md`, `docs/configuration.md`, `docs/configuration-guide.md`,
  `docs/REPORT.md`, `docs/design/web-dashboard.md`, `docs/images/*` (README dùng).

Optional in Git (đang track, có giá trị chia sẻ, giữ nguyên vì là bản ghi theo ngày):
`docs/audits/**` (gồm `evidence/`), `docs/plans/*`, `docs/qa/*`, `docs/superpowers/**`.
`docs/plans/multi-bridge-accounts.md` (chưa track): commit nếu là plan được duyệt, kèm trạng thái đúng.

Thay đổi chưa commit cần quyết định:

| File | Đề xuất | Lý do |
|---|---|---|
| `.claude/settings.json` | **Không commit diff hiện tại** | F1. Chỉ commit phần đổi format (nếu muốn). Nới quyền cho phiên này đặt vào `settings.local.json` |
| `CLAUDE.md` | Commit phần "Branches and PRs"; **sửa lại** rule 3 | F2: phần approve không thực thi được và mâu thuẫn với skill |
| `GOAL.md` | Xem mục 4 | prompt phiên làm việc |

## 4. Danh sách file cần loại khỏi Git và lý do (Must exclude)

| Mục | Hiện bị ignore bởi | Lý do loại | Đề xuất |
|---|---|---|---|
| `.ai/orchestrator.yaml`, `.ai/accounts.yaml` | `.ai/.gitignore` | Tên account, đường dẫn home, cấu hình máy; có thể chứa tham chiếu credential | Giữ như hiện tại |
| `.ai/backup-*/` | `.ai/.gitignore` | Bản sao cấu hình cũ | Giữ |
| `.claude/settings.local.json` | `~/.config/git/ignore` (chỉ máy này) | Quyền cá nhân | Thêm vào `.gitignore` repo (F4) |
| `.claude/session_handoff.md` | `~/.config/git/ignore` | Ledger cục bộ theo memory-protocol | Giữ ở exclude cục bộ, **không** đưa vào `.gitignore` chung |
| `.DS_Store` | `.git/info/exclude` | Rác macOS | Thêm vào `.gitignore` (F4) |
| `temp/` | `temp/.gitignore` cục bộ | Bản review nháp | Thêm `temp/` vào `.gitignore` |
| `agent-bridge-check-*/` | **không** | Thư mục tạm bị bỏ sót (F6) | Xoá thủ công sau khi người dùng xác nhận; cân nhắc thêm vào `.gitignore` |
| `prompts.md` | **không** (untracked) | Prompt yêu cầu, không phải tài liệu sản phẩm | Giữ cục bộ hoặc ignore. Tác động: `docs/plans/project-accounts.md` trích dẫn nó; nếu cần truy vết, chép yêu cầu vào plan |
| `GOAL.md` | đang track | Prompt cho phiên agent, thay đổi theo từng phiên (diff 381 dòng); không mô tả hành vi sản phẩm | Đề xuất ngừng track (`git rm --cached`, cần người dùng duyệt). Tác động: không file nào tham chiếu `GOAL.md` ngoài audit |
| `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.pytest_tmp/`, `dist/`, `*.egg-info/` | `.gitignore` | Cache/build | Giữ |

Nguy cơ lộ thông tin nhạy cảm:

- Không có secret thật trong lịch sử (`gitleaks git --log-opts="--all"`: 5 hit, đều là chuỗi giả trong
  `tests/qa/test_e2e_security.py` và `tests/unit/test_executor_events.py`). Đề xuất `.gitleaks.toml`
  allowlist cho các fixture đó để scan sạch được dùng trong CI.
- Tên người dùng thật (`tuanvm`) xuất hiện trong tên nhánh ở docs và test fixture: không phải secret.
- `.mcp.json` không chứa credential; `env` chỉ có `BRIDGE_PROJECT_ROOT`.
- `.ai/config.json` được track có chủ ý; mọi thay đổi là thay đổi bảo mật (rules/security.md).

## 5. Rules, Agents, Skills, Workflows: giữ / sửa / bổ sung / loại bỏ

| Thành phần | Quyết định | Lý do / việc cần làm |
|---|---|---|
| `CLAUDE.md` (56 dòng) | **Sửa** | Dưới ngưỡng 200 dòng của docs. Sửa rule 3 cho khớp thực tế: approve cần TTY nên người dùng chạy; ghi rõ `.ai/` được sửa hẹp hay không (F2). Không đổi tên workflow/lệnh |
| `.claude/settings.json` | **Giữ bản đã commit**, bỏ diff | F1. Cú pháp `Edit(/.ai/**)` trỏ `<repo>/.ai/**` theo docs, đúng ý định. Thêm comment trong docs rằng deny Bash chỉ là "speed bump" (đã có trong security.md cho `security`) |
| `rules/orchestration.md` | Giữ | Template phát hành. Audit này không đối chiếu lại từng chuyển trạng thái với code (đã có ở audit 02) |
| `rules/delegation.md` | Giữ | Template phát hành; nội dung chung |
| `rules/security.md` | Giữ | Mô tả đúng ranh giới; khớp docs về giới hạn deny rule |
| `rules/docs-sync.md`, `rules/engineering-roles.md` | Giữ | Chỉ cho repo này; `paths:` frontmatter là tối ưu context tuỳ chọn |
| Skill `agent-orchestration` | Giữ | Frontmatter hợp lệ (`name`, `description`). Đồng bộ §9 với quyết định cuối về approve |
| Workflow bridge (plan → execute → test → review) | Giữ | Không đổi tên lệnh/trạng thái |
| CI workflow | **Bổ sung** | Thêm job lint (ruff), gitleaks với allowlist, `pip-audit`/`uv` audit (F7) |
| `.claude/agents/`, `.claude/commands/`, hooks | Không bổ sung | Vai trò đã là agent của bridge; docs khuyên dùng skill thay commands. Hook PreToolUse chỉ đáng thêm nếu muốn chặn approve chắc hơn deny text, nhưng TTY gate đã là ranh giới |
| `docs/plans/project-accounts.md` | Không sửa (bản ghi) | Thêm dòng trạng thái mới qua CHANGELOG hoặc chỉ mục audit; docs-sync cấm sửa bản ghi theo ngày |
| `README*.md` | **Sửa nhỏ** | Phiên bản Claude đã kiểm chứng: thống nhất với `PROBED_VERSIONS`; probe lại 2.1.319 rồi cập nhật cả ba nơi |
| Loại bỏ | Không có | Không thành phần nào thừa đến mức cần xoá |

## 6. Đề xuất cấu trúc repository sau khi chuẩn hoá

Không đổi tên, không di chuyển file đang dùng. Chỉ thêm:

```
.gitignore                 + .DS_Store, temp/, .claude/settings.local.json, agent-bridge-check-*/
.gitleaks.toml             + allowlist các fixture giả trong tests/
.github/workflows/ci.yml   + job lint + secret scan + dependency audit
docs/audits/README.md      + chỉ mục: 3 thế hệ audit (11-04, 11-08, 11-09), trạng thái từng finding
                             trỏ tới IMPLEMENTATION-TRACEABILITY.md
docs/architecture.md       (tuỳ chọn) một trang kiến trúc hiện hành, tách khỏi audit
```

Giữ nguyên: `bridge/`, `tests/`, `.claude/rules/*` (tên file là hợp đồng với `install.py` và
wheel), `docs/plans/`, `docs/audits/`. `GOAL.md` ngừng track (cần duyệt).

## 7. GAP Analysis theo 6 hạng mục

Nguồn finding còn mở: `docs/audits/IMPLEMENTATION-TRACEABILITY.md` ("Not addressed on this branch"),
cộng phát hiện mới ở trên.

| # | Hạng mục | Đã có (có bằng chứng) | Còn thiếu | Ưu tiên |
|---|---|---|---|---|
| 1 | Bảo mật & toàn vẹn dữ liệu | Sandbox Codex/Claude, scope check 2 lần, verify dưới Landlock/Seatbelt, hash git config, TTY gate cho human-only, scrub env, redact output | **F1, F2** (diff chưa commit làm yếu deny template); SEC-03/12 (gate cùng user, giới hạn đã ghi), SEC-06 (con `setsid` thoát process group), SEC-11/13; AUTH-05 chưa probe; không có secret scan trong CI | Cao: F1/F2 trước mọi commit |
| 2 | Bug chức năng & concurrency | `AccountLock`, recovery runner chết, cancel ~1 s | FUN-04, FUN-06, ARC-02, REL-06 (workflow ownership giữa process, slot wait cho `plan`); F6 (thư mục tạm bị bỏ sót khi bị kill) | Trung bình |
| 3 | Auth/settings/quota/billing | `accounts.yaml`, keychain credential, `config-set/unset`, `project-account-*`, quota per account, quota fallback | CFG-03/05/07 (cảnh báo key cũ, `AGENT_BRIDGE_HOME`, xem effective config); AUTH-03/06–08, BILL-02/03; plan `multi-bridge-accounts.md` chưa duyệt | Trung bình |
| 4 | Hiệu năng & độ tin cậy | MCP off event loop, cache report | REL-04/05 (DB hỏng/chỉ đọc), PERF-03/04, ARC-03 (schema versioning); CLI 2.1.319 chưa probe (`cli_unprobed`) | Trung bình (REL-04/05), thấp (PERF) |
| 5 | UX/UI | TUI, web dashboard, visual regression baseline | UX-04..UX-12 (thuật ngữ, layout) | Thấp |
| 6 | Maintainability | Docs-sync test, CHANGELOG, 932 test (817 pass, 114 skip; lý do skip chưa phân tích) | ARC-01/04, TEST-05; F9 (test phụ thuộc tmp nằm trong git repo); không có lint trong CI; plan lỗi thời (F5); thiếu chỉ mục audit; `.gitignore` phụ thuộc máy (F4) | Thấp–trung bình |

## Phương án nâng cấp đề xuất (chưa thực hiện)

1. Người dùng quyết định F1/F2: revert diff `.claude/settings.json` (giữ format nếu muốn) và viết lại
   rule 3 của `CLAUDE.md` cho khớp TTY gate và skill §9.
2. Một PR nhỏ "repo hygiene": `.gitignore`, `.gitleaks.toml`, CI lint/secret/dependency scan,
   `docs/audits/README.md`, sửa phiên bản Claude trong README (sau khi probe 2.1.319).
3. Ngừng track `GOAL.md` (cần duyệt), xoá `agent-bridge-check-8lkp2lfb/` (cần duyệt).
4. Các finding chức năng còn mở (mục 7, hạng mục 2–4) đi qua bridge theo `delegation.md`, từng task một.

## Đã xử lý trên nhánh `chore/repo-audit-hygiene`

| Finding | Xử lý | Commit / task |
|---|---|---|
| F1 | `init` lấy deny list từ `templates/claude-settings.json` (giữ `Edit(/.ai/**)` và deny `approve` cho mọi project). Adapter Claude đọc credential deny từ cùng template, nên bản wheel vẫn chạy. `.claude/settings.json` của repo này bỏ `Edit(/.ai/**)` | `9341deb` (T0164 bị block vì spec thiếu `executor.py`, T0166 hoàn tất) |
| F2 | Rule 3 của `CLAUDE.md` khớp TTY gate: `approve`/`reassign` là việc của người dùng; sửa `.ai/` hẹp và chỉ khi được yêu cầu. Deny `approve` được khôi phục | `13e9464`, `9341deb` |
| F4 | `.gitignore` thêm `.DS_Store`, `temp/`, `.claude/settings.local.json`, `GOAL.md`, `prompts.md`; `GOAL.md` ngừng track (file vẫn còn trên máy) | `13e9464` |
| F5 (một phần) | README/README.vi ghi Claude 2.1.308 cho khớp `PROBED_VERSIONS`. Chưa probe 2.1.319; plan cũ giữ nguyên (bản ghi theo ngày) | `13e9464` |
| F6 | Xoá thư mục rỗng `agent-bridge-check-8lkp2lfb/` | không có trong git |
| F9 | `test_codex_argv` dùng fixture `repo` | `597eaee` (T0167) |

Kiểm chứng: toàn bộ test với `--basetemp` ngoài repo: 820 passed, 114 skipped, 0 failed (448 s).

Chưa xử lý: F3 (giới hạn đã ghi trong docs), F7 (lint/secret scan/dependency audit trong CI cần dependency mới), F8 (file cục bộ), probe Claude 2.1.319, các finding mở ở mục 7.
