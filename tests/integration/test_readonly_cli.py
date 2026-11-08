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
    monkeypatch.setattr("bridge.service.describe_sandbox", lambda mode: "landlock ABI 5: writes confined")
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


# @pt.id T0044-spec-errors
# @pt.item Unreadable spec files and unknown tasks give one-line errors
# @pt.condition `create` with a missing file, a non-JSON file and a JSON list; `status` of an unknown task
# @pt.expected exit 1 with "error: ..." naming the file and the problem, no traceback and no quotes around the unknown-task message; no task is created
# @pt.target bridge.cli._read_spec, bridge.state.NotFound
# @pt.method integration
# @pt.legacy a missing file raised FileNotFoundError with a traceback; unknown tasks printed 'unknown task: T9999'
def test_spec_errors(repo, fake_codex, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(repo))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    bad = tmp_path / "bad.json"
    for content, needle in ((None, "cannot read spec"), ("not json", "is not valid JSON"),
                            ("[1, 2]", "must be a JSON object, not list")):
        if content is not None:
            bad.write_text(content)
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["create", str(bad)])
        err = capsys.readouterr().err
        assert exit_info.value.code == 1 and err.startswith("error: ") and needle in err, err
        assert str(bad) in err
    with pytest.raises(SystemExit):
        cli.main(["status", "T9999"])
    assert capsys.readouterr().err == "error: unknown task: T9999\n"
    assert run(Bridge(repo).list_tasks()) == []
