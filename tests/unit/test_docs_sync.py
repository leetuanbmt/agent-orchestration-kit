"""Docs name every CLI command, BridgeConfig key and shipped example."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from bridge import cli
from bridge.config import BridgeConfig

ROOT = Path(__file__).resolve().parents[2]
CLI_DOC = ROOT / "docs" / "cli-reference.md"
CONFIG_DOC = ROOT / "docs" / "configuration.md"


class _Captured(Exception):
    def __init__(self, parser: argparse.ArgumentParser):
        self.parser = parser


def _cli_commands(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)

    def capture(self: argparse.ArgumentParser, *a: object, **k: object) -> None:
        raise _Captured(self)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(_Captured) as caught:
        cli.main([])
    for action in caught.value.parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    raise AssertionError("agent-bridge has no subcommands")


def _example_names() -> list[str]:
    return sorted(p.stem for p in (ROOT / "examples").glob("*.yaml")
                  if not p.name.endswith(".accounts.yaml"))


# @pt.id KIT-DOCS-001-cli-commands
# @pt.item CLI commands are documented
# @pt.condition every top-level agent-bridge subcommand is looked up in docs/cli-reference.md
# @pt.expected each appears as a code span starting with its name; a failure lists every missing command and names docs/cli-reference.md
# @pt.target bridge.cli.main, docs/cli-reference.md
# @pt.method unit
# @pt.legacy none
def test_every_cli_command_is_in_cli_reference(monkeypatch):
    text = CLI_DOC.read_text()
    commands = _cli_commands(monkeypatch)
    assert commands
    missing = [c for c in commands if not re.search(rf"`{re.escape(c)}[ `]", text)]
    assert not missing, (f"docs/cli-reference.md lacks CLI commands: {', '.join(missing)}; "
                         "add a row for each to the matching section of docs/cli-reference.md")


# @pt.id KIT-DOCS-002-config-keys
# @pt.item BridgeConfig keys are documented
# @pt.condition every BridgeConfig field is looked up in docs/configuration.md
# @pt.expected each appears as a `key` code span; a failure lists every missing key and names docs/configuration.md
# @pt.target bridge.config.BridgeConfig.model_fields, docs/configuration.md
# @pt.method unit
# @pt.legacy none
def test_every_config_key_is_in_configuration_doc():
    text = CONFIG_DOC.read_text()
    missing = [k for k in BridgeConfig.model_fields if f"`{k}`" not in text]
    assert not missing, (f"docs/configuration.md lacks BridgeConfig keys: {', '.join(missing)}; "
                         "add each to the `.ai/config.json` table in docs/configuration.md")


# @pt.id KIT-DOCS-003-examples
# @pt.item Shipped examples are listed
# @pt.condition every examples/<name>.yaml (not *.accounts.yaml) is looked up in docs/configuration.md
# @pt.expected each appears as a `name` code span; a failure lists every missing example and names docs/configuration.md
# @pt.target examples/*.yaml, docs/configuration.md
# @pt.method unit
# @pt.legacy none
def test_every_example_is_in_configuration_doc():
    names = _example_names()
    assert names
    text = CONFIG_DOC.read_text()
    missing = [n for n in names if f"`{n}`" not in text]
    assert not missing, (f"docs/configuration.md lacks shipped examples: {', '.join(missing)}; "
                         "add each to the Shipped examples table in docs/configuration.md")
