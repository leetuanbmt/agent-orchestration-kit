import asyncio
import os

import pytest

from bridge.models import TaskSpec


# @pt.id T0120-status-runner-liveness
# @pt.item Status and list report runner liveness without recovery
# @pt.condition Active tasks have dead, missing, current-process, or live runner PIDs
# @pt.expected Summaries report liveness and database rows stay unchanged
# @pt.target bridge.service.Bridge.task_status, bridge.service.Bridge.list_tasks
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("state", ["RUNNING", "VERIFYING", "READY"])
@pytest.mark.parametrize("runner", ["dead", "missing", "owned", "live", "reused"])
def test_read_only_runner_liveness(make_bridge, monkeypatch, state, runner):
    bridge = make_bridge()
    try:
        row, _ = bridge.store.create(TaskSpec(
            objective="Check runner liveness", allowed_paths=["src/**"],
            acceptance_criteria=["Read-only views report dead runners"]), "liveness")
        tid = row["id"]
        pid = {"dead": 2_147_483_647, "missing": None,
               "owned": os.getpid(), "live": 12345, "reused": 12345}[runner]
        if runner in {"live", "reused"}:
            monkeypatch.setattr("bridge.security.osutil.alive", lambda pid: True)
            monkeypatch.setattr("bridge.security.process_started", lambda pid: "live start")
        started = "live start" if runner == "live" else "old start"
        bridge.store.update(tid, state=state, runner_pid=pid, runner_started=started)
        before = bridge.store.get(tid)
        changes = bridge.store.db.total_changes
        expected = None if state == "READY" else runner in {"owned", "live"}
        status = asyncio.run(bridge.task_status(tid))
        listed = asyncio.run(bridge.list_tasks())
        assert status["runner_alive"] is expected
        assert listed[0]["runner_alive"] is expected
        assert status["state"] == listed[0]["state"] == state
        assert bridge.store.get(tid) == before
        assert bridge.store.db.total_changes == changes
    finally:
        bridge.store.db.close()
