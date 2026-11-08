"""Verification subprocesses inherit the agent process restriction."""

import asyncio
from pathlib import Path

from bridge.accounts import Account
from bridge.config import BridgeConfig
from bridge.executor import CodexAdapter, RunRequest
from bridge.review import run_verification


# @pt.id KIT-SEC-VERIFY-ENV-001-agent-marker
# @pt.item Verification commands are marked as agent processes
# @pt.condition a verification subprocess checks AGENT_BRIDGE_AGENT
# @pt.expected the variable is 1 and the command passes
# @pt.target bridge.review.run_verification
# @pt.method integration
# @pt.legacy verification subprocesses lacked the agent marker
def test_verification_sets_agent_marker(repo: Path) -> None:
    command = ('python3 -c "import os,sys; '
               "sys.exit(0 if os.environ.get('AGENT_BRIDGE_AGENT') == '1' else 3)\"")
    results = asyncio.run(run_verification([command], repo, BridgeConfig()))
    assert len(results) == 1
    assert results[0]["passed"], results


# @pt.id KIT-SEC-VERIFY-ENV-002-ignore-allow
# @pt.item Only agent processes honor env_allow
# @pt.condition a synthetic variable matches env_deny and is re-allowed by env_allow
# @pt.expected the agent subprocess receives the value and the verification subprocess does not
# @pt.target bridge.executor.Adapter.run, bridge.review.run_verification
# @pt.method integration
# @pt.legacy verification commands inherited variables re-allowed for agents
def test_verification_ignores_env_allow(repo: Path, monkeypatch) -> None:
    name = "BRIDGE_TEST_PRIVATE_VALUE"
    monkeypatch.setenv(name, "synthetic-value")
    cfg = BridgeConfig(env_deny=["BRIDGE_TEST_PRIVATE_*"], env_allow=[name])
    agent = CodexAdapter(cfg, repo)
    monkeypatch.setattr(agent, "build", lambda req, tmp: [
        "python3", "-c",
        f"import os,sys; sys.exit(0 if os.environ.get('{name}') == 'synthetic-value' else 3)",
    ])
    monkeypatch.setattr(agent, "available", lambda: "python3")
    monkeypatch.setattr(agent, "finish", lambda res, tmp, ctx: True)
    req = RunRequest(prompt="", account=Account("default", None),
                     sandbox="read-only", timeout_s=10)
    result = asyncio.run(agent.run(req))
    assert result.outcome == "ok" and result.exit_code == 0, result

    command = (f'python3 -c "import os,sys; '
               f"sys.exit(0 if '{name}' not in os.environ else 3)\"")
    results = asyncio.run(run_verification([command], repo, cfg, confine="none"))
    assert len(results) == 1
    assert results[0]["passed"], results
