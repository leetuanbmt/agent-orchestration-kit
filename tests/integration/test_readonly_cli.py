"""Read-only CLI commands do not initialize project storage."""

import json

import pytest

from bridge import cli, storage
from bridge.accounts import known_projects
from bridge.service import Bridge
from tests.conftest import run, spec


def _cli(capsys, *args):
    cli.main(list(args))
    return json.loads(capsys.readouterr().out)


# @pt.id T0044-fresh
# @pt.item Fresh project views
# @pt.condition No bridge state exists under an isolated home
# @pt.expected Views return JSON without creating state or registering the project
# @pt.target bridge.cli.main, bridge.service.Bridge
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("command", ["health", "list", "agents", "account-list"])
def test_fresh_views(repo, fake_codex, tmp_path, monkeypatch, capsys, command):
    home = tmp_path / "bridge-home"
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(home))
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(repo))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    data = _cli(capsys, command)
    if command == "list":
        assert data == []
    else:
        assert isinstance(data, dict)
    for path in (repo / ".ai", repo / ".git/agent-bridge", home / "projects",
                 home / "accounts.db", home / "projects.json"):
        assert not path.exists()


# @pt.id T0044-existing
# @pt.item Existing task views
# @pt.condition A normal Bridge has created a task
# @pt.expected Read-only list, status and result preserve task data and create no files
# @pt.target bridge.cli.main, bridge.service.Bridge
# @pt.method integration
# @pt.legacy none
def test_existing_views(repo, make_bridge, monkeypatch, capsys):
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(repo))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    writer = make_bridge()
    task = run(writer.create_task(spec()))["task_id"]
    expected_list = run(writer.list_tasks())
    expected_status = run(writer.task_status(task))
    expected_result = run(writer.task_result(task))
    before = {p for base in (repo, storage.home()) for p in base.rglob("*")}
    assert _cli(capsys, "list") == expected_list
    assert _cli(capsys, "status", task) == expected_status
    assert _cli(capsys, "result", task) == expected_result
    assert {p for base in (repo, storage.home()) for p in base.rglob("*")} == before


# @pt.id T0044-default
# @pt.item Default Bridge initializes storage
# @pt.condition Bridge is constructed without create=False
# @pt.expected State database and project registration exist
# @pt.target bridge.service.Bridge
# @pt.method integration
# @pt.legacy none
def test_default_creates_state(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(tmp_path / "bridge-home"))
    bridge = Bridge(repo)
    assert (repo / ".ai").is_dir()
    assert (bridge.state_dir / "bridge.db").is_file()
    assert (storage.home() / "accounts.db").is_file()
    assert str(repo) in known_projects()
