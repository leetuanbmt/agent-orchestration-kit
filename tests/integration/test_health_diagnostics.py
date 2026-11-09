"""health explains itself: each problem and warning comes with a code, a message and a fix."""

from __future__ import annotations

import pytest

from bridge import cli, sandbox
from bridge.models import Actor
from bridge.orchestrator import AccountLimits, account_owner, parse
from tests.conftest import git, run
from tests.integration.test_account_limits import FIVE_HOUR, config, seed
from tests.integration.test_limit_hardening import REGISTRY_YAML, bridge_for, registry_bridge
from tests.integration.test_limit_failover import mark_quota

PATH_WITHOUT_FAKES = "/usr/bin:/bin"


@pytest.fixture(autouse=True)
def available_landlock(monkeypatch):
    monkeypatch.setattr(sandbox, "backend", lambda: "landlock")


def _health(b) -> dict:
    h = run(b.health())
    assert h["ok"] == (h["problems"] == [])
    for entry in [*h["problems"], *h["warnings"]]:
        assert set(entry) == {"code", "message", "fix"} and entry["message"] and entry["fix"]
    return h


def _codes(entries: list[dict]) -> list[str]:
    return [e["code"] for e in entries]


# @pt.id KIT-HEALTH-DIAG-1
# @pt.item A healthy bridge reports no problems
# @pt.condition the fake codex CLI is installed and logged in; the repo is clean
# @pt.expected ok is true, problems is empty and no account warning is listed
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none
def test_healthy_has_no_problems(make_bridge):
    h = _health(make_bridge())
    assert h["ok"] is True and h["problems"] == []
    assert not [c for c in _codes(h["warnings"]) if c.startswith("account_")]
    assert "verify_unconfined" not in _codes(h["warnings"])


# @pt.id KIT-HEALTH-DIAG-8
# @pt.item Unavailable verification confinement is a health problem
# @pt.condition auto or strict mode with no Landlock available
# @pt.expected ok is false with verify_unconfined and a fix naming both human options
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy unavailable confinement was only a warning
@pytest.mark.parametrize("mode", ["auto", "strict"])
def test_unavailable_sandbox_is_problem(make_bridge, monkeypatch, mode):
    monkeypatch.setattr(sandbox, "backend", lambda: None)
    b = make_bridge()
    b.orch.workflow.verify_sandbox = mode
    h = _health(b)
    assert h["ok"] is False
    assert _codes(h["problems"]) == ["verify_unconfined"]
    problem, = h["problems"]
    assert problem["message"] == f"verification sandbox: {sandbox.describe(mode)}"
    assert "Linux kernel with Landlock" in problem["fix"]
    assert "workflow.verify_sandbox: none in .ai/orchestrator.yaml" in problem["fix"]
    assert "verify_unconfined" not in _codes(h["warnings"])


# @pt.id KIT-HEALTH-DIAG-9
# @pt.item Explicitly accepted unconfined verification is a warning
# @pt.condition none mode with no Landlock available
# @pt.expected ok stays true and verify_unconfined warns that checks run unconfined by choice
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none mode did not explain unconfined verification
def test_unconfined_by_choice_is_warning(make_bridge, monkeypatch):
    monkeypatch.setattr(sandbox, "backend", lambda: None)
    b = make_bridge()
    b.orch.workflow.verify_sandbox = "none"
    h = _health(b)
    assert h["ok"] is True and h["problems"] == []
    warning, = [e for e in h["warnings"] if e["code"] == "verify_unconfined"]
    assert "checks run unconfined by choice" in warning["message"]


# @pt.id KIT-HEALTH-DIAG-2
# @pt.item A missing adapter is a problem
# @pt.condition the codex CLI is not on PATH
# @pt.expected problems is exactly adapter_missing for codex-cli with an install fix; ok is false
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy health returned ok false without saying why
def test_missing_adapter(make_bridge, monkeypatch):
    b = make_bridge()
    monkeypatch.setenv("PATH", PATH_WITHOUT_FAKES)
    h = _health(b)
    assert h["ok"] is False
    assert _codes(h["problems"]) == ["adapter_missing"]
    problem, = h["problems"]
    assert "codex-cli" in problem["message"] and "install the codex CLI" in problem["fix"]


# @pt.id KIT-HEALTH-DIAG-3
# @pt.item A dirty protected file is a problem
# @pt.condition the committed .ai/config.json differs from HEAD in the working tree
# @pt.expected problems is protected_dirty naming the file; ok is false
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none
def test_protected_dirty(make_bridge, repo):
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "config.json").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "cfg")
    (repo / ".ai" / "config.json").write_text("{ }\n")
    h = _health(make_bridge())
    assert h["ok"] is False
    assert _codes(h["problems"]) == ["protected_dirty"]
    problem, = h["problems"]
    assert ".ai/config.json" in problem["message"] and "git diff" in problem["fix"]


# @pt.id KIT-HEALTH-DIAG-4
# @pt.item Stored account marks are warnings, not problems
# @pt.condition two claude accounts; cl1 is marked quota and cl2 auth in the store
# @pt.expected warnings list account_quota for cl1 and account_auth for cl2 with the stored error and a fix; ok and problems do not change
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none
def test_account_marks_are_warnings(make_orch):
    b = make_orch(config())
    before = _health(b)
    b.store.set_account_state("cl1", "quota", "usage limit reached", actor=Actor.BRIDGE,
                              key=b._key("cl1"))
    b.store.set_account_state("cl2", "auth", "token expired", actor=Actor.BRIDGE,
                              key=b._key("cl2"))
    h = _health(b)
    assert h["ok"] == before["ok"] and h["problems"] == before["problems"]
    marks = {e["code"]: e for e in h["warnings"] if e["code"] in ("account_quota", "account_auth")}
    assert set(marks) == {"account_quota", "account_auth"}
    quota, auth = marks["account_quota"], marks["account_auth"]
    assert "cl1" in quota["message"] and "usage limit reached" in quota["message"]
    assert "routing.quota_fallback" in quota["fix"]
    assert "cl2" in auth["message"] and "token expired" in auth["message"]
    assert "agent-bridge account-login cl2" in auth["fix"]


# @pt.id KIT-HEALTH-DIAG-5
# @pt.item An account over its limits is a warning
# @pt.condition cl1 has five_hour_pct 80 and reports 90 % used
# @pt.expected warnings list account_limited for cl1 with the limit reason; ok is unaffected
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none
def test_limited_account_is_warning(make_orch):
    b = make_orch(config(cl1=FIVE_HOUR))
    seed(b, "cl1")
    h = _health(b)
    limited = [e for e in h["warnings"] if e["code"] == "account_limited"]
    assert len(limited) == 1 and "cl1" in limited[0]["message"]
    assert h["limits"]["cl1"]["reason"] in limited[0]["message"]
    assert "account_limited" not in _codes(h["problems"])


# @pt.id KIT-HEALTH-DIAG-9
# @pt.item The limit fix and the deprecation warning point at the file that owns the account
# @pt.condition a project that only uses the registry login reg1, over its five-hour limit; then a project whose accounts sit in accounts.yaml
# @pt.expected account_limited's fix holds account_owner's text, not .ai/orchestrator.yaml; config_deprecated is a warning and ok stays true; the accounts.yaml project has no config_deprecated
# @pt.target bridge.service.Bridge.health, bridge.orchestrator.account_owner
# @pt.method integration
# @pt.legacy the fix always said .ai/orchestrator.yaml
def test_limit_fix_names_owner_and_deprecated_warns(repo, fake_codex, fake_claude, fake_others):
    b = registry_bridge(repo, fake_codex)
    b.orch.accounts["reg1"].limits = AccountLimits(five_hour_pct=80)
    seed(b, "reg1")
    h = _health(b)
    limited, = [e for e in h["warnings"] if e["code"] == "account_limited"]
    assert account_owner(b.orch, "reg1") in limited["fix"]
    assert ".ai/orchestrator.yaml" not in limited["fix"]
    deprecated, = [e for e in h["warnings"] if e["code"] == "config_deprecated"]
    assert "config-check" in deprecated["fix"]
    assert "config_deprecated" not in _codes(h["problems"]) and h["ok"] is True

    home = fake_codex.parent / "homes" / "own"
    clean = bridge_for(repo, fake_codex, parse(
        REGISTRY_YAML.replace("reg1", "own"),
        managed={"accounts": {"own": {"provider": "anthropic", "home": str(home)}}}))
    h = _health(clean)
    assert "config_deprecated" not in _codes(h["warnings"]) and h["ok"] is True


# @pt.id KIT-HEALTH-DIAG-7
# @pt.item A lapsed QUOTA mark is not a warning, and health does not lift it
# @pt.condition cl1 has limits cooldown_s 600 and no quota snapshot; it was marked QUOTA 610 s ago
# @pt.expected warnings list no account_quota; the stored mark is still quota after health()
# @pt.target bridge.service.Bridge.health, bridge.gate.Gate._effective_account_state
# @pt.method integration
# @pt.legacy health warned about a mark that routing already treated as lapsed
def test_lapsed_quota_mark_is_not_warning(make_orch):
    b = make_orch(config(cl1="{cooldown_s: 600}"))
    mark_quota(b, "cl1", age_s=610)
    h = _health(b)
    assert "account_quota" not in _codes(h["warnings"])
    assert b.store.account_state("cl1", key=b._key("cl1"))["state"] == "quota"


# @pt.id KIT-HEALTH-DIAG-6
# @pt.item The health table prints the fixes
# @pt.condition the codex CLI is not on PATH and the default account is marked auth; health runs in a terminal
# @pt.expected under "To fix:" the view prints the install fix of the problem and the account-login fix of the warning
# @pt.target bridge.cli.main, bridge.render.health_view
# @pt.method integration
# @pt.legacy none
def test_cli_prints_fixes(make_bridge, repo, capsys, monkeypatch):
    monkeypatch.setenv("BRIDGE_PROJECT_ROOT", str(repo))
    b = make_bridge()
    b.store.set_account_state("default", "auth", "token expired", actor=Actor.BRIDGE,
                              key=b._key("default"))
    monkeypatch.setenv("PATH", PATH_WITHOUT_FAKES)
    h = _health(b)
    assert "account_auth" in _codes(h["warnings"])
    capsys.readouterr()
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit) as failed:
        cli.main(["health"])
    assert failed.value.code == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "NOT OK" in out and "To fix:" in out
    fixes = out.split("To fix:", 1)[1]
    assert "install the codex CLI" in fixes
    assert "agent-bridge account-login default" in fixes


# @pt.id KIT-CFG-LEGACY-001
# @pt.item A legacy worktree warns when a sibling worktree has an orchestrator.yaml
# @pt.condition the main worktree has .ai/orchestrator.yaml; a linked worktree has none
# @pt.expected health of the linked worktree lists legacy_config naming the main worktree; ok is unchanged
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy the legacy fallback ran silently with a different configuration
def test_legacy_worktree_warns_about_sibling_config(repo, fake_codex, tmp_path):
    from bridge.config import BridgeConfig
    from bridge.service import Bridge
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "orchestrator.yaml").write_text("version: 1\n")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", str(linked), "-b", "other")
    cfg = BridgeConfig(retry_backoff_s=0, lock_dir=str(fake_codex / "locks"))
    b = Bridge(linked.resolve(), cfg)
    assert b.legacy
    h = _health(b)
    warning, = [e for e in h["warnings"] if e["code"] == "legacy_config"]
    assert str(repo) in warning["message"] and "copy" not in warning["message"]
    assert ".ai/orchestrator.yaml" in warning["fix"] and str(repo) in warning["fix"]
    assert "legacy_config" not in _codes(h["problems"])
    assert h["ok"] is True


# @pt.id KIT-CFG-LEGACY-002
# @pt.item A legacy repository without other worktrees gets no legacy_config warning
# @pt.condition legacy setup, a single worktree
# @pt.expected warnings list no legacy_config
# @pt.target bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none
def test_legacy_alone_has_no_legacy_config_warning(make_bridge):
    b = make_bridge()
    assert b.legacy
    assert "legacy_config" not in _codes(_health(b)["warnings"])


# @pt.id KIT-HEALTH-DIAG-TEST04
# @pt.item A CLI newer than the probed version is a health warning
# @pt.condition the codex adapter reports 0.163.0 (probed), then 0.200.1 and a non-semver string
# @pt.expected only 0.200.1 adds cli_unprobed with a re-probe fix; ok is unaffected
# @pt.target bridge.service.Bridge.health, Bridge.unprobed
# @pt.method integration
# @pt.legacy audit TEST-04: sandbox claims were never re-checked when the CLI changed
def test_unprobed_cli_is_warning(make_bridge, monkeypatch):
    from bridge.executor import Adapter
    b = make_bridge()
    for version, warned in (("codex-cli 0.163.0", False), ("codex-cli 0.200.1", True),
                            ("codex-cli 0.0.0-fake", False), ("error: gone", False)):
        monkeypatch.setattr(Adapter, "version", lambda self, v=version: v)
        h = _health(b)
        assert ("cli_unprobed" in _codes(h["warnings"])) is warned, version
        assert h["ok"] is True
