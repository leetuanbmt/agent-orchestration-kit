"""E2E: secrets, isolation and the limits placed on agents, checked on real runs."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from tests.qa.conftest import Sandbox

SECRETS = {
    "OPENAI_API_KEY": "sk-proj-QAleak0123456789abcdefghijklmnop",
    "GITHUB_TOKEN": "ghp_QAleak0123456789abcdefghijklmnopqrst",
    "AWS_SECRET_ACCESS_KEY": "QAleakAWS0123456789abcdefghijklmnopqrstu",
}


# @pt.id QA-E2E-080-redaction
# @pt.item Secrets printed by a verification command are not stored
# @pt.condition a check prints a file holding an OpenAI key, a GitHub token and a full private key block
# @pt.expected neither the result nor the database holds the raw values
# @pt.target bridge.security.redact on stored output
# @pt.method e2e
# @pt.legacy none
def test_secrets_redacted_in_stored_output(sandbox: Sandbox) -> None:
    pem = ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n"
           "-----END OPENSSH PRIVATE KEY-----")
    leaked = f"key={SECRETS['OPENAI_API_KEY']}\n{SECRETS['GITHUB_TOKEN']}\n{pem}\n"
    (sandbox.root / "src" / "fixture.txt").write_text(leaked)  # what a check might print
    task = sandbox.create(verification_commands=[
        "python3 -c \"print(open('src/fixture.txt').read())\""])
    sandbox.ok("execute", task)
    stored = json.dumps(sandbox.ok("result", task))
    database = Path(sandbox.ok("health")["database"]).read_bytes()
    assert "[REDACTED]" in stored
    for raw in (SECRETS["OPENAI_API_KEY"], SECRETS["GITHUB_TOKEN"], "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU"):
        assert raw not in stored and raw.encode() not in database, raw


# @pt.id QA-E2E-081-verification-env
# @pt.item Verification commands do not see the user's credentials
# @pt.condition OPENAI_API_KEY, GITHUB_TOKEN and AWS_SECRET_ACCESS_KEY exported in the user's shell; a check prints its environment's variable names
# @pt.expected none of the three names reaches the check
# @pt.target bridge.review.run_verification env
# @pt.method e2e
# @pt.legacy none
def test_verification_env_scrubbed(sandbox: Sandbox) -> None:
    task = sandbox.create(verification_commands=[
        "python3 -c \"import os; print(sorted(os.environ))\""])
    sandbox.ok("execute", task, env=SECRETS)
    output = sandbox.ok("result", task)["attempts"][-1]["verification"]["checks"][0]["output_tail"]
    assert "PATH" in output
    for name in SECRETS:
        assert name not in output, output


# @pt.id QA-E2E-082-state-isolation
# @pt.item Bridge state lives outside the worktree, private to the user
# @pt.condition after a run with a log
# @pt.expected the database and logs are under $HOME/.agent-bridge, not in the project; git status shows no bridge files; directories 0700, logs 0600
# @pt.target bridge.storage, attempt logs
# @pt.method e2e
# @pt.legacy none
def test_state_outside_worktree_and_private(sandbox: Sandbox) -> None:
    task = sandbox.create()
    sandbox.ok("execute", task)
    health = sandbox.ok("health")
    database, state = Path(health["database"]), Path(health["state_dir"])
    assert sandbox.home / ".agent-bridge" in database.parents
    assert sandbox.root not in database.parents
    status = subprocess.run(["git", "-C", str(sandbox.root), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert ".db" not in status and "logs" not in status, status
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    logs = list((state / "logs").glob("*.jsonl"))
    assert logs and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in logs)
    assert stat.S_IMODE((state / "logs").stat().st_mode) == 0o700


# @pt.id QA-E2E-083-codex-sandbox-flags
# @pt.item What the Codex executor is allowed to do
# @pt.condition a normal run
# @pt.expected the argv asks for the workspace-write sandbox, never danger-full-access, and the run happens in the project
# @pt.target CodexAdapter argv
# @pt.method e2e
# @pt.legacy none
def test_codex_sandbox_flags(sandbox: Sandbox) -> None:
    sandbox.ok("execute", sandbox.create())
    call = sandbox.calls()[-1]
    argv = " ".join(call["argv"])
    assert "workspace-write" in argv and "danger" not in argv, argv
    assert Path(call["cwd"]).resolve() == sandbox.root


# @pt.id QA-E2E-084-claude-permissions
# @pt.item What a Claude CLI executor is allowed to do
# @pt.condition a claude-cli writer with allowed_paths src/**
# @pt.expected strict MCP config (no servers), no settings sources, dontAsk mode, Edit/Write scoped to ./src/**, the kit's credential read denies and protected-path write denies passed, no Bash
# @pt.target ClaudeAdapter argv
# @pt.method e2e
# @pt.legacy none
def test_claude_permission_flags(sandbox: Sandbox) -> None:
    sandbox.orchestrator("""
version: 1
accounts:
  cl1: {provider: anthropic, home: {home}/cl1}
agents:
  claude1: {adapter: claude-cli, account: cl1, allow_write: true}
roles: {executor: claude1}
workflow: {stages: [execute, test]}
""")
    sandbox.ok("execute", sandbox.create())
    argv = sandbox.calls("claude")[-1]["argv"]
    joined = " ".join(argv)
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Edit(./src/**)" in allowed and "Bash" not in allowed, allowed
    denied = argv[argv.index("--disallowedTools") + 1]
    assert "Read(~/.codex/auth.json)" in denied and "Write(./.env)" in denied, denied
    assert "danger" not in joined


# @pt.id QA-E2E-085-human-only-guard
# @pt.item The kit `init` installs keeps human-only commands away from Claude Code
# @pt.condition the .claude/settings.json written by `agent-bridge init`
# @pt.expected approve, reassign, account-add/login/remove and config-init are denied; no secret values in the installed files
# @pt.target bridge.install
# @pt.method e2e
# @pt.legacy none
def test_init_denies_human_only_commands(sandbox: Sandbox) -> None:
    settings = json.loads((sandbox.root / ".claude" / "settings.json").read_text())
    denied = " ".join(settings["permissions"]["deny"])
    for command in ("approve", "reassign", "account-add", "account-login", "account-remove"):
        assert command in denied, (command, denied)
    import re
    installed = "".join(p.read_text() for p in (sandbox.root / ".claude").rglob("*") if p.is_file())
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}", installed)


# @pt.id QA-E2E-086-verification-sandbox
# @pt.item Verification commands are confined to the project
# @pt.condition a task whose verification command (an allowlisted interpreter) writes a file in $HOME, outside the worktree
# @pt.expected the write is refused, or the file at least shows up as a violation
# @pt.target bridge.review.run_verification (audit finding SEC-01)
# @pt.method e2e
# @pt.legacy none
@pytest.mark.xfail(strict=True, reason="QA-BUG-001 (audit SEC-01, open): verification commands run "
                   "unsandboxed with the user's permissions; a task or planner spec can run any "
                   "code through an allowlisted interpreter")
def test_verification_cannot_write_outside(sandbox: Sandbox) -> None:
    target = sandbox.home / "written-by-a-check"
    task = sandbox.create(verification_commands=[
        f"python3 -c \"open('{target}', 'w').write('x')\""])
    sandbox.ok("execute", task)
    assert not target.exists(), "a verification command wrote outside the worktree"


# @pt.id QA-E2E-087-claude-credential-reads
# @pt.item A Claude agent cannot read Claude logins or the project's .env
# @pt.condition a claude-cli executor on an account whose home is outside ~/.agent-bridge/homes (as in every shipped example: ~/.claude-accounts/<name>)
# @pt.expected --disallowedTools holds Read denies for ~/.claude/.credentials.json, every configured anthropic account home and ./.env
# @pt.target ClaudeAdapter.build, .claude/settings.json deny list
# @pt.method e2e
# @pt.legacy none
@pytest.mark.xfail(strict=True, reason="QA-BUG-006: the Read denies cover Codex, agent-bridge and agy "
                   "credentials only; Claude logins, configured Claude homes and .env stay readable")
def test_claude_cannot_read_claude_credentials(sandbox: Sandbox) -> None:
    sandbox.orchestrator("""
version: 1
accounts:
  cl1: {provider: anthropic, home: {home}/cl1}
  cl2: {provider: anthropic, home: {home}/cl2}
agents:
  claude1: {adapter: claude-cli, account: cl1, allow_write: true}
roles: {executor: claude1}
workflow: {stages: [execute, test]}
""")
    sandbox.ok("execute", sandbox.create())
    argv = sandbox.calls("claude")[-1]["argv"]
    denied = argv[argv.index("--disallowedTools") + 1]
    other_home = str(sandbox.base / "homes" / "cl2")
    assert "Read(~/.claude/.credentials.json)" in denied, denied
    assert other_home in denied, "another account's Claude login is readable"
    assert "Read(./.env)" in denied, ".env is readable"
