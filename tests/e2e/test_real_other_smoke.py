"""Explicitly opted-in reviews by the installed real opencode and agy CLIs (fake Codex executes)."""

import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

from bridge.config import BridgeConfig
from bridge.models import Actor
from bridge.orchestrator import parse
from bridge.service import Bridge
from tests.conftest import FAKES, git, run, spec

CONFIG = """
version: 1
accounts:
  codex: {{provider: openai}}
  other: {{provider: {provider}}}
agents:
  codex: {{adapter: codex-cli, account: codex}}
  other: {{adapter: {adapter}, account: other, model: "{model}", timeout_s: 600}}
roles: {{executor: codex, reviewer: other}}
"""
CASES = {
    "opencode": ("OPENCODE_SMOKE", "opencode", "opencode-cli", "opencode_bin",
                 os.environ.get("OPENCODE_SMOKE_MODEL", "ollama-cloud/gpt-oss:120b")),
    "agy": ("AGY_SMOKE", "google", "agy-cli", "agy_bin",
            os.environ.get("AGY_SMOKE_MODEL", "gemini-3.8-flash-low")),
}


# @pt.id KIT-INTEG-006-real-other-smoke
# @pt.item Real opencode / agy reviewer decides on a verified change
# @pt.condition OPENCODE_SMOKE=1 or AGY_SMOKE=1 and that CLI logged in globally; the fake Codex makes the change
# @pt.expected the reviewer runs read-only, leaves the worktree exactly as the executor left it and decides as AGENT; an approval ends DONE
# @pt.target OpenCodeAdapter, AgyAdapter, Bridge._review_stage
# @pt.method e2e
# @pt.legacy none
@pytest.mark.parametrize("name", CASES)
def test_real_other_smoke(name, repo, fake_codex, monkeypatch):
    flag, provider, adapter, bin_key, model = CASES[name]
    if os.environ.get(flag) != "1":
        pytest.skip(f"set {flag}=1 to opt in")
    # The real CLI needs the real home for its login; the fake Codex stays first on PATH.
    monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
    exe = shutil.which(name, path=os.pathsep.join(
        part for part in os.environ["PATH"].split(os.pathsep)
        if Path(part).resolve() != FAKES.resolve()))
    assert exe, f"no real {name} on PATH"
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    bridge = Bridge(repo, BridgeConfig(**{bin_key: exe, "lock_dir": str(fake_codex / "locks")}),
                    parse(CONFIG.format(provider=provider, adapter=adapter, model=model)))
    task = run(bridge.create_task(spec()))["task_id"]
    result = run(bridge.execute_task(task, wait=True))
    events = bridge.store.events(task, 200)
    review = [e for e in events if e["kind"] == "review"]
    assert review, events[-3:]
    assert review[-1]["actor"] == Actor.AGENT and review[-1]["detail"]["reviewer"] == "other"
    git(repo, "add", "-A")
    # .ai/ is the bridge's own; anything else besides the executor's file came from the reviewer.
    changed = git_out(repo, "diff", "--cached", "--name-only", "HEAD", "--", ".", ":!.ai")
    assert changed == ["src/app.py"], changed
    if review[-1]["to_state"] == "APPROVED":
        assert result["state"] == "DONE", result


def git_out(root: Path, *args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                         check=True)
    return out.stdout.split()
