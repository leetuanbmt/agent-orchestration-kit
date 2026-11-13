"""`agent-bridge --help` explains itself to a first-time user; `init` ends with the next steps."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge import cli
from tests.conftest import git

EPILOG = ("getting started:\n"
          "  agent-bridge init          set up the kit in this git repository\n"
          "  agent-bridge health        check agent CLIs, logins and quota\n"
          "  agent-bridge config-init   list example role setups (optional)\n"
          "  claude                     open Claude Code in the project and ask for a change\n"
          "\n"
          "docs: https://github.com/leetuanbmt/agent-orchestration-kit#quick-start\n")

SUBCOMMAND_HELP = {
    "account-clear": "clear a QUOTA/AUTH unavailable mark on an account after a human fixed it",
    "execute": "start the executor agent on a READY task",
    "result": "full evidence of a task: patch, checks, scope violations, reviews",
    "resume": "re-run a REWORK, FAILED or PAUSED task, continuing its session",
    "status": "compact status of a task; --wait blocks until it leaves RUNNING/VERIFYING",
    "storage": "where bridge state lives; migrate, relink, prune and clean it up",
    "list": "tasks of this project, newest first",
    "cancel": "cancel a task that is not DONE or CANCELLED",
    "review": "record the client review of a task in REVIEW",
    "run-workflow": "run the tasks of a planned workflow in dependency order",
    "workflow-status": "status of a workflow and its tasks",
}

ARGUMENT_HELP = [
    (["status", "--help"], "seconds to wait, at most 55"),
    (["resume", "--help"], "notes for the agent's next run"),
    (["cancel", "--help"], "why the task is cancelled"),
    (["web", "--help"], "port on 127.0.0.1; 0 picks a free one (default 8765)"),
    (["web", "--help"], "open the dashboard in a browser"),
]


@pytest.fixture(autouse=True)
def wide_plain_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    # argparse wraps help at the terminal width; keep each help string on one line.
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _help(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)
    assert exited.value.code == 0
    return capsys.readouterr().out


# @pt.id T0100-help-usage-epilog
# @pt.item Top-level help is short and ends with getting started
# @pt.condition agent-bridge --help
# @pt.expected Exit 0; usage line shows <command>, not the subcommand list; output ends with the epilog
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none
def test_top_level_usage_and_epilog(capsys):
    out = _help(["--help"], capsys)
    usage = out.splitlines()[0]
    assert usage == "usage: agent-bridge [-h] <command> ..."
    assert "{" not in usage
    assert out.endswith(EPILOG)


# @pt.id T0100-help-subcommands
# @pt.item Every subcommand has a one-line help
# @pt.condition agent-bridge --help
# @pt.expected Each previously undocumented subcommand is listed with its help string
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize(("name", "text"), sorted(SUBCOMMAND_HELP.items()))
def test_subcommand_help_strings(capsys, name, text):
    lines = _help(["--help"], capsys).splitlines()
    assert any(line.split() == [name, *text.split()] for line in lines), name


# @pt.id T0100-help-storage-status
# @pt.item storage subcommands are documented
# @pt.condition agent-bridge storage --help
# @pt.expected Exit 0 with the help of `status`
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none
def test_storage_status_help(capsys):
    lines = _help(["storage", "--help"], capsys).splitlines()
    assert any(line.split() == ["status", *"show the state location and any legacy storage".split()]
               for line in lines)


# @pt.id T0100-help-arguments
# @pt.item Options of status, resume, cancel and web have help
# @pt.condition <command> --help
# @pt.expected Exit 0 with the given argument help
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize(("argv", "text"), ARGUMENT_HELP)
def test_argument_help(capsys, argv, text):
    assert text in _help(argv, capsys)


# @pt.id T0100-help-init-next-steps
# @pt.item init ends with the next steps
# @pt.condition agent-bridge init <fresh git repo>
# @pt.expected updated <file> lines, then the next steps block; no old "Open claude" hint
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none
def test_init_prints_next_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(tmp_path / "state"))
    proj = tmp_path / "proj"
    proj.mkdir()
    git(proj, "init", "-q")
    cli.main(["init", str(proj)])
    out = capsys.readouterr().out
    assert out.startswith("updated ")
    head, steps = out.split("next steps:\n", 1)
    assert all(line.startswith("updated ") for line in head.splitlines())
    assert steps == ("  1. agent-bridge health       check agent CLIs and logins\n"
                     "  2. claude                    open Claude Code here once and trust the "
                     "folder\n"
                     "  3. agent-bridge              terminal dashboard (or: agent-bridge web)\n")
    assert "Open claude in the project once" not in out


# @pt.id KIT-HELP-project-account-current
# @pt.item project-account-* help describes what the commands do now
# @pt.condition agent-bridge project-account-{add,limits,remove} --help
# @pt.expected No stale claim of adding an agent, editing roles.executor or being limited to added accounts
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none
def test_project_account_help_not_stale(capsys):
    texts = " ".join(" ".join(_help([c, "--help"], capsys).split())
                     for c in ("project-account-add", "project-account-limits",
                               "project-account-remove"))
    texts += " " + " ".join(_help(["--help"], capsys).split())
    for stale in ("add an account and its agent", "(after any default)",
                  "added with project-account-add"):
        assert stale not in texts
    assert "print the agent line to paste into .ai/orchestrator.yaml" in texts
    assert "(writes nothing)" in texts
    assert "of an account in .ai/accounts.yaml" in texts
    assert "its Keychain token; its home is kept" in texts
