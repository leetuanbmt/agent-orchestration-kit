"""Verification subprocesses inherit the agent process restriction."""

import asyncio
from pathlib import Path

from bridge.config import BridgeConfig
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
