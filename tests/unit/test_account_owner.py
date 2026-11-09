"""account_owner names the file a human edits to change an account."""

from __future__ import annotations

from bridge.config import BridgeConfig
from bridge.orchestrator import account_owner, legacy, parse

YAML = """version: 1
accounts:
  own: {provider: anthropic, home: /tmp/own-home}
agents:
  a-own: {adapter: claude-cli, account: own, allow_write: true}
  a-reg: {adapter: claude-cli, account: reg}
  a-man: {adapter: claude-cli, account: man}
roles: {executor: a-own}
workflow: {stages: [execute, test]}
"""


# @pt.id KIT-ACCT-OWNER-001
# @pt.item account_owner says where to change an account's settings
# @pt.condition an orchestrator.yaml account, an implicit registry account, an accounts.yaml account and a legacy account
# @pt.expected each gets its own text: orchestrator.yaml (move it), the registry with a login stanza, accounts.yaml with the limits command, and .ai/config.json
# @pt.target bridge.orchestrator.account_owner
# @pt.method unit
# @pt.legacy every message said .ai/orchestrator.yaml
def test_account_owner_texts():
    orch = parse(YAML, registry={"reg": {"provider": "anthropic", "home": "/tmp/reg-home"}},
                 managed={"accounts": {"man": {"provider": "anthropic", "home": "/tmp/man"}}})
    assert account_owner(orch, "own") == (
        "accounts.own in .ai/orchestrator.yaml (move it to .ai/accounts.yaml)")
    assert account_owner(orch, "reg") == (
        "the machine registry; declare it for this project: `reg: {login: reg}` in "
        ".ai/accounts.yaml (or run `agent-bridge config-migrate`)")
    assert account_owner(orch, "man") == (
        "accounts.man in .ai/accounts.yaml (`agent-bridge project-account-limits man`)")
    assert account_owner(legacy(BridgeConfig()), "default") == ".ai/config.json"
