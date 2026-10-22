"""Human views of health / agents / list: a table in a terminal, the same JSON otherwise."""

import io
import json

import pytest
from rich.console import Console

from bridge import cli
from bridge.render import accounts_view, ago
from tests.conftest import run, spec


@pytest.fixture
def project(make_bridge, repo, monkeypatch, capsys):
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(repo))
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_bridge()
    task = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(task, wait=True))  # verified: waits in REVIEW for the client
    capsys.readouterr()
    return task


def _out(capsys, *argv, tty=True, monkeypatch=None):
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: tty)
    cli.main(list(argv))
    return capsys.readouterr().out


# @pt.id KIT-CLI-VIEW-001-tables
# @pt.item health, agents and list read as tables in a terminal
# @pt.condition one task waiting in REVIEW; stdout is a terminal, then a pipe, then --json
# @pt.expected the terminal gets tables (no JSON) with the next command for the waiting task; a pipe and --json get exactly the data the MCP tools return
# @pt.target bridge.cli.main, bridge.render.health_view, bridge.render.agents_view, bridge.render.tasks_view
# @pt.method integration
# @pt.legacy none
def test_views(project, capsys, monkeypatch):
    listing = _out(capsys, "list", monkeypatch=monkeypatch)
    assert "{" not in listing and project in listing and "REVIEW" in listing
    assert "● needs you:" in listing and f"agent-bridge result {project}" in listing
    agents = _out(capsys, "agents", monkeypatch=monkeypatch)
    assert "executor" in agents and "available" in agents and "{" not in agents
    health = _out(capsys, "health", monkeypatch=monkeypatch)
    assert " OK " in health and "Adapters" in health and "REVIEW 1" in health

    piped = json.loads(_out(capsys, "list", tty=False, monkeypatch=monkeypatch))
    assert piped[0]["task_id"] == project
    forced = json.loads(_out(capsys, "agents", "--json", monkeypatch=monkeypatch))
    assert {a["agent"] for a in forced["agents"]} >= {"default"}


# @pt.id KIT-CLI-VIEW-002-health-exit
# @pt.item health fails loudly when the bridge cannot run
# @pt.condition the codex CLI is not on PATH
# @pt.expected exit code 1 in a terminal and through a pipe; the terminal view says NOT OK and how to fix it
# @pt.target bridge.cli.main, bridge.render.health_view
# @pt.method integration
# @pt.legacy none
def test_health_exit(project, capsys, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    with pytest.raises(SystemExit) as failed:
        _out(capsys, "health", monkeypatch=monkeypatch)
    assert failed.value.code == 1
    out = capsys.readouterr().out
    assert "NOT OK" in out and "To fix:" in out and "not installed" in out
    with pytest.raises(SystemExit) as piped:
        _out(capsys, "health", tty=False, monkeypatch=monkeypatch)
    assert piped.value.code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


LISTING = {
    "accounts": [{"name": "work", "provider": "openai", "source": "registry", "logged_in": True,
                  "state": "available", "home": "/h/work",
                  "quota": {"primary": {"used_percent": 22}, "secondary": {"used_percent": 13}}}],
    "codex_switch": {"homes": [{"name": "tv", "codex_home": "/c/tv", "logged_in": True,
                                "configured": False}], "profiles": [{"name": "old"}]},
    "claude_switch": {"profiles": [{"name": "me"}]},
    "unconfigured": [{"provider": "google", "binary": "/bin/agy",
                      "hint": "agent-bridge account-add <name> --provider google"}],
}


def _text(renderable, width):
    console = Console(width=width, record=True, file=io.StringIO())
    console.print(renderable)
    return console.export_text()


# @pt.id KIT-CLI-VIEW-004-accounts-compact
# @pt.item account-list shows usable accounts, compactly on a narrow terminal
# @pt.condition one registry account plus a home not added, two switch profiles and a bare CLI
# @pt.expected by default only the account is listed, with a count of the hidden rows and the add hints; a narrow terminal drops Source and Home and shortens the quota; --all lists every row
# @pt.target bridge.render.accounts_view, bridge.cli.main
# @pt.method unit
# @pt.legacy none
def test_accounts_compact():
    narrow = _text(accounts_view(LISTING, width=80), 80)
    assert "work" in narrow and "5h 78% · wk 87%" in narrow
    assert "Home" not in narrow and "Source" not in narrow and "/h/work" not in narrow
    assert "old" not in narrow and "4 more not usable here" in narrow
    assert "--provider google" in narrow and "--home /c/tv" in narrow
    assert "\n\n\n" not in narrow  # no blank line between the hints and the note
    wide = _text(accounts_view(LISTING, width=160, show_all=True), 160)
    assert all(s in wide for s in ("Home", "/h/work", "old", "me", "not added", "no account"))
    assert "5h 78% left" in wide and "more not usable" not in wide


# @pt.id KIT-CLI-VIEW-003-ago
# @pt.item Relative times in the task list
# @pt.condition timestamps seconds, minutes, hours and days old, or missing
# @pt.expected compact "Ns/Nm/Nh/Nd ago" and "-" for none
# @pt.target bridge.render.ago
# @pt.method unit
# @pt.legacy none
def test_ago():
    assert [ago(1000 - d, 1000) for d in (5, 120, 7200, 172800)] == \
        ["5s ago", "2m ago", "2h ago", "2d ago"]
    assert ago(None) == "-"
