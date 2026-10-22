"""Explicitly opted-in lifecycle against the installed real Codex CLI."""

import os
import pwd
import shutil
from pathlib import Path

import pytest

from bridge.config import BridgeConfig
from bridge.orchestrator import parse
from bridge.service import Bridge
from tests.conftest import FAKES, git, run, spec


# @pt.id KIT-INTEG-004-real-codex-smoke
# @pt.item Real Codex completes a verified one-file task
# @pt.condition CODEX_SMOKE=1 and a real authenticated Codex CLI is installed
# @pt.expected Task reaches REVIEW with a passing check and the expected change, then DONE
# @pt.target bridge.service.Bridge.execute_task, bridge.service.Bridge.review_task
# @pt.method e2e
# @pt.legacy none (new kit)
@pytest.mark.real_codex
@pytest.mark.skipif(os.environ.get("CODEX_SMOKE") != "1", reason="set CODEX_SMOKE=1 to opt in")
def test_real_codex_smoke(tmp_path, monkeypatch):
    # The real CLI needs the real home for its login (the suite isolates HOME).
    monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
    clean_path = os.pathsep.join(
        part for part in os.environ.get("PATH", "").split(os.pathsep)
        if Path(part).resolve() != FAKES.resolve()
    )
    monkeypatch.setenv("PATH", clean_path)
    codex = shutil.which("codex")
    assert codex and Path(codex).resolve() != (FAKES / "codex").resolve()
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src/app.py").write_text("x = 1\n")
    git(root, "init", "-q")
    git(root, "add", "src/app.py")
    git(root, "commit", "-qm", "Initial fixture")
    bridge = Bridge(root, BridgeConfig(codex_bin=codex, default_timeout_s=600,
                                      max_timeout_s=600, network_retries=0))
    task = run(bridge.create_task(spec(account="default", timeout_s=600,
                                     allowed_paths=["src/app.py"])))["task_id"]
    result = run(bridge.execute_task(task, wait=True))
    assert result["state"] == "REVIEW", result
    assert (root / "src/app.py").read_text() == "x = 2\n"
    attempt = run(bridge.task_result(task))["attempts"][-1]
    assert attempt["changes"]["files"] == ["src/app.py"]
    assert attempt["verification"]["passed"]
    assert run(bridge.review_task(task, "approve", "Verified the one-file change and check"))["state"] == "DONE"


SINGLE_AGENT = """
version: 1
accounts: {default: {provider: openai}}
agents: {codex: {adapter: codex-cli, account: default, timeout_s: 600}}
roles: {planner: codex, executor: codex, reviewer: codex}
workflow: {stages: [plan, execute, test, review]}
"""


# @pt.id KIT-INTEG-006-real-codex-workflow
# @pt.item Real Codex as planner, executor and reviewer
# @pt.condition CODEX_SMOKE=1, a real authenticated Codex CLI, the single-agent configuration
# @pt.expected Codex accepts the plan and review schemas read-only; the workflow ends DONE
# @pt.target Bridge.plan, Bridge.run_workflow, CodexAdapter
# @pt.method e2e
# @pt.legacy none
@pytest.mark.real_codex
@pytest.mark.skipif(os.environ.get("CODEX_SMOKE") != "1", reason="set CODEX_SMOKE=1 to opt in")
def test_real_codex_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv("PATH", os.pathsep.join(
        part for part in os.environ.get("PATH", "").split(os.pathsep)
        if Path(part).resolve() != FAKES.resolve()))
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src/app.py").write_text("x = 1\n")
    git(root, "init", "-q")
    git(root, "add", "src/app.py")
    git(root, "commit", "-qm", "Initial fixture")
    bridge = Bridge(root, BridgeConfig(codex_bin=shutil.which("codex"), max_timeout_s=600,
                                      network_retries=0), parse(SINGLE_AGENT))
    wf = run(bridge.plan("Change src/app.py so that it assigns x = 2 instead of x = 1. "
                         "One task; allowed path src/app.py.", wait=True))
    assert wf["state"] == "READY" and wf["tasks"], wf
    wf = run(bridge.run_workflow(wf["workflow_id"], wait=True))
    assert wf["state"] == "DONE", wf
    assert "x = 2" in (root / "src/app.py").read_text()
    reviews = run(bridge.task_result(wf["tasks"][0]["task_id"]))["reviews"]
    assert reviews[-1]["reviewer"] == "codex" and reviews[-1]["decision"] == "approve"
