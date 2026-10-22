"""The terminal dashboard, driven headless through Textual's Pilot."""

from __future__ import annotations

from textual.widgets import DataTable, Input, OptionList, ProgressBar, Select, Static

from bridge.models import State
from bridge.tui import CURSOR, ActionMenu, BridgeApp, PatchView, account_rows
from tests.conftest import run, spec


def _select(app: BridgeApp, task_id: str, panel: str = "tasks") -> None:
    table = app.query_one(f"#{panel}", DataTable)
    table.focus()
    table.move_cursor(row=table.get_row_index(task_id))


async def _settle(pilot) -> None:  # noqa: ANN001 - workers refresh the table asynchronously
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


# @pt.id KIT-TUI-001-human-actions
# @pt.item Dashboard lists waiting tasks and performs approve, review and patch view
# @pt.condition one high-risk task waiting in APPROVED, one task waiting in REVIEW
# @pt.expected both rows are in the Needs you panel; `a` + Yes closes the approved task
#              as DONE by the human; `v` + notes sends the review task to REWORK; `p` shows the
#              patch; a key that does not fit the task's state does nothing
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
            assert app.query_one("#tasks", DataTable).row_count == 2
            assert app.query_one("#needs").border_title == "Needs you (2)"
            assert app.title == f"{b.root.name} · 2 need you"

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
            await pilot.press("a")  # does not fit REVIEW: nothing happens
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


# @pt.id KIT-TUI-002-account-rows
# @pt.item Accounts table shows everything the machine knows about
# @pt.condition project and registry accounts, an unadded codex-switch home, codex-switch and claude-switch profiles, an installed CLI without an account
# @pt.expected each appears once with its source; only real accounts are keyed by bare name; copied-token profiles are marked not usable and left out with profiles=False (the dashboard); an account whose login probe failed reads "logged out" whatever its stored state
# @pt.target bridge.tui.account_rows
# @pt.method unit
# @pt.legacy none
def test_account_rows() -> None:
    listing = {
        "accounts": [{"name": "default", "provider": "openai", "source": "project",
                      "logged_in": True, "state": "available", "quota": "UNKNOWN", "home": None},
                     {"name": "gm", "provider": "google", "source": "registry",
                      "logged_in": False, "state": "available", "quota": "UNKNOWN",
                      "home": "/h/gm"}],
        "codex_switch": {"homes": [{"name": "tv", "codex_home": "/c/tv", "logged_in": True,
                                    "configured": False},
                                   {"name": "used", "codex_home": "/c/u", "logged_in": True,
                                    "configured": True}],
                         "profiles": [{"name": "old"}]},
        "claude_switch": {"profiles": [{"name": "me", "email": "me@example.com"}]},
        "unconfigured": [{"provider": "opencode", "binary": "/bin/opencode"}],
    }
    rows = dict(account_rows(listing))
    assert list(rows) == ["default", "gm", "codex-switch:tv", "codex-profile:old",
                          "claude-switch:me", "installed:opencode"]
    assert rows["gm"][2] == "registry" and rows["default"][6] == "(global login)"
    assert str(rows["gm"][4]) == "logged out" and rows["default"][4] == "available"
    assert "not usable" in str(rows["claude-switch:me"][4])
    assert "not usable" in str(rows["codex-profile:old"][4])
    assert [k for k, _ in account_rows(listing, profiles=False)] == [
        "default", "gm", "codex-switch:tv", "installed:opencode"]


# @pt.id KIT-TUI-003-account-actions
# @pt.item Account keys in the dashboard
# @pt.condition fake agent CLIs on PATH; the human-only commands are intercepted
# @pt.expected the accounts table loads in the background with the installed-but-unused CLIs; selecting an account shows its provider and the agents using it in Detail (a CLI row shows "no account"); `n` on one asks for a name with that provider preselected and runs account-add; `u` on a google account only shows the eval line; `l` on a non-account row runs nothing
# @pt.target bridge.tui.BridgeApp
# @pt.method integration
# @pt.legacy none
def test_tui_account_actions(make_orch, monkeypatch) -> None:
    monkeypatch.setattr("bridge.accounts.codex_switch_profiles",
                        lambda: {"installed": False, "profiles": []})
    monkeypatch.setattr("bridge.accounts.claude_switch_profiles",
                        lambda: {"installed": False, "profiles": []})
    b = make_orch("""
accounts:
  cx: {provider: openai, home: {home}/cx}
  gm: {provider: google, home: {home}/gm}
agents:
  codex: {adapter: codex-cli, account: cx}
  agy: {adapter: agy-cli, account: gm}
roles: {executor: codex, reviewer: agy}
""")
    calls: list[tuple[str, ...]] = []

    async def drive() -> None:
        app = BridgeApp(b)
        monkeypatch.setattr(app, "_human_cmd", lambda *args: calls.append(args))
        async with app.run_test(size=(180, 45)) as pilot:
            await _settle(pilot)
            table = app.query_one("#accounts", DataTable)
            keys = [k.value for k in table.rows]
            assert keys[:2] == ["cx", "gm"] and "installed:opencode" in keys

            table.focus()
            table.move_cursor(row=keys.index("gm"))
            await pilot.pause()
            detail = str(app.query_one("#detail-text", Static).render())
            assert "google" in detail and "agy (reviewer)" in detail
            assert app.query_one("#detail").border_title == "gm"
            table.move_cursor(row=keys.index("installed:opencode"))
            await pilot.pause()
            assert "no account" in str(app.query_one("#detail-text", Static).render())

            table.move_cursor(row=keys.index("installed:anthropic"))
            await pilot.press("l")
            await _settle(pilot)
            assert calls == []
            await pilot.press("n")
            await pilot.pause()
            assert str(app.screen.query_one("#choice", Select).value) == "anthropic"
            app.screen.query_one("#text", Input).value = "work"
            await pilot.click("#ok")
            await _settle(pilot)
            assert calls == [("account-add", "work", "--provider", "anthropic")]

            table.move_cursor(row=keys.index("gm"))
            await pilot.press("u")
            await _settle(pilot)
            assert len(calls) == 1
            assert any("account-use gm" in str(n.message) for n in app._notifications)

    run(drive())


# @pt.id KIT-TUI-004-panels
# @pt.item One-screen panels: Enter menu, search, cursor and quota bar
# @pt.condition one task in REVIEW and one CANCELLED; the executor account has reported 30% used
# @pt.expected Enter on the REVIEW task offers only the actions that fit it and runs the chosen one; the cursor marks the selected row; `/` filters the task list; the bottom bar shows the executor's quota left
# @pt.target bridge.tui.BridgeApp, bridge.tui.task_actions, bridge.render.remaining_pct
# @pt.method integration
# @pt.legacy none
def test_tui_panels(make_bridge, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_bridge()
    review = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(review, wait=True))
    other = run(b.create_task(spec(objective="Other thing")))["task_id"]
    run(b.cancel_task(other, "not needed"))
    quota = {"primary": {"used_percent": 30.0}, "secondary": {"used_percent": 10.0}}
    monkeypatch.setattr(b, "_account_quota", lambda name: quota)

    async def drive() -> None:
        app = BridgeApp(b)
        async with app.run_test(size=(180, 45)) as pilot:
            await _settle(pilot)
            assert app.query_one("#quota", ProgressBar).progress == 70
            assert "70% left" in str(app.query_one("#quota-label", Static).render())

            _select(app, review, "needs")
            await _settle(pilot)
            assert app.query_one("#needs", DataTable).get_cell(review, "cur") == CURSOR
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ActionMenu)
            menu = app.screen.query_one(OptionList)
            offered = [menu.get_option_at_index(i).id for i in range(menu.option_count)]
            assert offered == ["review", "patch", "move", "cancel_task"]
            menu.highlighted = offered.index("patch")
            await pilot.press("enter")
            await _settle(pilot)
            assert isinstance(app.screen, PatchView) and "+x = 2" in app.screen.patch
            await pilot.press("escape")

            await pilot.press("slash")
            await pilot.press(*"other")
            await _settle(pilot)
            tasks = app.query_one("#tasks", DataTable)
            assert [k.value for k in tasks.rows] == [other]
            assert app.query_one("#needs").border_title == "Needs you (0)"

    run(drive())
