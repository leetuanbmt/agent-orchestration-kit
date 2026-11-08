# Phase 4: An ninh và ranh giới tin cậy

## Phạm vi, phương pháp, môi trường

- **Phạm vi:** web dashboard (`bridge/webui.py`, `bridge/webactions.py`, `bridge/static/*`), MCP server (`bridge/server.py`), CLI (`bridge/cli.py`), kiểm tra đường dẫn và phạm vi (`bridge/security.py`, `bridge/review.py`, `bridge/runner.py`), cô lập agent (`bridge/executor.py`, `bridge/sandbox.py`), tính toàn vẹn của phê duyệt (`bridge/service.py`), chuỗi cung ứng (`pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`). Tôi cũng xác thực lại SEC-01..SEC-10 và REL-01..REL-03 của `docs/audits/security-audit.md`.
- **Phương pháp:** đọc mã nguồn ở HEAD và chạy tái hiện. Mọi tái hiện dùng CLI giả (`tests/fakes`, đứng đầu `PATH`), repo git dùng một lần, và `HOME`, `AGENT_BRIDGE_HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` riêng trong scratch. `AGENT_BRIDGE_AGENT` được unset và thư mục store bị xoá trước mỗi lần chạy. Lệnh chạy chung (gọi là `runrepro.sh`):
  ```
  env HOME=$S/home AGENT_BRIDGE_HOME=$S/ab CODEX_HOME=$S/codexhome CLAUDE_CONFIG_DIR=$S/claudecfg \
    PATH=<repo>/tests/fakes:$PATH PYTHONPATH=<repo> <repo>/.venv/bin/python -m pytest <file> \
    -p tests.conftest --rootdir=$S -c /dev/null -o addopts="" --basetemp=$S/pt -p no:cacheprovider -q -rA
  ```
  Tôi viết hai tệp: một bản sao của `docs/audits/evidence/test_audit_repro.py` và một tệp mới, `test_sec_new.py`, gồm 15 test. Với test mới, PASS nghĩa là hành vi được mô tả **đang tồn tại**. Payload và kết quả then chốt được chép thẳng vào từng phát hiện.
- **Môi trường:** macOS 26.5 (25F71, darwin), Python 3.12.11 (`.venv`), HEAD `0541153`, nhánh `main`. Working tree có thay đổi chưa commit của người dùng; tôi không chạm vào chúng.
- **BLOCKED / chưa kiểm chứng:**
  - Codex CLI và Claude CLI thật không được chạy vì sẽ tốn quota thật. Hành vi sandbox seatbelt của Codex (ghi vào `.ai/`, ghi qua symlink, `setsid`) và việc Claude áp `Read(...)` deny lên Grep/Glob chỉ được đọc qua mã nguồn, chưa kiểm chứng lúc chạy.
  - Landlock chỉ có trên Linux. Phần đó đã đọc mã nhưng chưa kiểm chứng lúc chạy trên host này.
  - Không chạy `scripts/check.sh --e2e`, `--with-health` hay `tests/e2e/test_real_*` (sẽ tốn quota thật).
  - `pip-audit` **đã chạy được** (chỉ đọc dữ liệu advisory công khai).

## Mô hình mối đe doạ

| Tác nhân | Tin cậy | Năng lực giả định |
|---|---|---|
| Chủ máy (human) | Tin cậy | TTY, trình duyệt, mọi quyền của user |
| MCP client (phiên Claude Code) | Bán tin cậy: tuân thủ rule, có thể bị prompt-injection | Gọi tool MCP. Bash bị giới hạn bởi deny rule dạng chuỗi trong `.claude/settings.json` |
| Executor/planner/reviewer agent | **Không tin cậy** (có thể bị prompt-injection từ mã, issue hay tài liệu) | Ghi trong worktree (Codex: seatbelt; Claude: allowlist tool, không có OS sandbox). Đọc gần như mọi thứ user đọc được |
| Mã do executor viết, chạy lúc verification | **Không tin cậy** | Chạy như tiến trình của user. Trên macOS **không có sandbox** (xem SEC-01) |
| Tiến trình cục bộ khác, trang web khác trong trình duyệt | Không tin cậy | Gửi request tới `127.0.0.1:<port>`, DNS rebinding, CSRF |
| Mã cùng user (bất kỳ) | Không thể phân biệt với human | Không có ranh giới OS nào bên trong bridge (`.claude/rules/security.md` tự thừa nhận điều này) |

Ranh giới tin cậy (văn bản):

```
 [Human] --TTY--> agent-bridge approve (isatty + gõ lại task id) / TUI 'a' (TUI cần TTY + hộp Confirm) --> Bridge.human_approve
    |                                                    ^
    |--browser--> webui 127.0.0.1 (Host/Origin/cookie) --+--> python -m bridge <action>  (cwd = project root!)
    |                                                    |
 [MCP client] --stdio--> server.py (không có tool human_approve) --> Bridge (store: ~/.agent-bridge/...)
                                                         |
          ==== ranh giới sandbox (chỉ trong lúc agent chạy) ====
                                                         v
 [Executor agent]  Codex: seatbelt workspace-write, không mạng | Claude: Edit(./allowed/**), không sandbox OS
          |  ghi vào worktree
          v
 [Worktree] --snapshot/diff (git)--> scope check --> verification (argv allowlist)
                                                         |
          ==== macOS: KHÔNG có ranh giới ở đây (Landlock = Linux) ====
                                                         v
 [Mã do executor viết, chạy như user] : ghi ngoài worktree, mạng, đọc credential, gọi Bridge API
```

## Đánh giá theo khu vực

### Web dashboard
Các kiểm soát đã có và đã được kiểm chứng (`test_web_auth_controls`, PASS):
- Chỉ bind `127.0.0.1` (`webui.py:195`).
- Host không khớp `127.0.0.1:<port>` / `localhost:<port>` trả 421, chặn DNS rebinding (`webui.py:87-90`).
- `Origin` / `Sec-Fetch-Site` lạ trên `/api/` trả 403 (`webui.py:92-98`). Action bắt buộc có `Content-Type: application/json` và có Origin hoặc Sec-Fetch-Site (`webui.py:56-62`).
- Launch code dùng một lần, hết hạn sau 600 s, so sánh bằng `hmac.compare_digest` (`webui.py:126-142`).
- Cookie phiên: `Set-Cookie: bridge_session=…; HttpOnly; SameSite=Strict; Path=/` (đầu ra thật). Phiên hết hạn sau 12 h không hoạt động.
- Có CSP `default-src 'self'; frame-ancestors 'none'`, `nosniff` và `no-store` (`webui.py:36-41`).
- **XSS:** `app.js` không có `innerHTML`, `outerHTML`, `insertAdjacentHTML` hay `eval`. Mọi văn bản đi qua `el()` → `textContent` (`app.js:230-235`). Launch code bị xoá khỏi URL bằng `history.replaceState` (`app.js:3-6`).
- Web chỉ cho phép `review | cancel | resume | execute` (`webactions.py:15-20`). Không có `approve` của human. `review approve` của task `risk_level: high` vẫn dừng ở APPROVED (`service.py:696-700`).

Lỗ hổng chính nằm ở cách action được thực thi (SEC-02). Ngoài ra có hai điểm phụ: cookie không tách theo cổng, và audit trail không phân biệt human trên web với MCP client (SEC-12).

### MCP server và CLI
`server.py` không có tool cho approve, reassign hay account-*. Đây là ranh giới hợp lệ đối với một MCP client tuân thủ. Nhưng mọi "human-only" gate còn lại chỉ là quy ước: biến môi trường `AGENT_BRIDGE_AGENT` (`cli.py:434`, `server.py:200`), `isatty` (`cli.py:31`), và API Python `Bridge.human_approve` không có bảo vệ nào (`service.py:757-763`). Xem SEC-03.

### Injection, path, phạm vi
- `parse_command` không dùng shell, từ chối toán tử shell và đường dẫn ở argv[0] (`review.py:274-288`).
- `validate_pattern` từ chối traversal, đường dẫn tuyệt đối, ký tự điều khiển, prefix là symlink trỏ ra ngoài, và pattern quá rộng (`**/*`, `*/**`, `**`) (`security.py:77-104`; kiểm chứng bằng `test_old_sec10_patterns`, `test_symlink_write_through`).
- Claude rule injection bị chặn bởi `rule_unsafe` (`security.py:45-48`, `executor.py:458-459`).

Còn hở: ghi xuyên qua symlink nằm *bên trong* một allowed path (SEC-05), tệp git-ignored chỉ được báo cáo mà không chặn (SEC-08), và config bị sửa không được ngăn lại (SEC-04).

### Cô lập agent
- **Codex:** `-s workspace-write`, loại trừ `/tmp` và `$TMPDIR`, và `--ignore-user-config` (`executor.py:385-406`). Không có argv nào loại trừ `.ai/` hay các protected path khỏi vùng được ghi. Comment ở `config.py:64` ("Never writable by Codex") vì vậy không đúng với `.ai/**`, `.mcp.json` và `.env*`: các protected path này chỉ bị **phát hiện sau khi ghi**. Riêng `.git/**` thì policy của chính Codex có thể đã bảo vệ; chưa kiểm chứng.
- **Claude:** `--setting-sources ""`, `--strict-mcp-config`, `dontAsk`, allowlist `Read,Glob,Grep` cộng `Edit/Write(./<allowed>)`, và `--disallowedTools` cho các credential read (`executor.py:449-475`). Việc deny `Read(...)` có áp lên Grep/Glob hay không chỉ là **Hypothesis**: Claude thật chưa được chạy.
- **Env scrubbing:** xem SEC-10. **Redaction:** xem SEC-09.
- **Landlock trên host này:** `sandbox.describe("auto")` trả về `"unavailable on this system: checks run unconfined (auto)"`, và `wrap()` trả nguyên argv (`sandbox.py:68-69`). Kết quả đã được assert trong test. Ngay cả trên Linux, chế độ `auto` vẫn để mạng mở (`sandbox.py:31,71`) và không giới hạn đọc.

### Toàn vẹn phê duyệt và TOCTOU
Từ commit `1047715`, phê duyệt được ràng buộc với `after_hashes` của các tệp mà attempt đã chạm (`service.py:733-749`, `runner.py:291-293`). Có ba khoảng hở:
- Các tệp khác và HEAD không được ràng buộc. `verified_head` chỉ được ghi (`runner.py:293`), không ở đâu so sánh lại (grep: chỉ một chỗ xuất hiện).
- Kiểm tra hash rồi mới `transition`, không atomic. Cửa sổ race ngắn và đã đọc mã, chưa đo.
- Phê duyệt có thể bị làm giả từ mã cùng user (SEC-01, SEC-03).

### Chuỗi cung ứng
- `uv export --frozen` cho 39 gói runtime và 44 gói khi tính mọi group. `pip-audit 2.9.0 -r req.txt --no-deps --disable-pip` → `No known vulnerabilities found` (cả hai lần).
- Các pin chính: `mcp==2.3.0`, `starlette==1.9.0`, `uvicorn==0.54.0`, `pydantic==2.14.1`, `pyyaml==6.0.3`, `textual==8.2.8`.
- CI dùng `pull_request` (không phải `pull_request_target`) và `permissions: contents: read`. Action chỉ pin theo tag, và `uv sync` không có `--locked` (SEC-13).

## Xác thực lại các phát hiện trước (revision 3571546 → HEAD 0541153)

Tôi chạy bản sao `test_audit_repro.py` (11 test). Kết quả: 10 FAIL, 1 PASS. Trong tệp đó, FAIL nghĩa là assertion "lỗi đang tồn tại" không còn đúng, **nhưng FAIL không tự động có nghĩa là đã sửa xong**. Vì vậy tôi bổ sung tái hiện cho các biến thể còn lại.

| ID cũ | Kết luận | Bằng chứng | ID mới |
|---|---|---|---|
| SEC-01 | **Sửa một phần.** Ghi *trong* worktree lúc verification giờ bị bắt (`recheck`, `review.py:291-303`; `runner.py:280-284`). Repro cũ fail: `scope_violations = ['protected f… .env', … 'OUTSIDE.txt']`. Ghi *ngoài* worktree, truy cập mạng và gọi Bridge API vẫn không bị phát hiện trên macOS | `test_verify_code_outside_worktree_and_forges_human_approval` PASS | SEC-01 |
| SEC-02 | **Sửa một phần.** Task giờ chuyển sang BLOCKED (`models.py:122-124`), nhưng tệp bị sửa vẫn nằm lại và lần khởi động sau nạp nó. Với `.ai/orchestrator.yaml` (git-ignored), `health` cũng không thấy | `test_protected_config_tamper_persists`, `test_gitignored_orchestrator_tamper` PASS | SEC-04 |
| SEC-03 | **Sửa một phần.** Không có stage review: tệp ignored ngoài allowed path là violation (`review.py:229-234`, `runner.py:224-225`). Có stage review: chỉ được báo cáo | Chạy với `[execute, test, review]`: `REVIEW [] ['build/sitecustomize.py']` | SEC-08 |
| SEC-04 | **Sửa một phần.** Tiến trình con cùng process group bị giết (`osutil.end_group`, commit `f97c211`). Tiến trình con đã gọi `setsid` vẫn ghi sau DONE | `test_setsid_child_escapes_group_kill` PASS | SEC-06 |
| SEC-05 | **Sửa một phần.** Đã có ràng buộc `after_hashes` (`service.py:733-749`, commit `1047715`). Tệp không do attempt chạm tới và HEAD không được ràng buộc | `test_old_sec05_untouched_file_not_bound` PASS | SEC-07 |
| SEC-06 | **Đã sửa.** `bounded_patch` giữ phần đầu diff của mọi tệp (`review.py:148-170`, commit `e738324`). Repro cũ fail: `a_evil.py` có trong patch | repro cũ FAIL | — |
| SEC-07 | **Sửa một phần.** argv[0] dạng đường dẫn giờ bị từ chối (`review.py:282-284`, commit `0792dac`). `python3 -c …` và `npx --yes …` vẫn được chấp nhận | `test_old_sec07_interpreters_still_allowlisted` PASS | SEC-11 |
| SEC-08 | **Vẫn tái hiện, và nặng hơn:** vượt TTY gate bằng `script`, gọi API Python tạo event `actor=human, tty: True` giả | `test_human_approve_tty_gate_via_script` PASS | SEC-03 |
| SEC-09 | **Sửa một phần.** Đã thêm `ALWAYS_DENY_ENV` (`security.py:144-145`). Vẫn lọt `SECRET_KEY`, `STRIPE_KEY`, `NETRC`, `PYPI_PAT`, … | chạy `clean_env` | SEC-10 |
| SEC-10 | **Đã sửa.** `**/*`, `*/**`, `**` bị từ chối (`security.py:100-103`). `.claude/**` và `.vscode/**` thuộc `AGENT_INSTRUCTIONS` nên bắt buộc human (`review.py:259-264`) | `test_old_sec10_patterns` PASS | — |
| REL-01 | **Đã sửa.** Repro cũ PASS chỉ là **artifact**: nó monkeypatch `_git` để trả về `""`, bỏ qua đúng chỗ đã sửa. Khi git thật lỗi (`.git/index` hỏng), `_git` raise `EvidenceError` (`review.py:29-41`) | `test_old_rel01_real_git_failure_raises` PASS | — |
| REL-02 | **Đã sửa.** `_verify` kiểm tra `should_cancel` mỗi ≤0,1 s và `kill_group` (`review.py:357-367`, commit `950113b`). `AFTER_CANCEL.txt` không được tạo | repro cũ FAIL | — |
| REL-03 | **Đã sửa.** `_clean_detail` redact từng chuỗi lá thay vì cả JSON đã serialize (`state.py:118-127`, commit `743254f`). Không còn `AttributeError` | repro cũ FAIL | — |

## Phát hiện

### SEC-01 — Mã do executor viết chạy không sandbox lúc verification (macOS mặc định), ghi ngoài worktree và làm giả phê duyệt của human
- Severity: High      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P0
- Evidence:
  - `sandbox.py:63-69`: `auto` + không có Landlock → trả argv nguyên vẹn. `review.py:334-349`: env = `clean_env(os.environ)`, không đặt `AGENT_BRIDGE_AGENT`, và giữ `AGENT_BRIDGE_HOME`/`PYTHONPATH`. `service.py:757-763`: `human_approve` không có bảo vệ nào.
  - Tái hiện `test_verify_code_outside_worktree_and_forges_human_approval`: workflow `[execute, test]`. T0001 (`risk_level: high`) đi tới APPROVED. Với T0002, CLI giả ghi `src/check.py`, và verification là `python3 src/check.py`:
    ```
    import os, pathlib, subprocess
    pathlib.Path('<tmp>/OUTSIDE_WORKTREE.txt').write_text('marker=' + repr(os.environ.get('AGENT_BRIDGE_AGENT')))
    subprocess.run(['<venv>/bin/python', '-c', "from bridge.service import Bridge\nprint(Bridge('<repo>').human_approve('T0001')['state'])"], check=True)
    ```
    Kết quả: `T2 checks: [{"command": "python3 src/check.py", "passed": true, "exit_code": 0, "output_tail": "DONE\n"}]`. Tệp ngoài worktree chứa `marker=None`. T0001 → **DONE**, với event `forged event: human {'tty': True}`. Bản thân T0002 cũng DONE, với `scope_violations == []`.
- Expected vs actual: kỳ vọng mã không tin cậy không thể ghi ngoài worktree hay đóng một task cần human. Thực tế cả hai đều xảy ra, và audit log ghi tên human.
- Root cause & impact:
  - Trên macOS, verification là lần đầu tiên mã của agent chạy *ngoài* sandbox Codex, với toàn quyền của user. Thiết lập `auto` hạ cấp âm thầm; `health` chỉ in ra một dòng mô tả.
  - Hệ quả: vô hiệu hoá sandbox Codex, đọc và gửi credential ra ngoài (mạng mở), sửa `~/.agent-bridge` và đóng mọi task high-risk.
  - Trên Linux (đã đọc mã, chưa kiểm chứng lúc chạy): Landlock `auto` chặn ghi vào store trung tâm `~/.agent-bridge`. Nhưng với main checkout, store legacy `<git-dir>/agent-bridge/` (`storage.py:83-88`) nằm trong `root`, vốn ghi được (git dir của linked worktree thì nằm ngoài) (`review.py:321`), và mạng cùng quyền đọc vẫn mở.
- Options & trade-offs:
  - (a) Trên macOS, chạy verification trong `sandbox-exec` với profile chỉ cho ghi worktree và tmp, không mạng. Cùng cơ chế với Codex, nhưng `sandbox-exec` đã bị Apple đánh dấu deprecated.
  - (b) Mặc định `strict` khi không có sandbox: an toàn nhưng làm hỏng verification trên macOS.
  - (c) Ít nhất: đặt `AGENT_BRIDGE_AGENT=1` cho verification, và cảnh báo chặn (không chỉ một dòng `health`) khi `auto` hạ cấp. Biện pháp này yếu (xem SEC-03), chỉ chặn được đường gọi ngây thơ.
  - (d) Ký event human bằng một bí mật mà chỉ TTY của human có (ví dụ khoá trong keychain). Tốn công hơn.
- Acceptance criteria / verification method: test trên trở thành FAIL. `OUTSIDE_WORKTREE.txt` không được tạo hoặc check fail. T0001 vẫn APPROVED. Có thêm một test cho thấy `describe("auto")` trên macOS báo sandbox đang hoạt động, hoặc verification bị từ chối kèm thông báo rõ ràng.

### SEC-02 — Action trên web dashboard chạy `python -m bridge` với cwd = project root, nên một gói `bridge/` trong worktree được thực thi thay cho kit
- Severity: High      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P0
- Evidence:
  - `webactions.py:104`: `argv = [sys.executable, "-m", "bridge", action, tid, *args]`. `webactions.py:30-38`: `cwd=root`. Với `-m`, Python đặt cwd vào `sys.path[0]`.
  - Tái hiện `test_web_action_executes_project_bridge_package`: CLI giả ghi `src/app.py`, `bridge/__init__.py` (rỗng) và `bridge/__main__.py = "import pathlib\npathlib.Path('<tmp>/WEB_ACTION_RAN_PROJECT_CODE').write_text('pwned')"`. Kết quả: `state after run: REWORK ['outside allowed paths: bridge/__init__.py', 'outside allowed paths: bridge/__main__.py']` (vi phạm được phát hiện, nhưng tệp vẫn nằm lại). Sau đó `webactions.run_action(..., {"action": "resume"})` → `web resume: 202 {'ok': True, 'action': 'resume', 'started': True}`, và tệp marker chứa `pwned`.
- Expected vs actual: kỳ vọng nút trên dashboard chạy bridge đã cài. Thực tế nó chạy mã do executor viết, với quyền của user và ngoài mọi sandbox.
- Root cause & impact:
  - Tên gói top-level là `bridge`, rất phổ biến (`pyproject.toml`: `packages = ["bridge"]`), và Python import từ cwd. Lỗi xảy ra với `resume`/`execute` (`start_detached`) và với `review`/`cancel` (`run_sync`).
  - Chỉ cần một vi phạm phạm vi đã bị *phát hiện* (task ở REWORK) là đủ. Khi kit tự làm việc trên chính repo của nó, một thay đổi *hợp lệ* trong `bridge/**` cũng đủ.
  - Một dự án đích sẵn có thư mục `bridge/` hay tệp `bridge.py` cũng làm hỏng dashboard.
- Options & trade-offs: dùng `[sys.executable, "-I", "-m", "bridge", …]` hoặc `-P` (Python ≥3.11, không chèn cwd vào sys.path), hoặc gọi entry point theo đường dẫn tuyệt đối. Truyền project qua `BRIDGE_PROJECT_ROOT` (đã có) và chạy với `cwd` nằm ngoài worktree. Lưu ý `-I` bỏ qua cả `PYTHONPATH`, điều này có thể ảnh hưởng tới cách chạy dev.
- Acceptance criteria / verification method: test trên FAIL (marker không được tạo). Một test hồi quy với `bridge/__main__.py` độc hại trong repo đích chạy cho cả 4 action.

### SEC-03 — Human gate không phải ranh giới đối với mã cùng user: biến môi trường, TTY giả và API Python không có bảo vệ
- Severity: Medium      - Confidence: Confirmed
- Kind: Intentional limitation (một phần đã được ghi trong tài liệu) nhưng có hệ quả cụ thể
- Priority: P1
- Evidence:
  - `cli.py:30-36` (`isatty` cộng gõ lại id). `cli.py:434` và `server.py:200` (chỉ kiểm tra `AGENT_BRIDGE_AGENT`). `service.py:757-763`.
  - Tái hiện `test_human_approve_tty_gate_via_script`: chạy `sh -c "(sleep 3; echo T0001; sleep 3) | script -q /dev/null <venv>/bin/python -m bridge approve T0001"` với `stdin=DEVNULL` → `script rc: 0`, task chuyển `"state": "DONE"`.
  - `env -u AGENT_BRIDGE_AGENT … -m bridge list --json` với `AGENT_BRIDGE_AGENT=1` → rc 0.
  - SEC-01 cho thấy event giả `actor=human, {'tty': True}`.
  - `.claude/settings.json` chỉ deny theo chuỗi (`Bash(*-m bridge approve*)`, `Bash(*sqlite3*)`) và không bao phủ `script …`, `python -c "…human_approve…"` hay ghi DB bằng Python. Quan sát thêm: bản **chưa commit** trong working tree đã xoá dòng `"Bash(*agent-bridge approve*)"` có ở HEAD (`git diff .claude/settings.json`). Tôi không sửa gì; người dùng nên kiểm tra lại thay đổi đó.
- Expected vs actual: tài liệu nói approve "requires an interactive terminal". Thực tế, bất kỳ tiến trình cùng user nào cũng thoả mãn được điều kiện đó, và log thì ghi `tty: True`.
- Root cause & impact: mọi thứ chạy dưới cùng một UID. Gate dựa vào việc agent đã bị giới hạn. Agent Claude có `allow_bash`, hoặc mã chạy trong verification (SEC-01), có thể vượt gate. Audit trail không phân biệt được human thật với mã giả mạo.
- Options & trade-offs:
  - (a) Đổi tên event thành `tty_confirmed` và không khẳng định danh tính.
  - (b) Yêu cầu xác thực ngoài kênh (khoá trong macOS Keychain hoặc Touch ID, hay một passphrase do human đặt và lưu hash ngoài phạm vi agent đọc được).
  - (c) Thêm `AGENT_BRIDGE_AGENT` vào env của verification để chặn đường dễ nhất.
  - (d) Ghi rõ trong `security.md` rằng gate chỉ chống được MCP client tuân thủ.
- Acceptance criteria / verification method: hoặc tài liệu cùng tên event phản ánh đúng giới hạn, hoặc tái hiện bằng `script` cộng API Python bị từ chối.

### SEC-04 — Config được bảo vệ bị sửa thì được phát hiện nhưng không bị chặn lại; `.ai/orchestrator.yaml` (git-ignored) vô hình với `health`
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P1
- Evidence:
  - `models.py:122-124` (BLOCKED, yêu cầu "inspect and restore"). `review.py:249-254` (`protected_dirty` dùng `--untracked-files=no`). `review.py:94-95` (snapshot có gồm orchestrator.yaml). `config.py:64` có comment "Never writable by Codex", nhưng argv Codex (`executor.py:385-406`) không loại trừ đường dẫn nào.
  - Tái hiện `test_protected_config_tamper_persists`: CLI giả ghi `.ai/config.json = {"protected_paths": [], "verify_allowlist": ["sh","bash"]}` → BLOCKED. `load_config(repo)` trả `protected_paths == []` và `"bash" in verify_allowlist`. `Bridge(repo)` khởi động không từ chối.
  - Tái hiện `test_gitignored_orchestrator_tamper`: `.ai/orchestrator.yaml` có stages `[execute, test, review]`. CLI giả đổi thành `[execute, test]` → `tamper state: BLOCKED`, `health protected_dirty: []`, `next bridge stages: ['execute', 'test']`. Theo `orchestration.md`, không có stage review thì bridge tự approve.
- Expected vs actual: kỳ vọng một thay đổi bị chặn sẽ không ảnh hưởng tới lần chạy sau. Thực tế, config đã bị sửa có hiệu lực từ lần khởi động tiếp theo, và với yaml thì không có cảnh báo nào.
- Root cause & impact: chỉ phát hiện, không khôi phục và không chặn khởi động. Executor có thể tắt stage review, nới allowlist hoặc đặt `verify_sandbox: none` cho các task sau. Các git hook bị sửa (`review.py:105-113`) cũng chỉ được phát hiện, rồi vẫn chạy khi human commit.
- Options & trade-offs:
  - (a) Lưu hash của `.ai/*` và `.git/hooks`/`config` lúc human chạy `config-check`, rồi từ chối khởi động khi hash lệch.
  - (b) Khôi phục nội dung baseline khi BLOCKED vì protected path. Có rủi ro mất chỉnh sửa hợp lệ của human đang làm song song.
  - (c) Thêm vào argv Codex các writable root loại trừ `.ai`, nếu Codex hỗ trợ (cần kiểm chứng với CLI thật).
- Acceptance criteria / verification method: hai test trên chuyển thành: bridge kế tiếp từ chối khởi động, hoặc `health` báo `protected_dirty` gồm `.ai/orchestrator.yaml`.

### SEC-05 — Ghi xuyên qua symlink nằm trong allowed path không để lại dấu vết
- Severity: Medium      - Confidence: Confirmed (pipeline bằng chứng, CLI giả); Hypothesis (Codex/Claude thật)
- Kind: Grounded risk
- Priority: P2
- Evidence:
  - `security.py:88-96` chỉ resolve prefix literal. `review.py:44-80`: snapshot dựa trên git, mà git coi symlink là một blob.
  - Tái hiện `test_symlink_write_through`: commit `src/out -> <tmp>/outside_dir`. `validate_pattern("src/out/**")` bị từ chối đúng như mong đợi. Nhưng với `allowed_paths: ["src/**"]`, CLI giả ghi `src/out/evil.txt` → `symlink: DONE ['src/app.py'] []`, trong khi `outside_dir/evil.txt == "through link"`.
- Expected vs actual: kỳ vọng ghi ra ngoài project là một vi phạm. Thực tế bằng chứng hoàn toàn trống.
- Root cause & impact:
  - Executor có thể tự tạo symlink trong một allowed path (git chỉ thấy một tệp mới hợp lệ) rồi ghi qua nó. Seatbelt của Codex nhiều khả năng chặn, vì đường dẫn thật nằm ngoài writable root, nhưng chưa kiểm chứng.
  - Quy tắc `Edit(./src/**)` của Claude có resolve symlink hay không thì chưa rõ. Mã chạy trong verification trên macOS thì làm được mà không cần symlink (SEC-01).
- Options & trade-offs: coi mọi symlink mới hoặc đã đổi trong diff mà trỏ ra ngoài root là vi phạm (rẻ: `os.readlink` cộng `resolve`). Có thể từ chối luôn symlink trong allowed path lúc tạo task.
- Acceptance criteria / verification method: test trên cho ra violation `symlink escapes project: src/out`, và một test tạo symlink mới trong cùng attempt.

### SEC-06 — Tiến trình con đã `setsid` thoát khỏi lệnh kill group và ghi sau khi task đã DONE
- Severity: Medium      - Confidence: Confirmed (CLI giả); Hypothesis (Codex thật)
- Kind: Confirmed defect (tồn dư của SEC-04 cũ)
- Priority: P2
- Evidence:
  - `osutil.end_group` chỉ giết process group.
  - Tái hiện `test_setsid_child_escapes_group_kill`: wrapper `codex` chạy nền `python3 -c "import os,time\nif os.fork()==0:\n os.setsid()\n time.sleep(3)\n open('LATE_OUTSIDE.txt','w').write('late')"`, rồi `exec` CLI giả. Task DONE; sau 5 s, `LATE_OUTSIDE.txt` tồn tại.
- Expected vs actual: kỳ vọng không có gì do agent khởi chạy sống lâu hơn lần chạy. Thực tế nó ghi sau DONE và không xuất hiện trong bằng chứng.
- Root cause & impact: process group không phải ranh giới. Với Codex thật, seatbelt kế thừa sang tiến trình con nên việc ghi vẫn chỉ trong worktree, nhưng lại diễn ra *sau* snapshot. Kết hợp với SEC-07, tệp không bị ràng buộc sẽ không bị phát hiện.
- Options & trade-offs: kiểm tra lại các tiến trình mà cha chung là agent (`ps -o ppid`) hoặc dùng session id. Trên Linux dùng cgroup hoặc `PR_SET_CHILD_SUBREAPER`, trên macOS không có tương đương rẻ. Dù sao vẫn nên chụp snapshot lại ngay trước approve (xem SEC-07).
- Acceptance criteria / verification method: test trên FAIL, hoặc approve phát hiện `LATE_OUTSIDE.txt`.

### SEC-07 — Phê duyệt chỉ ràng buộc với tệp mà attempt chạm tới; `verified_head` được lưu nhưng không bao giờ được so sánh
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect (tồn dư của SEC-05 cũ)
- Priority: P1
- Evidence:
  - `service.py:733-749` (chỉ `after_hashes`). `runner.py:291-293`. Grep `verified_head` chỉ ra đúng một chỗ, là lúc ghi.
  - Tái hiện `test_old_sec05_untouched_file_not_bound`: task high-risk ở APPROVED. Sau đó tạo `src/conftest.py` và chạy `git commit --allow-empty` → `human_approve` → DONE.
- Expected vs actual: kỳ vọng human phê duyệt đúng trạng thái đã được verify. Thực tế, tệp mới (ví dụ `conftest.py`, sẽ được nạp ở lần test sau) và HEAD mới đều lọt qua.
- Root cause & impact: thiết kế cố ý bỏ qua tệp ngoài attempt ("other tasks' work"). Hệ quả là các ghi muộn (SEC-06) hay của mã verification (SEC-01) không bị bắt. Ngoài ra kiểm tra hash và `transition` không atomic (TOCTOU, cửa sổ nhỏ; đã đọc mã, chưa đo).
- Options & trade-offs:
  - (a) So `verified_head` với `current_head` khi approve: rẻ.
  - (b) So toàn bộ `tree` của worktree với tree sau verification, và chỉ bỏ qua các tệp đã được ghi nhận thuộc về task khác.
  - (c) Giữ thiết kế hiện tại nhưng hiển thị "HEAD đã đổi / có N tệp mới" trong màn hình approve.
- Acceptance criteria / verification method: test trên bị từ chối với thông báo "HEAD moved since verification" hoặc liệt kê tệp mới.

### SEC-08 — Khi có stage review, thay đổi tệp git-ignored chỉ được báo cáo, không bị chặn
- Severity: Low      - Confidence: Confirmed
- Kind: Grounded risk (tồn dư của SEC-03 cũ)
- Priority: P2
- Evidence:
  - `review.py:215-234` (`enforce_ignored` chỉ khi không có review). `runner.py:224-225`.
  - Chạy lại repro cũ với `stages: [execute, test, review]` → `SEC03 review-stage: REVIEW [] ['build/sitecustomize.py']`.
- Expected vs actual: tệp như `.venv/…/sitecustomize.py` sẽ chạy ở mọi lần Python khởi động trong venv đó (kể cả verification sau). Hiện tại nó chỉ hiện trong `ignored_files`, và reviewer có thể bỏ sót.
- Root cause & impact: phụ thuộc vào việc reviewer đọc mục "Changed git-ignored files" (`executor.py:737-738`). Một reviewer agent đã bị injection có thể bỏ qua mục này.
- Options & trade-offs: coi các tệp ignored nằm ngoài allowed path là vi phạm ngay cả khi có review, trừ `IGNORED_NOISE`. Đổi lại sẽ có thêm dương tính giả với các build artifact.
- Acceptance criteria / verification method: chạy lại repro với review → vi phạm `ignored file changed outside allowed paths: build/sitecustomize.py`.

### SEC-09 — `redact` bỏ sót các dạng credential phổ biến
- Severity: Medium      - Confidence: Confirmed
- Kind: Confirmed defect
- Priority: P2
- Evidence:
  - `security.py:113-139`. Regex key=value đòi hỏi `[:=]` đứng ngay sau tên key, nên khoá JSON có dấu nháy không khớp.
  - `test_redact_gaps` và `test_redact_json_tokens`: những chuỗi sau đi ra **không đổi**:
    - `{"password": "hunter2hunter2"}`
    - `sk_live_…`
    - `AIza…`
    - `github_pat_…`
    - `npm_…`
    - `https://user:s3cr3tpass@example.com/x`
    - `{"refresh_token": "rt_…"}`
    - `"access_token": "…"`
  - Ngược lại, `sk-ant-api03-…`, `OPENAI_API_KEY=…` và `refresh_token: …` đều được redact.
- Expected vs actual: kỳ vọng token bị che trong patch, log và event đã lưu. Thực tế chúng được lưu nguyên văn.
- Root cause & impact: agent đọc gần như mọi thứ, kể cả `auth.json` của account home khác (Codex không có deny đọc). Một executor bị injection có thể chép token vào một tệp được phép. Patch sau đó được lưu vào store, hiện trên dashboard và đưa vào prompt của reviewer LLM mà không bị redact.
- Options & trade-offs: thêm các pattern `"(key)"\s*:\s*"…"`, `sk_(live|test)_`, `AIza[0-9A-Za-z_-]{35}`, `github_pat_`, `npm_`, `://[^/\s:@]+:[^@\s]+@`. Redaction vẫn chỉ là biện pháp phụ: nên kết hợp với việc deny đọc account home cho Codex (cần kiểm chứng xem Codex có hỗ trợ không).
- Acceptance criteria / verification method: `test_redact_gaps` liệt kê `NOT redacted: []`.

### SEC-10 — Env scrubbing vẫn để lọt biến mang credential
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement (tồn dư của SEC-09 cũ)
- Priority: P2
- Evidence: `config.py:75`, `security.py:144-153`. Chạy `clean_env` với cấu hình mặc định giữ lại: `['AGENT_BRIDGE_HOME', 'BRIDGE_PROJECT_ROOT', 'DOCKER_CONFIG', 'NETRC', 'NPM_CONFIG__AUTH', 'PYPI_PAT', 'SECRET_KEY', 'SENTRY_AUTH', 'STRIPE_KEY', 'VAULT_ADDR']`.
- Expected vs actual: biến kiểu `SECRET_KEY` hay `*_PAT` lẽ ra phải bị bỏ.
- Root cause & impact: danh sách pattern hẹp (`*_SECRET*` không khớp với `SECRET_KEY`). Mã chạy trong verification đọc được các biến này, và trên macOS có thể gửi chúng ra ngoài.
- Options & trade-offs: thêm `*SECRET*`, `*_KEY`, `*_PAT`, `*_AUTH*`, `NETRC`, `*NPM_CONFIG_*AUTH*`. Rủi ro là bỏ nhầm biến cần cho test; `env_allow` đã có để mở lại.
- Acceptance criteria / verification method: chạy lại đoạn kiểm tra `clean_env` → chỉ còn lại các biến không phải credential.

### SEC-11 — Allowlist cho verification gồm cả interpreter; nó không phải ranh giới
- Severity: Low      - Confidence: Confirmed
- Kind: Intentional limitation (tồn dư của SEC-07 cũ)
- Priority: P2
- Evidence: `config.py:60-63`. `test_old_sec07_interpreters_still_allowlisted`: `parse_command("python3 -c \"import os; os.system('id')\"")[0] == "python3"` và `parse_command("npx --yes some-remote-package")[0] == "npx"`.
- Expected vs actual: allowlist chỉ chặn được shell syntax, không chặn được việc chạy mã tuỳ ý. Lệnh verification do planner agent viết thì chạy mà không ai duyệt.
- Root cause & impact: thiết kế (verification vốn chạy test của agent). Mức nghiêm trọng thật đến từ SEC-01.
- Options & trade-offs: hiển thị và yêu cầu client xác nhận các lệnh verification do planner sinh ra. Ghi rõ trong tài liệu rằng allowlist không phải sandbox.
- Acceptance criteria / verification method: tài liệu hoặc UI nói rõ giới hạn này. Workflow từ planner yêu cầu xác nhận khi lệnh chứa `-c` hoặc `--yes`.

### SEC-12 — Cookie phiên của dashboard không tách theo cổng, và audit không phân biệt human trên web với MCP client
- Severity: Low      - Confidence: Confirmed (header); Likely (lộ sang cổng khác: theo RFC 6265, cookie không phân biệt cổng)
- Kind: Grounded risk
- Priority: P2
- Evidence:
  - `webui.py:138-139`: `bridge_session=…; HttpOnly; SameSite=Strict; Path=/`, không có tiền tố hay khoá nào gắn với cổng.
  - Trong `test_web_auth_controls`, một client không phải trình duyệt, có cookie và tự thêm `Sec-Fetch-Site: same-origin`, nhận được `404` từ `run_action`. 404 ở đây nghĩa là request **đã qua** kiểm tra auth/Origin, chứ không phải đã chạy action (task không tồn tại).
  - `webactions.py:104` gọi `review` CLI → `Actor.CLIENT` (`service.py:711-712`).
- Expected vs actual: kỳ vọng cookie chỉ gửi tới dashboard. Thực tế trình duyệt gửi `bridge_session` tới mọi server trên `127.0.0.1` ở *bất kỳ cổng nào* (ví dụ một dev server do agent hay verification khởi động). Server đó có thể dùng lại cookie trong 12 h.
- Root cause & impact: cookie không có cô lập theo cổng. Rủi ro thấp vì kẻ tấn công phải là tiến trình cục bộ được trình duyệt truy cập. Riêng, các quyết định review trên web được ghi là "client" giống như Claude.
- Options & trade-offs: đặt tên cookie kèm cổng hoặc nonce (`bridge_session_<port>`), thêm token CSRF trong header lấy từ trang. Ghi `actor=web` hoặc `detail.via="web"`.
- Acceptance criteria / verification method: cookie từ một phiên dashboard khác cổng bị từ chối. Event review từ web có `via: web`.

### SEC-13 — CI: action chỉ pin theo tag, `uv sync` không có `--locked`
- Severity: Low      - Confidence: Confirmed
- Kind: Improvement
- Priority: P2
- Evidence: `.github/workflows/ci.yml`: `actions/checkout@v4`, `astral-sh/setup-uv@v5`, `actions/setup-node@v4`, `run: uv sync`. Điểm tốt: `permissions: contents: read`, `pull_request` (không phải `pull_request_target`), `playwright@1.63.0` có pin. `pip-audit` cho cả 39 và 44 gói: `No known vulnerabilities found`.
- Expected vs actual: tag có thể bị dời sang commit khác. `uv sync` có thể âm thầm resolve lại nếu lock lệch với `pyproject.toml`.
- Root cause & impact: rủi ro chuỗi cung ứng thấp (CI không có secret).
- Options & trade-offs: pin action theo SHA kèm Dependabot, dùng `uv sync --locked`, và thêm job `pip-audit` (cần mạng).
- Acceptance criteria / verification method: workflow chỉ dùng SHA 40 ký tự. `uv sync --locked` fail khi lock lệch.

## Kết luận theo khu vực

| Khu vực | Kết luận |
|---|---|
| Web dashboard: xác thực, CSRF, Host/Origin, XSS | GOOD (SEC-12 là điểm phụ) |
| Web dashboard: thực thi action | **HIGH RISK** (SEC-02) |
| MCP server / CLI: human gate | NEEDS IMPROVEMENT (SEC-03; ranh giới chỉ đúng với client tuân thủ) |
| Injection / path / phạm vi | NEEDS IMPROVEMENT (SEC-04, SEC-05, SEC-08) |
| Cô lập agent và verification (macOS) | **HIGH RISK** (SEC-01; Landlock không có trên macOS, `auto` hạ cấp âm thầm) |
| Cô lập agent: Codex/Claude CLI thật | INSUFFICIENT EVIDENCE (không chạy CLI thật) |
| Toàn vẹn phê duyệt / TOCTOU | NEEDS IMPROVEMENT (SEC-07, SEC-06) |
| Redaction / env | NEEDS IMPROVEMENT (SEC-09, SEC-10) |
| Chuỗi cung ứng / CI | GOOD (SEC-13 là cải tiến) |
