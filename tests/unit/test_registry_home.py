"""The machine registry lives in the same bridge home as central storage."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from bridge import accounts, registry, storage


def _legacy_with_account(tmp_path: Path) -> tuple[Path, Path]:
    legacy = Path.home() / ".agent-bridge"
    (legacy / "homes" / "old").mkdir(parents=True)
    (legacy / "homes" / "old" / "auth.json").write_text("login")
    external = tmp_path / "external-login"
    external.mkdir()
    (legacy / "accounts.json").write_text(
        json.dumps({"old": {"provider": "openai", "home": str(external)}}))
    return legacy, external


# @pt.id KIT-CFG-REGHOME-001
# @pt.item Registry home default
# @pt.condition AGENT_BRIDGE_HOME is unset
# @pt.expected registry_dir is HOME/.agent-bridge
# @pt.target bridge.registry.registry_dir
# @pt.method unit
# @pt.legacy None
def test_default_registry_dir():
    assert registry.registry_dir() == Path.home() / ".agent-bridge"


# @pt.id KIT-CFG-REGHOME-002
# @pt.item Registry follows AGENT_BRIDGE_HOME
# @pt.condition AGENT_BRIDGE_HOME is set and an account is registered
# @pt.expected registry_dir and storage.home agree and the account home is under the new home
# @pt.target bridge.registry.registry_dir bridge.storage.home bridge.accounts.register
# @pt.method unit
# @pt.legacy None
def test_registry_follows_env(tmp_path, monkeypatch):
    bridge = tmp_path / "bh"
    bridge.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(bridge))
    assert registry.registry_dir() == bridge
    assert storage.home() == bridge
    account = accounts.register("fresh", "openai")
    assert account.home == (bridge / "homes" / "fresh").resolve()
    assert (bridge / "accounts.json").is_file()
    assert not (Path.home() / ".agent-bridge" / "accounts.json").exists()


# @pt.id KIT-CFG-REGHOME-003
# @pt.item One-time copy-only adoption
# @pt.condition A legacy accounts.json exists and the new home has none
# @pt.expected It is copied once, legacy files stay, and an existing target is never overwritten
# @pt.target bridge.registry.adopt_legacy
# @pt.method unit
# @pt.legacy None
def test_adoption_copies_once(tmp_path, monkeypatch):
    legacy, external = _legacy_with_account(tmp_path)
    before = (legacy / "accounts.json").read_text()
    bridge = tmp_path / "bh"
    bridge.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(bridge))
    assert accounts.load_registry()["old"]["home"] == str(external)
    copied = bridge / "accounts.json"
    assert copied.is_file()
    assert (copied.stat().st_mode & 0o777) == 0o600
    assert (legacy / "accounts.json").read_text() == before
    assert (legacy / "homes" / "old" / "auth.json").read_text() == "login"
    assert not (bridge / "homes").exists()
    copied.write_text("{}")
    assert accounts.load_registry() == {}
    other = tmp_path / "bh2"
    other.mkdir()
    (other / "accounts.json").write_text('{"keep": {"provider": "openai", "home": null}}')
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(other))
    assert set(accounts.load_registry()) == {"keep"}


# @pt.id KIT-CFG-REGHOME-004
# @pt.item Symlinked bridge home
# @pt.condition AGENT_BRIDGE_HOME is a symlink
# @pt.expected registry_dir raises ValueError
# @pt.target bridge.registry.registry_dir
# @pt.method unit
# @pt.legacy None
def test_symlinked_home_refused(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(link))
    with pytest.raises(ValueError):
        registry.registry_dir()


# @pt.id KIT-CFG-REGHOME-005
# @pt.item Concurrent account registration
# @pt.condition Two threads register different accounts together
# @pt.expected Both are in accounts.json and no temp file remains
# @pt.target bridge.accounts.register
# @pt.method unit
# @pt.legacy None
def test_concurrent_register(tmp_path):
    names = [f"acct{index}" for index in range(8)]
    barrier = Barrier(len(names))

    def add(name):
        barrier.wait(timeout=5)
        accounts.register(name, "openai")

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        list(pool.map(add, names))
    assert set(accounts.load_registry()) == set(names)
    assert not list(registry.registry_dir().glob("accounts.json.*tmp"))
