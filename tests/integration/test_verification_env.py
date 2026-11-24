"""Verification subprocesses inherit the agent process restriction."""

import asyncio
import shutil
from pathlib import Path

import pytest

from bridge import sandbox

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


# @pt.id KIT-SEC-VERIFY-ENV-003-go-cache
# @pt.item A check gets GOCACHE and GOTMPDIR in its private TMPDIR unless the caller set them
# @pt.condition one run with neither set, one with GOCACHE set by the caller
# @pt.expected first: both are under TMPDIR; second: GOCACHE keeps the caller's value
# @pt.target bridge.review.run_verification
# @pt.method integration
# @pt.legacy G-130: go checks under Landlock failed writing ~/.cache/go-build
def test_verification_go_cache_in_private_tmp(repo: Path, monkeypatch) -> None:
    monkeypatch.delenv("GOCACHE", raising=False)
    monkeypatch.delenv("GOTMPDIR", raising=False)
    inside = ('python3 -c "import os,sys; t = os.environ[\'TMPDIR\']; '
              "sys.exit(0 if all(os.environ.get(k, '').startswith(t + os.sep) "
              "for k in ('GOCACHE', 'GOTMPDIR')) else 3)\"")
    assert asyncio.run(run_verification([inside], repo, BridgeConfig(), confine="none"))[0]["passed"]
    monkeypatch.setenv("GOCACHE", "/caller/cache")
    kept = ('python3 -c "import os,sys; '
            "sys.exit(0 if os.environ.get('GOCACHE') == '/caller/cache' else 3)\"")
    assert asyncio.run(run_verification([kept], repo, BridgeConfig(), confine="none"))[0]["passed"]


# @pt.id KIT-SEC-VERIFY-ENV-004-go-build-confined
# @pt.item A go build check passes under the verification sandbox with its cache in the check's TMPDIR
# @pt.condition go installed, a sandbox backend available, a one-file Go module, verify_sandbox auto
# @pt.expected `go build ./...` passes and `go env GOCACHE` prints a path in an agent-bridge-check- directory
# @pt.target bridge.review.run_verification
# @pt.method integration
# @pt.legacy G-130: `go test` failed with permission denied on ~/.cache/go-build under Landlock
@pytest.mark.skipif(shutil.which("go") is None or sandbox.backend() is None,
                    reason="go or a verification sandbox is unavailable")
def test_go_build_under_sandbox(repo: Path, monkeypatch) -> None:
    monkeypatch.delenv("GOCACHE", raising=False)
    monkeypatch.delenv("GOTMPDIR", raising=False)
    monkeypatch.setenv("GOTOOLCHAIN", "local")
    monkeypatch.setenv("GOFLAGS", "-mod=mod")
    (repo / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
    (repo / "main.go").write_text("package main\n\nfunc main() {}\n")
    build, where = asyncio.run(run_verification(
        ["go build -o /dev/null ./...", "go env GOCACHE"], repo, BridgeConfig(), confine="auto"))
    assert build["passed"], build
    assert where["passed"] and "agent-bridge-check-" in where["output_tail"], where
