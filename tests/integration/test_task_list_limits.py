"""Task counts, truncation notices, and access to older tasks."""

import json

import pytest
from textual.widgets import DataTable, Input

from bridge import cli
from bridge.models import TaskSpec
from bridge.tui import BridgeApp
from tests.conftest import run, spec


@pytest.fixture
def many_tasks(make_bridge):
    b = make_bridge()
    for n in range(265):
        b.store.create(TaskSpec.model_validate(spec(objective=f"Change number {n}")), str(n))
    with b.store.tx() as db:
        db.execute("UPDATE tasks SET state='FAILED' WHERE id='T0001'")
    return b


# @pt.id T0113-cli-list-limits
# @pt.item CLI reports partial task lists without changing JSON shape
# @pt.condition 265 tasks; terminal, pipe, and explicit JSON with limit 500
# @pt.expected 200 rows and a 200 of 265 notice on the appropriate stream
# @pt.target bridge.cli.main, bridge.render.tasks_view
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("tty,force_json", [(True, False), (False, False), (True, True)])
def test_cli_list_limits(many_tasks, monkeypatch, capsys, tty, force_json):
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(many_tasks.root))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: tty)
    monkeypatch.setenv("COLUMNS", "160")
    cli.main(["list", "--limit", "500", *(["--json"] if force_json else [])])
    captured = capsys.readouterr()
    notice = "showing 200 of 265 tasks (newest first); narrow with --state"
    if tty and not force_json:
        assert notice in captured.out
        assert not captured.err
    else:
        tasks = json.loads(captured.out)
        assert isinstance(tasks, list) and len(tasks) == 200
        assert notice in captured.err


# @pt.id T0113-count-filter
# @pt.item Counts respect state filters and list caps remain unchanged
# @pt.condition 265 tasks including one FAILED task
# @pt.expected exact totals; list remains a capped list; filtered CLI has no false notice
# @pt.target bridge.service.Bridge.count_tasks, bridge.service.Bridge.list_tasks, bridge.cli.main
# @pt.method integration
# @pt.legacy none
def test_task_counts(many_tasks, monkeypatch, capsys):
    b = many_tasks
    assert run(b.count_tasks()) == 265
    assert run(b.count_tasks("READY")) == 264
    assert run(b.count_tasks("FAILED")) == 1
    assert run(b.count_tasks("DONE")) == 0
    assert len(run(b.list_tasks(limit=500))) == 200
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(b.root))
    cli.main(["list", "--state", "FAILED", "--json"])
    captured = capsys.readouterr()
    assert len(json.loads(captured.out)) == 1
    assert captured.err == ""


# @pt.id T0113-tui-older-tasks
# @pt.item Dashboard discloses truncation and exposes older tasks
# @pt.condition 265 tasks with the oldest task FAILED, then a lowercase exact-id search
# @pt.expected Tasks title says 200 of 265; old FAILED is in Needs you and search finds it
# @pt.target bridge.tui.BridgeApp.refresh_tasks
# @pt.method integration
# @pt.legacy none
def test_tui_older_tasks(many_tasks):
    async def drive():
        app = BridgeApp(many_tasks)
        async with app.run_test(size=(160, 45)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            tasks = app.query_one("#tasks", DataTable)
            needs = app.query_one("#needs", DataTable)
            assert tasks.row_count == 200
            assert tasks.border_title == "Tasks (200 of 265)"
            assert needs.get_row("T0001")
            assert needs.row_count == 1
            app.query_one("#search", Input).value = "t0001"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert tasks.row_count == 1
            assert tasks.get_row("T0001")
            app.query_one("#search", Input).value = "T9999"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert tasks.row_count == 0

    run(drive())
