"""Owner-configured Claude hooks remain separate from project settings."""

import pytest

from bridge.models import State
from tests.conftest import run, spec
from tests.integration.test_orchestration import CONFIG_B, CONFIG_D, log


# @pt.id KIT-SEC-CLAUDE-050-hook-settings-argv
# @pt.item Optional Claude hooks-only settings
# @pt.condition Claude executor runs with the option unset or an external hooks-only file
# @pt.expected Only the configured run adds --settings and both disable setting sources
# @pt.target Bridge._start, ClaudeAdapter.build
# @pt.method integration
# @pt.legacy Default Claude argv has no --settings
@pytest.mark.parametrize("configured", [False, True])
def test_hook_settings_argv(make_orch, fake_claude, tmp_path, configured):
    settings = tmp_path / "hooks.json"
    settings.write_text('{"hooks": {}}')
    b = make_orch(CONFIG_B, claude_hook_settings=str(settings) if configured else None)
    tid = run(b.create_task(spec(verification_commands=[])))["task_id"]
    run(b.execute_task(tid, wait=True))
    call, = log(fake_claude)
    argv = call["argv"]
    assert argv[argv.index("--setting-sources") + 1] == ""
    if configured:
        assert argv[argv.index("--setting-sources") + 2:argv.index("--setting-sources") + 4] == [
            "--settings", str(settings)]
    else:
        assert "--settings" not in argv


# @pt.id KIT-SEC-CLAUDE-051-unsafe-hook-settings
# @pt.item Unsafe settings are refused before execution
# @pt.condition Settings contain extra keys, malformed JSON, unsafe paths or invalid hooks
# @pt.expected ValueError names claude_hook_settings; READY and attempts zero; no CLI starts
# @pt.target Bridge._start, bridge.security.hook_settings_error
# @pt.method integration
# @pt.legacy Claude settings were always disabled
@pytest.mark.parametrize("case", [
    "permissions", "env", "enabledMcpjsonServers", "apiKeyHelper", "statusLine",
    "non-object", "invalid", "relative", "missing", "inside", "directory",
    "hooks-list", "no-hooks", "symlink-inside",
])
def test_unsafe_hook_settings(make_orch, fake_claude, tmp_path, repo, case):
    settings = tmp_path / "hooks.json"
    content = '{"hooks": {}}'
    if case in {"permissions", "env", "enabledMcpjsonServers", "apiKeyHelper", "statusLine"}:
        content = '{"hooks": {}, "' + case + '": {}}'
    elif case == "non-object":
        content = "[]"
    elif case == "invalid":
        content = "{"
    elif case == "hooks-list":
        content = '{"hooks": []}'
    elif case == "no-hooks":
        content = "{}"
    if case == "inside":
        settings = repo / "hooks.json"
    if case == "directory":
        settings.mkdir()
    elif case == "symlink-inside":
        target = repo / "hooks.json"
        target.write_text(content)
        settings.symlink_to(target)
    elif case != "missing":
        settings.write_text(content)
    path = "hooks.json" if case == "relative" else str(settings)
    b = make_orch(CONFIG_B, claude_hook_settings=path)
    tid = run(b.create_task(spec(verification_commands=[])))["task_id"]
    with pytest.raises(ValueError, match="claude_hook_settings"):
        run(b.execute_task(tid, wait=True))
    row = b.store.get(tid)
    assert row["state"] == State.READY
    assert row["attempts"] == 0
    assert not log(fake_claude)


# @pt.id KIT-SEC-CLAUDE-052-codex-ignores-hook-settings
# @pt.item Claude settings do not affect Codex
# @pt.condition Codex executor runs with a missing Claude settings file configured
# @pt.expected Codex starts without --settings and Claude never starts
# @pt.target Bridge._start
# @pt.method integration
# @pt.legacy Other adapter argv remains unchanged
def test_codex_ignores_hook_settings(make_orch, fake_codex, fake_claude, tmp_path):
    b = make_orch(CONFIG_D, claude_hook_settings=str(tmp_path / "missing.json"))
    tid = run(b.create_task(spec(verification_commands=[])))["task_id"]
    run(b.execute_task(tid, wait=True))
    assert log(fake_codex)
    assert all("--settings" not in call["argv"] for call in log(fake_codex))
    assert not log(fake_claude)
