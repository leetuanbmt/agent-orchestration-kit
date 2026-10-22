"""The terminal dashboard, driven headless through Textual's Pilot."""

from __future__ import annotations

from textual.widgets import DataTable, Input, Static

from bridge.models import State
from bridge.tui import BridgeApp, PatchView
from tests.conftest import run, spec


def _select(app: BridgeApp, task_id: str) -> None:
    table = app.query_one("#task-table", DataTable)
    table.move_cursor(row=table.get_row_index(task_id))


async def _settle(pilot) -> None:  # noqa: ANN001 - workers refresh the table asynchronously
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


# @pt.id KIT-TUI-001-human-actions
# @pt.item Dashboard lists waiting tasks and performs approve, review and patch view
# @pt.condition one high-risk task waiting in APPROVED, one task waiting in REVIEW
# @pt.expected both rows are flagged as needing the human; `a` + Yes closes the approved task
#              as DONE by the human; `v` + notes sends the review task to REWORK; `p` shows the
#              patch; approving a task in the wrong state only shows a notification
# @pt.target bridge.tui.BridgeApp
# @pt.method integration
# @pt.legacy none (new kit)
def test_tui_human_actions(make_bridge, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_bridge()
    high = run(b.create_task(spec(risk_level="high")))["task_id"]
    run(b.execute_task(high, wait=True))
    run(b.review_task(high, "approve", "looks right"))
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/other.py=y = 3\n")
    review = run(b.create_task(spec(objective="Second change")))["task_id"]
    run(b.execute_task(review, wait=True))
    assert b.store.get(high)["state"] == State.APPROVED
    assert b.store.get(review)["state"] == State.REVIEW

    async def drive() -> None:
        app = BridgeApp(b)
        async with app.run_test(size=(160, 45)) as pilot:
            await _settle(pilot)
            assert app.query_one("#task-table", DataTable).row_count == 2
            assert app.title == "agent-bridge · 2 need you"

            _select(app, review)
            await _settle(pilot)
            detail = str(app.query_one("#detail-text", Static).render())
            assert "Needs you: review" in detail and "tries 1/3" in detail
            await pilot.press("c")  # escape closes the dialog without acting
            await pilot.pause()
            await pilot.press("escape")
            await _settle(pilot)
            assert app.screen is app.screen_stack[0]
            assert b.store.get(review)["state"] == State.REVIEW
            await pilot.press("a")  # wrong state: refused with a notification, no dialog
            await _settle(pilot)
            assert b.store.get(review)["state"] == State.REVIEW
            assert not isinstance(app.screen, PatchView)

            await pilot.press("p")
            await _settle(pilot)
            assert isinstance(app.screen, PatchView) and "+y = 3" in app.screen.patch
            await pilot.press("escape")

            await pilot.press("v")  # the worker waits on the dialog: pause, do not settle
            await pilot.pause()
            await pilot.click("#choice")
            await pilot.press("down", "enter")  # approve -> rework
            app.screen.query_one("#text", Input).value = "rename the variable"
            await pilot.click("#ok")
            await _settle(pilot)
            assert b.store.get(review)["state"] == State.REWORK

            _select(app, high)
            await _settle(pilot)
            await pilot.press("a")
            await pilot.pause()
            await pilot.click("#yes")
            await _settle(pilot)
            assert b.store.get(high)["state"] == State.DONE

    run(drive())
    done = [e["actor"] for e in b.store.events(high) if e["to_state"] == "DONE"]
    assert done == ["human"]
