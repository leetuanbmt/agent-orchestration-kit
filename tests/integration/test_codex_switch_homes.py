"""Isolated codex-switch homes are discovered without reading their contents."""

import json

import pytest

from bridge.accounts import discover, resolve
from bridge.config import BridgeConfig


# @pt.id KIT-INTEG-001-home-resolution
# @pt.item Isolated home path boundary
# @pt.condition Fake HOME contains isolated homes and snapshot paths
# @pt.expected Only a direct child of homes is accepted
# @pt.target bridge.accounts.resolve
# @pt.method integration
# @pt.legacy none (new kit)
def test_home_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    base = tmp_path / ".codex-accounts"
    home = base / "homes" / "work"
    cfg = BridgeConfig(accounts={"work": {"codex_home": str(home)}})
    assert resolve(cfg, tmp_path / "project", "work").codex_home == home
    for path in (base, base / "x.json", base / "homes", home / "nested"):
        cfg = BridgeConfig(accounts={"work": {"codex_home": str(path)}})
        with pytest.raises(ValueError):
            resolve(cfg, tmp_path / "project", "work")
    assert not base.exists()


# @pt.id KIT-INTEG-002-home-discovery
# @pt.item Home discovery and old-version fallback
# @pt.condition Fake switch returns two homes or fails or emits invalid JSON
# @pt.expected Configuration snippets and configured flags match; failures return a short error
# @pt.target bridge.accounts.discover
# @pt.method integration
# @pt.legacy none (new kit)
@pytest.mark.parametrize("failure", [None, "exit", "json", "schema"])
def test_home_discovery(tmp_path, monkeypatch, fake_codex, failure):
    monkeypatch.setenv("HOME", str(tmp_path))
    rows = [{"name": name, "codex_home": str(tmp_path / ".codex-accounts" / "homes" / name),
             "logged_in": logged} for name, logged in (("work", True), ("other", False))]
    monkeypatch.setenv("FAKE_CODEX_SWITCH_HOMES", json.dumps(rows))
    monkeypatch.setenv("FAKE_CODEX_SWITCH_EXIT", "1" if failure == "exit" else "0")
    if failure in ("json", "schema"):
        monkeypatch.setenv("FAKE_CODEX_SWITCH_HOMES", "bad" if failure == "json" else "{}")
    cfg = BridgeConfig(accounts={"alias": {"codex_home": rows[0]["codex_home"]}})
    switch = discover(cfg, tmp_path / "project")["codex_switch"]
    if failure:
        assert switch["homes"] == []
        assert switch["homes_error"]
    else:
        assert switch["homes"] == [
            {**row, "configured": i == 0, "config_snippet": {
                "accounts": {row["name"]: {"codex_home": row["codex_home"]}}}}
            for i, row in enumerate(rows)
        ]
    assert switch["profiles"] == []
    assert not (tmp_path / ".codex-accounts").exists()


