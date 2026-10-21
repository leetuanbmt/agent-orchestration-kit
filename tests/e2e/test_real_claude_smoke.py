"""Explicitly opted-in review by the installed real Claude CLI (fake Codex executes)."""

import os
import pwd
import shutil
from pathlib import Path

import pytest

from bridge.config import BridgeConfig
from bridge.models import Actor
from bridge.orchestrator import parse
from bridge.service import Bridge
from tests.conftest import FAKES, run, spec

CONFIG = """
version: 1
accounts:
  codex: {provider: openai}
  claude: {provider: anthropic}
agents:
  codex: {adapter: codex-cli, account: codex}
  claude: {adapter: claude-cli, account: claude, model: haiku, timeout_s: 300}
roles: {executor: codex, reviewer: claude}
"""


# @pt.id KIT-INTEG-005-real-claude-smoke
# @pt.item Real Claude reviewer decides on a verified change
# @pt.condition CLAUDE_SMOKE=1 and a real logged-in Claude CLI; the fake Codex makes the change
# @pt.expected Claude runs read-only without MCP servers and approves as AGENT; the task is DONE
# @pt.target ClaudeAdapter, Bridge._review_stage
# @pt.method e2e
# @pt.legacy none
@pytest.mark.skipif(os.environ.get("CLAUDE_SMOKE") != "1", reason="set CLAUDE_SMOKE=1 to opt in")
def test_real_claude_smoke(repo, fake_codex, monkeypatch):
    # The real CLI needs the real home for its login; the fake Codex stays first on PATH.
    monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
    claude = shutil.which("claude", path=os.pathsep.join(
        part for part in os.environ["PATH"].split(os.pathsep)
        if Path(part).resolve() != FAKES.resolve()))
    assert claude, "no real claude on PATH"
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    bridge = Bridge(repo, BridgeConfig(claude_bin=claude, lock_dir=str(fake_codex / "locks")),
                    parse(CONFIG))
    task = run(bridge.create_task(spec()))["task_id"]
    result = run(bridge.execute_task(task, wait=True))
    review = [e for e in bridge.store.events(task, 200) if e["kind"] == "review"]
    assert result["state"] == "DONE", (result, bridge.store.events(task, 200)[-3:])
    assert review[-1]["actor"] == Actor.AGENT and review[-1]["detail"]["reviewer"] == "claude"
