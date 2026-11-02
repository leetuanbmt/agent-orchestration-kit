"""Machine-wide account marks and reported quota snapshots."""

import hashlib
import stat

import pytest

from bridge import storage
from bridge.accounts import Account, AccountLock, account_key
from bridge.config import BridgeConfig
from bridge.executor import RunResult
from bridge.models import Actor
from bridge.service import Bridge
from tests.conftest import git


@pytest.fixture
def bridges(repo, tmp_path, fake_codex):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    home = tmp_path / "account"
    home.mkdir()
    cfg = BridgeConfig(accounts={"work": {"codex_home": str(home)}},
                       default_account="work",
                       auto_switch={"fallback_accounts": ["default"]})
    return Bridge(repo, cfg), Bridge(other, cfg)


# @pt.id KIT-SHARED-001-marks
# @pt.item Account availability is machine-wide
# @pt.condition Two projects use the same account identity
# @pt.expected Quota and auth marks block both projects until cleared in either project
# @pt.target Bridge._record_account, Bridge._unavailable, Bridge.clear_account
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("outcome", ["quota", "auth"])
def test_shared_marks(bridges, outcome):
    a, b = bridges
    a._record_account("work", RunResult(outcome=outcome, error="unavailable"))
    assert b._unavailable("work", "write") == f"account work {outcome}"
    assert b._pick("executor", "write") == "default"
    assert b.clear_account("work")["state"] == "available"
    assert a._unavailable("work", "write") is None
    assert a._pick("executor", "write") == "work"
    assert a.store.account_state("work")["state"] == outcome
    assert stat.S_IMODE((storage.home() / "accounts.db").stat().st_mode) == 0o600


# @pt.id KIT-SHARED-002-quota
# @pt.item Reported quota is machine-wide for non-openai providers
# @pt.condition Two projects report on the same provider identity
# @pt.expected The second project returns the first project's snapshot
# @pt.target Bridge._account_quota, Bridge._record_account
# @pt.method integration
# @pt.legacy none
def test_shared_quota(bridges, monkeypatch):
    a, b = bridges
    account = Account("work", None, "opencode")
    for bridge in bridges:
        monkeypatch.setattr(bridge, "_account", lambda name: account)
    limits = {"primary": {"used_percent": 25, "resets_at": 9999999999}}
    a._record_account("work", RunResult(outcome="ok", rate_limits=limits))
    assert b._account_quota("work")["remaining_pct"] == 75
    assert b._account_quota("work")["primary"] == limits["primary"]


# @pt.id KIT-SHARED-005-unknown-account
# @pt.item Unresolved account names retain local persistence
# @pt.condition A run reports quota for an unknown or empty account name
# @pt.expected Recording succeeds locally without sharing the mark or snapshot
# @pt.target Bridge._key, Bridge._record_account
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("name", ["unknown", ""])
def test_record_unknown_account(bridges, name):
    a, b = bridges
    limits = {"primary": {"used_percent": 100}}
    a._record_account(name, RunResult(outcome="quota", error="exhausted", rate_limits=limits))
    assert a._key(name) is None
    assert a.store.account_state(name)["state"] == "quota"
    assert a.store.quota(name)["rate_limits"] == limits
    assert b.store.account_state(name)["state"] == "available"
    assert b.store.quota(name) is None


# @pt.id KIT-SHARED-003-identities
# @pt.item Legacy names do not identify machine-wide accounts
# @pt.condition Two projects have identically named accounts with different homes
# @pt.expected Marks and snapshots do not leak between the identities
# @pt.target Store.account_state, Store.quota, account_key
# @pt.method integration
# @pt.legacy none
def test_distinct_homes(bridges, tmp_path):
    a, old_b = bridges
    cfg = BridgeConfig(accounts={"work": {"codex_home": str(tmp_path / "different")}})
    b = Bridge(old_b.root, cfg)
    key = account_key(a._account("work"))
    a.store.set_account_state("work", "quota", actor=Actor.BRIDGE, key=key)
    a.store.save_quota("work", {"primary": {"used_percent": 100}}, key=key)
    assert b._unavailable("work", "write") is None
    assert b._account_quota("work") == "UNKNOWN"


# @pt.id KIT-SHARED-004-locks
# @pt.item AccountLock retains its original filenames
# @pt.condition OpenAI and other providers have explicit or inherited homes
# @pt.expected Account keys and lock filenames match the original seed algorithm
# @pt.target account_key, AccountLock
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("provider", ["openai", "anthropic", "opencode"])
@pytest.mark.parametrize("explicit", [False, True])
def test_lock_names(tmp_path, provider, explicit):
    home = tmp_path / "account" if explicit else None
    account = Account("work", home, provider)
    seed = str(home) if home else "default"
    if provider != "openai":
        seed = f"{provider}:{seed}"
    assert account_key(account) == seed
    digest = hashlib.sha256(seed.encode()).hexdigest()[:16]
    lock = AccountLock(account, str(tmp_path / "locks"), slots=2)
    assert [p.name for p in lock.paths] == [
        f"agent-bridge-acct-{digest}.lock", f"agent-bridge-acct-{digest}-1.lock"]
