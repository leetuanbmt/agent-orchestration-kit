"""Readable terminal views for config-check, config-migrate and project-account-add."""

from __future__ import annotations

import json
import sys

from rich.console import Console

from bridge import cli, render

PASTE = 'agent-new: {"adapter": "codex-cli", "account": "new", "allow_write": true}'
CHECK = {"ok": True, "source": ".ai/orchestrator.yaml", "stages": ["plan", "execute"],
         "accounts": ["acc1"], "agents": ["codex"], "roles": {"executor": ["codex"]},
         "deprecated": ["acc1: declared in orchestrator.yaml; move it to .ai/accounts.yaml"],
         "warnings": ["accounts.acc1.limits: budget"]}
MOVES = ["account acc1: orchestrator.yaml -> accounts.yaml", "agent a: accounts.yaml -> orch"]
PREVIEW = {"changed": True, "applied": False, "moves": MOVES,
           ".ai/accounts.yaml": "accounts:\n  acc1: {provider: openai}\n",
           ".ai/orchestrator.yaml": "version: 1\nagents: {}\n",
           "note": "comments in orchestrator.yaml are not kept. Nothing was written."}
ADDED = {"account": "new", "agent": "agent-new", "home": "/h/new", "executor": True,
         "file": "/p/.ai/accounts.yaml",
         "paste": {"agents": PASTE, "roles.executor": "put agent-new first in roles.executor"},
         "next": "agent-bridge account-login new"}


def text(renderable) -> str:
    console = Console(record=True, width=100)
    console.print(renderable)
    return console.export_text()


# @pt.id T0146-config-view
# @pt.item config-check prints a readable view
# @pt.condition render a failing config with deprecated entries and warnings
# @pt.expected NOT OK, source, roles, errors, the Move section with the config-migrate hint
# @pt.target bridge.render.config_view
# @pt.method unit
# @pt.legacy none
def test_config_view():
    out = text(render.config_view({**CHECK, "ok": False, "errors": ["bad home"]}))
    assert "NOT OK" in out and ".ai/orchestrator.yaml" in out and "executor" in out
    assert "bad home" in out and "Move" in out and "agent-bridge config-migrate" in out
    assert "workflow.verify_*" in out and "accounts.acc1.limits" in out
    assert " OK " in text(render.config_view(CHECK))


# @pt.id T0146-migrate-view
# @pt.item config-migrate prints a view for each result shape
# @pt.condition render the unchanged, preview and applied results
# @pt.expected unchanged says nothing written; preview lists numbered moves, files and --apply; applied shows backup and config-check
# @pt.target bridge.render.migrate_view
# @pt.method unit
# @pt.legacy none
def test_migrate_view_shapes():
    same = text(render.migrate_view({"changed": False, "applied": False, "moves": [], "note": "x"}))
    assert "one-owner layout" in same and "nothing was written" in same
    prev = text(render.migrate_view(PREVIEW))
    assert "1. account acc1" in prev and "2. agent a" in prev
    assert ".ai/accounts.yaml" in prev and "acc1: {provider: openai}" in prev
    assert ".ai/orchestrator.yaml" in prev and "version: 1" in prev
    assert "Nothing written" in prev and "agent-bridge config-migrate --apply" in prev
    done = text(render.migrate_view({"changed": True, "applied": True, "backup": "/p/.ai/backup-1",
                                     "moves": MOVES}))
    assert "/p/.ai/backup-1" in done and "1. account acc1" in done
    assert "agent-bridge config-check" in done


# @pt.id T0146-added-view
# @pt.item project-account-add prints the paste line alone
# @pt.condition render an added account with an executor hint
# @pt.expected the paste line is its own line, the hint and the next command are shown
# @pt.target bridge.render.account_added_view
# @pt.method unit
# @pt.legacy none
def test_account_added_view():
    out = text(render.account_added_view(ADDED))
    assert PASTE in out.splitlines()
    assert "under `agents:` in .ai/orchestrator.yaml" in out and "/h/new" in out
    assert "put agent-new first in roles.executor" in out
    assert "agent-bridge account-login new" in out


# @pt.id T0146-json
# @pt.item --json and a non-terminal keep the JSON
# @pt.condition run main for config-check with --json on a tty, and without it on a pipe
# @pt.expected both print the returned dict as JSON
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none
def test_json_fallback(monkeypatch, capsys):
    async def fake_run(args):
        return CHECK

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.delenv(cli.AGENT_ENV, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cli.main(["config-check", "--json"])
    assert json.loads(capsys.readouterr().out) == CHECK
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    cli.main(["config-check"])
    assert json.loads(capsys.readouterr().out) == CHECK
