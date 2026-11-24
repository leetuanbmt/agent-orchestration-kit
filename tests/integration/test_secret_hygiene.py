"""A configured Claude secret stays out of agents' reads, checks, stored text and the bridge's own env."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bridge import accounts, osutil, sandbox, security
from bridge.accounts import Account, credential_file
from bridge.config import BridgeConfig
from bridge.executor import ClaudeAdapter, RunRequest
from bridge.registry import registry_dir
from bridge.review import run_verification, verification_read_denies
from tests.conftest import run

# Short enough that no credential-shaped pattern in security.redact matches it.
TOKEN = "sk-ant-oat01-q9zx"
VAR = "AGENT_BRIDGE_CLAUDE_CL_TOKEN"

YAML = """version: 1
accounts:
  cl: {provider: anthropic, home: '{home}/cl', %s}
agents:
  claude: {adapter: claude-cli, account: cl, allow_write: true, allow_bash: false}
roles: {executor: claude}
workflow: {stages: [execute, test]}
"""


def leaks(*dirs: Path, skip: Path | None = None) -> list[str]:
    found = []
    for base in dirs:
        for path in base.rglob("*"):
            if path.is_file() and not path.is_symlink() and (skip is None or skip not in
                                                              path.parents and path != skip):
                if TOKEN.encode() in path.read_bytes():
                    found.append(str(path))
    return found


# @pt.id KIT-SECRETS-001-read-denies
# @pt.item The credential store and owner-named credential files are denied to Claude agents
# @pt.condition a Claude run with Bash and config credential_paths naming a token env file
# @pt.expected --disallowedTools has Read rules for ~/.agent-bridge/credentials and each credential path, and the Bash sandbox denyRead (shared with the Seatbelt verify profile) lists them
# @pt.target bridge.executor.ClaudeAdapter.build, bridge.review.verification_read_denies
# @pt.method unit
# @pt.legacy only account homes and global logins were denied
def test_read_denies_name_store(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    envfile = Path.home() / ".config" / "bridge.env"
    cfg = BridgeConfig(credential_paths=[str(envfile)])
    req = RunRequest("task", Account("cl", None, "anthropic"), "read-only", 60, allow_bash=True)
    argv = ClaudeAdapter(cfg, root).build(req, tmp_path)
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "Read(~/.agent-bridge/credentials/**)" in denied
    assert "Read(~/.config/bridge.env)" in denied
    settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
    deny_read = settings["sandbox"]["filesystem"]["denyRead"]
    store = str(registry_dir() / "credentials")
    assert store in deny_read and str(envfile) in deny_read
    assert {store, str(envfile)} <= set(verification_read_denies(root, [], cfg.credential_paths))


# @pt.id KIT-SECRETS-002-credential-paths
# @pt.item credential_paths entries must be absolute and expressible as deny rules
# @pt.condition a relative path, a quote and a space in credential_paths
# @pt.expected BridgeConfig rejects each one; ~ expands
# @pt.target bridge.config.BridgeConfig
# @pt.method unit
# @pt.legacy credential_paths did not exist
def test_credential_paths_validation():
    from pydantic import ValidationError
    assert BridgeConfig(credential_paths=["~/x.env"]).credential_paths == [str(Path.home() / "x.env")]
    for bad in ("rel/x.env", '/tmp/a"b', "/tmp/a b"):
        with pytest.raises(ValidationError):
            BridgeConfig(credential_paths=[bad])


# @pt.id KIT-SECRETS-003-check-reads-store
# @pt.item A verification command cannot read the credential store where the confine can deny reads
# @pt.condition a 0600 token file in the store; a check that prints it, under the auto confine
# @pt.expected Seatbelt (macOS): the check fails and its output holds no token; Landlock (Linux) restricts writes only, so the check reads it, as documented, and its stored output is still redacted
# @pt.target bridge.review.run_verification, bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy the store did not exist
def test_check_cannot_read_store(repo, tmp_path):
    path = credential_file(Account("cl", None, "anthropic", credential="file"))
    path.parent.mkdir(mode=0o700, parents=True)
    path.write_text(TOKEN + "\n")
    path.chmod(0o600)
    assert accounts.claude_token(Account("cl", None, "anthropic", credential="file")) == TOKEN
    cmd = f"python3 -c \"import sys; sys.stdout.write(open({str(path)!r}).read())\""
    out, = run(run_verification([cmd], repo, BridgeConfig(), confine="auto",
                                read_deny=verification_read_denies(repo, [])))
    assert TOKEN not in json.dumps(out)
    backend = sandbox.backend()
    if backend == "seatbelt":
        assert out["passed"] is False
    elif backend == "landlock":
        assert out["passed"] is True  # documented limit: Landlock cannot deny one subtree's reads


# @pt.id KIT-SECRETS-004-exact-redaction
# @pt.item Every loaded credential is redacted by its exact value, whatever its shape
# @pt.condition an account whose token no credential pattern matches; a fake agent prints it in its final text, its summary and on stderr split across two writes
# @pt.expected no copy of the token in task_result, task_status, events, the run log or any file under the state store and HOME
# @pt.target bridge.security.redact, bridge.accounts.claude_token
# @pt.method integration
# @pt.legacy only credential-shaped substrings were redacted
def test_exact_value_redaction(make_orch, repo, tmp_path, monkeypatch, fake_claude):
    assert TOKEN in security.redact(TOKEN)  # no pattern matches this shape
    monkeypatch.setenv(VAR, TOKEN)
    monkeypatch.setenv("FAKE_CLAUDE_LEAK", "CLAUDE_CODE_OAUTH_TOKEN")
    b = make_orch(YAML % f"token_env: {VAR}")
    tid = run(b.create_task({"objective": "o", "allowed_paths": ["src/**"],
                             "acceptance_criteria": ["a"]}))["task_id"]
    run(b.execute_task(tid, wait=True))
    texts = json.dumps([run(b.task_result(tid)), run(b.task_status(tid)),
                        b.store.events(tid, 500)])
    assert "[REDACTED]" in texts
    assert TOKEN not in texts
    assert leaks(tmp_path, Path.home(), skip=fake_claude) == []


# @pt.id KIT-SECRETS-005-env-dropped
# @pt.item The bridge removes token variables from its own environment once read
# @pt.condition a token_env account whose variable an env_allow entry would let through the scrub; a check that fails when any AGENT_BRIDGE_CLAUDE_ variable is in its env
# @pt.expected the agent still gets the token as CLAUDE_CODE_OAUTH_TOKEN, the check passes, the variable is gone from os.environ, and bridge children started for the TUI and web get it back
# @pt.target bridge.accounts.capture_env_secrets, bridge.accounts.bridge_child_env, bridge.service.Bridge
# @pt.method integration
# @pt.legacy token variables stayed in the bridge's environment for every child
def test_bridge_drops_token_env(make_orch, monkeypatch, fake_claude):
    monkeypatch.setenv(VAR, TOKEN)
    b = make_orch(YAML % f"token_env: {VAR}", env_allow=[VAR])
    assert os.environ.get(VAR) is None  # never repr os.environ: it holds real secrets
    check = ("python3 -c \"import os,sys; "
             "sys.exit(any(k.startswith('AGENT_BRIDGE_CLAUDE_') for k in os.environ))\"")
    tid = run(b.create_task({"objective": "o", "allowed_paths": ["src/**"],
                             "acceptance_criteria": ["a"], "verification_commands": [check]}))
    tid = tid["task_id"]
    run(b.execute_task(tid, wait=True))
    checks = run(b.task_result(tid))["attempts"][-1]["verification"]["checks"]
    assert [c["passed"] for c in checks] == [True]
    logged = [json.loads(line) for line in (fake_claude / "log.jsonl").read_text().splitlines()]
    assert logged[-1]["secret_vars"] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert all(TOKEN not in json.dumps(entry["argv"]) for entry in logged)
    assert accounts.bridge_child_env({"PATH": "/bin"})[VAR] == TOKEN
    from bridge import webactions
    assert webactions._env(str(b.root))[VAR] == TOKEN


# @pt.id KIT-SECRETS-006-no-dumps
# @pt.item On Linux the bridge makes itself non-dumpable after loading credentials
# @pt.condition osutil.disable_dumps in a child interpreter
# @pt.expected Linux: PR_GET_DUMPABLE reads 0, so same-user processes cannot read /proc/<pid>/environ or memory; other systems: a no-op that does not fail
# @pt.target bridge.osutil.disable_dumps
# @pt.method integration
# @pt.legacy the bridge stayed dumpable
def test_disable_dumps():
    code = ("import ctypes, sys; from bridge import osutil; osutil.disable_dumps(); "
            "print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0) if sys.platform.startswith('linux') "
            "else 'skip')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(osutil.__file__).parents[1], check=True)
    assert out.stdout.strip() == ("0" if sys.platform.startswith("linux") else "skip")
