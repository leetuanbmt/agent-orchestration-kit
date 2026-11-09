"""Claude Bash confinement settings and owner hook merging."""

import json
from pathlib import Path

import pytest

from bridge.accounts import Account
from bridge.config import BridgeConfig
from bridge.executor import ClaudeAdapter, RunRequest
from bridge.review import verification_read_denies


# @pt.id KIT-SANDBOX-CLAUDE-001
# @pt.item Bash sandbox settings and merged hooks
# @pt.condition Bash enabled with or without owner hooks and multiple account homes
# @pt.expected One settings file enforces confinement, all read denies and security rules
# @pt.target ClaudeAdapter.build
# @pt.method unit
# @pt.legacy Bash had no OS confinement
@pytest.mark.parametrize("hooks", [False, True])
def test_bash_settings(tmp_path, hooks):
    root = tmp_path / "project"
    root.mkdir()
    run_tmp = tmp_path / "run"
    run_tmp.mkdir()
    homes = [tmp_path / "account-one", tmp_path / "account-two"]
    hook_data = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}
    hook_file = tmp_path / "hooks.json"
    hook_file.write_text(json.dumps(hook_data))
    req = RunRequest("task", Account("claude", homes[0], "anthropic"), "read-only", 60,
                     allow_bash=True, read_deny=homes,
                     hook_settings=str(hook_file) if hooks else None)
    argv = ClaudeAdapter(BridgeConfig(), root).build(req, run_tmp)
    assert argv.count("--settings") == 1
    generated = Path(argv[argv.index("--settings") + 1])
    assert generated.parent == run_tmp
    settings = json.loads(generated.read_text())
    sandbox = settings.pop("sandbox")
    denies = sandbox.pop("filesystem")
    assert sandbox == {
        "enabled": True, "allowUnsandboxedCommands": False,
        "autoAllowBashIfSandboxed": True, "failIfUnavailable": True,
        "network": {"allowedDomains": []},
    }
    expected = verification_read_denies(root, homes) + [
        str(Path.home() / p) for p in (".codex", ".claude", ".codex-accounts",
                                      ".claude-accounts", ".gemini", ".agent-bridge/homes")]
    assert set(denies["denyRead"]) == set(expected)
    assert settings == (hook_data if hooks else {})
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "Bash(security:*)" in denied
    assert "Bash(/usr/bin/security:*)" in denied


# @pt.id KIT-SANDBOX-CLAUDE-002
# @pt.item Runs without Bash preserve settings argv
# @pt.condition Bash disabled with or without an owner hook settings path
# @pt.expected No generated files or security rules; original hook path passed unchanged
# @pt.target ClaudeAdapter.build
# @pt.method unit
# @pt.legacy Read/Edit tool permission rules remain unchanged
@pytest.mark.parametrize("hooks", [None, "/owner/hooks.json"])
def test_without_bash(tmp_path, hooks):
    req = RunRequest("task", Account("claude", None, "anthropic"), "read-only", 60,
                     hook_settings=hooks)
    argv = ClaudeAdapter(BridgeConfig(), tmp_path).build(req, tmp_path)
    prefix = ["claude", "-p", "--output-format", "stream-json", "--verbose",
              "--strict-mcp-config", "--setting-sources", ""]
    if hooks:
        prefix += ["--settings", hooks]
    prefix += ["--permission-mode", "dontAsk", "--allowedTools", "Read,Glob,Grep",
               "--json-schema", json.dumps(req.schema), "--disallowedTools"]
    assert argv[:-1] == prefix
    assert "Bash(" not in argv[-1]
    assert not list(tmp_path.iterdir())


# @pt.id KIT-SANDBOX-CLAUDE-003
# @pt.item Invalid owner hook settings fail closed
# @pt.condition Hook settings disappear or contain invalid JSON or non-object data
# @pt.expected Clear claude_hook_settings error and no sandbox settings file
# @pt.target ClaudeAdapter.build
# @pt.method unit
# @pt.legacy Owner hook settings validation remains enforced
@pytest.mark.parametrize("content", [None, "{", "[]", "{}", '{"hooks": []}'])
def test_invalid_hooks(tmp_path, content):
    root = tmp_path / "project"
    root.mkdir()
    hook_file = tmp_path / "hooks.json"
    if content is not None:
        hook_file.write_text(content)
    req = RunRequest("task", Account("claude", None, "anthropic"), "read-only", 60,
                     allow_bash=True, hook_settings=str(hook_file))
    with pytest.raises(ValueError, match="claude_hook_settings"):
        ClaudeAdapter(BridgeConfig(), root).build(req, root)
    assert not list(root.iterdir())
