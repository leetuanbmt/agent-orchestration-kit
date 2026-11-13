"""`cancel` closes a task from every state except DONE and CANCELLED, BLOCKED included."""

import json

import pytest

from bridge import cli
from bridge.models import ACTIVE, State
from tests.conftest import run, spec

CLOSED = {State.DONE, State.CANCELLED}


# @pt.id T0100-cancel-every-state
# @pt.item CLI cancel from each state
# @pt.condition A task forced into each State, then `agent-bridge cancel <id> --reason r`
# @pt.expected BLOCKED and every non-terminal state end CANCELLED with the reason in the
#   failure and the transition event; DONE and CANCELLED are refused as illegal
# @pt.target bridge.cli.main, bridge.service.Bridge.cancel_task, bridge.models.TRANSITIONS
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("state", list(State))
def test_cli_cancel_from_every_state(repo, make_bridge, monkeypatch, capsys, state):
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(repo))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    b = make_bridge()
    tid = run(b.create_task(spec()))["task_id"]
    b.store.update(tid, state=state)
    capsys.readouterr()
    if state in CLOSED:
        with pytest.raises(SystemExit):
            cli.main(["cancel", tid, "--reason", "committed by hand"])
        assert f"illegal transition {state} -> CANCELLED" in capsys.readouterr().err
        assert b.store.get(tid)["state"] == state
        return
    cli.main(["cancel", tid, "--reason", "committed by hand"])
    assert json.loads(capsys.readouterr().out)["state"] == "CANCELLED"
    row = b.store.get(tid)
    assert (row["state"], row["failure"]) == ("CANCELLED", "CANCELLED: committed by hand")
    last = b.store.events(tid)[-1]
    # A RUNNING/VERIFYING row with no live runner is parked as PAUSED by startup recovery
    # before the cancel reaches it.
    source = State.PAUSED if state in ACTIVE else state
    assert (last["from_state"], last["to_state"], last["actor"]) == (source, "CANCELLED", "client")
    assert last["detail"] == {"reason": "committed by hand"}


# @pt.id T0100-review-blocked
# @pt.item Review cannot close a BLOCKED task
# @pt.condition `agent-bridge review <id> --decision approve` on a BLOCKED task
# @pt.expected Refused; the task stays BLOCKED (cancel is the way to close it)
# @pt.target bridge.cli.main, bridge.service.Bridge.review_task
# @pt.method integration
# @pt.legacy none
def test_cli_review_refuses_blocked(repo, make_bridge, monkeypatch, capsys):
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(repo))
    b = make_bridge()
    tid = run(b.create_task(spec()))["task_id"]
    b.store.update(tid, state=State.BLOCKED)
    with pytest.raises(SystemExit):
        cli.main(["review", tid, "--decision", "approve", "--notes", "done by hand"])
    assert "BLOCKED" in capsys.readouterr().err
    assert b.store.get(tid)["state"] == "BLOCKED"


# @pt.id T0100-tui-cancel-blocked
# @pt.item TUI offers cancel on a BLOCKED task
# @pt.condition task_actions for BLOCKED, DONE and CANCELLED rows
# @pt.expected BLOCKED offers patch and cancel (no move); DONE and CANCELLED offer no cancel
# @pt.target bridge.tui.task_actions
# @pt.method unit
# @pt.legacy none
def test_tui_offers_cancel_for_blocked():
    from bridge.tui import task_actions

    def ids(state):
        return [a for a, _ in task_actions({"state": state, "needs_human": 0})]

    assert ids("BLOCKED") == ["patch", "cancel_task"]
    assert ids("DONE") == ids("CANCELLED") == ["patch"]
