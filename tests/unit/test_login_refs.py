"""Project-scoped accounts: registry logins are referenced, not inherited."""

from __future__ import annotations

import pytest

from bridge.orchestrator import parse
from bridge.service import resolve_account

LIMITS = {"five_hour_pct": 80}
REGISTRY = {
    "alpha": {"provider": "openai", "home": "/reg/alpha", "max_concurrency": 2,
              "limits": LIMITS},
    "beta": {"provider": "openai", "home": "/reg/beta"},
    "tuanvm": {"provider": "openai", "home": "/reg/tuanvm"},
    "tuanvm@google": {"provider": "google", "home": "/reg/tuanvm-google"},
}

BASE = """
agents:
  codex: {adapter: codex-cli, account: alpha}
  agy: {adapter: agy-cli, account: tuanvm}
roles: {executor: codex, reviewer: agy}
"""


# @pt.id KIT-LOGIN-REF-001-scoping
# @pt.item registry accounts are project-scoped: only the ones an agent references stay
# @pt.condition a registry of four logins; agents reference alpha and tuanvm (resolving to tuanvm@google)
# @pt.expected accounts hold the referenced ones only, the rest are in catalog, alpha keeps its registry settings
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_registry_scoped_to_referenced():
    orch = parse(BASE, registry=REGISTRY)
    assert set(orch.accounts) == {"alpha", "tuanvm@google"}
    assert set(orch.catalog) == {"beta", "tuanvm"}
    alpha = orch.accounts["alpha"]
    assert (alpha.home, alpha.max_concurrency) == ("/reg/alpha", 2)
    assert alpha.limits is not None and alpha.limits.five_hour_pct == 80
    assert orch.accounts_from_registry == ["alpha", "tuanvm@google"]
    assert "catalog" not in orch.model_dump()


# @pt.id KIT-LOGIN-REF-002-login
# @pt.item a login entry takes provider and home from the registry, limits from the project
# @pt.condition accounts.yaml declares work with login alpha and its own limits
# @pt.expected provider and home are alpha's, limits are the project's, not listed as registry-only
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_login_entry():
    text = """
agents:
  codex: {adapter: codex-cli, account: work}
roles: {executor: codex}
workflow: {stages: [execute, test]}
"""
    managed = {"accounts": {"work": {"login": "alpha", "limits": {"weekly_pct": 50}}}}
    orch = parse(text, registry=REGISTRY, managed=managed)
    work = orch.accounts["work"]
    assert (work.provider, work.home) == ("openai", "/reg/alpha")
    assert work.limits is not None
    assert work.limits.weekly_pct == 50 and work.limits.five_hour_pct is None
    assert work.max_concurrency == 1
    assert "work" not in orch.accounts_from_registry
    assert "alpha" not in orch.accounts and "alpha" in orch.catalog


# @pt.id KIT-LOGIN-REF-003-login-errors
# @pt.item a bad login entry is refused and names the project file
# @pt.condition a login entry with its own home, a different provider, or an unknown login
# @pt.expected ValueError naming accounts.yaml
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("entry, reason", [
    ({"login": "alpha", "home": "/own"}, "remove `home`"),
    ({"login": "alpha", "provider": "anthropic"}, "does not match login"),
    ({"login": "ghost"}, "unknown login"),
])
def test_login_errors(entry, reason):
    text = """
agents:
  codex: {adapter: codex-cli, account: work}
roles: {executor: codex}
workflow: {stages: [execute, test]}
"""
    with pytest.raises(ValueError, match="accounts.yaml") as err:
        parse(text, registry=REGISTRY, managed={"accounts": {"work": entry}},
              managed_source="accounts.yaml")
    assert reason in str(err.value)


# @pt.id KIT-LOGIN-REF-004-clash
# @pt.item the same account or agent in both project files is an error
# @pt.condition cx and codex are declared in orchestrator.yaml and in accounts.yaml
# @pt.expected ValueError naming the name and both files
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_clash_is_an_error():
    text = """
accounts:
  cx: {provider: openai}
agents:
  codex: {adapter: codex-cli, account: cx}
roles: {executor: codex}
"""
    for managed, name in (({"accounts": {"cx": {"provider": "openai"}}}, "cx"),
                          ({"agents": {"codex": {"adapter": "codex-cli"}}}, "codex")):
        with pytest.raises(ValueError) as err:
            parse(text, "o.yaml", None, managed, "a.yaml")
        message = str(err.value)
        assert repr(name) in message and "o.yaml" in message and "a.yaml" in message


# @pt.id KIT-LOGIN-REF-005-string-executor
# @pt.item a string executor role merges with a managed executor list
# @pt.condition roles.executor is "codex" and accounts.yaml lists executor [extra]
# @pt.expected the config loads with extra before codex
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_string_executor_with_managed_list():
    text = """
accounts:
  cx: {provider: openai, home: /tmp/cx}
agents:
  codex: {adapter: codex-cli, account: cx}
roles: {executor: codex}
workflow: {stages: [execute, test]}
"""
    managed = {"agents": {"extra": {"adapter": "codex-cli", "account": "cx"}},
               "executor": ["extra"]}
    orch = parse(text, managed=managed)
    assert orch.role("executor").agents == ["extra", "codex"]
    assert orch.agents_in_accounts_file == ["extra"]


# @pt.id KIT-LOGIN-REF-006-catalog-resolve
# @pt.item machine-wide account commands still find a registry login no agent uses
# @pt.condition beta is in the registry and referenced by no agent
# @pt.expected resolve_account returns it; an unknown name is still refused
# @pt.target bridge.service.resolve_account
# @pt.method unit
# @pt.legacy none
def test_resolve_account_uses_catalog(tmp_path):
    orch = parse(BASE, registry=REGISTRY)
    assert "beta" not in orch.accounts
    acc = resolve_account(orch, None, tmp_path, "beta")  # type: ignore[arg-type]
    assert acc.name == "beta" and acc.provider == "openai"
    with pytest.raises(ValueError, match="unknown account"):
        resolve_account(orch, None, tmp_path, "ghost")  # type: ignore[arg-type]
