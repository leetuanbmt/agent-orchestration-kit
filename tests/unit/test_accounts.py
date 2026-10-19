"""Account isolation, discovery, and human-only login tests."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from bridge import accounts, cli, server
from bridge.accounts import Account, account_env, codex_overrides, discover, resolve
from bridge.config import BridgeConfig
from bridge.service import Bridge


# @pt.id KIT-ACCT-001-config-validation
# @pt.item Account configuration validation
# @pt.condition Invalid names, reserved default, unknown default, and extra account fields
# @pt.expected Invalid configurations are rejected and a configured default is accepted
# @pt.target bridge.config.BridgeConfig
# @pt.method unit
# @pt.legacy none (new kit)
def test_config_validation():
    for name in ("default", "", "UPPER", "-first", "a.b", "a" * 33, "a\n"):
        with pytest.raises(ValidationError):
            BridgeConfig(accounts={name: {"codex_home": "/isolated"}})
    with pytest.raises(ValidationError):
        BridgeConfig(default_account="missing")
    with pytest.raises(ValidationError):
        BridgeConfig(accounts={"work": {"codex_home": "/isolated", "extra": True}})
    cfg = BridgeConfig(accounts={"work-1_ok": {"codex_home": "/isolated"}},
                       default_account="work-1_ok")
    assert cfg.default_account == "work-1_ok"


# @pt.id KIT-ACCT-002-resolution
# @pt.item Account path isolation
# @pt.condition Default, configured, unknown, relative, project, and global paths are resolved
# @pt.expected Only isolated absolute homes pass and no directory is created
# @pt.target bridge.accounts.resolve
# @pt.method unit
# @pt.legacy none (new kit)
def test_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "project"
    home = tmp_path / "isolated"
    cfg = BridgeConfig(accounts={"work": {"codex_home": "~/isolated"}}, default_account="work")
    assert resolve(cfg, root, "default") == Account("default", None)
    assert resolve(cfg, root, None) == Account("work", home.resolve())
    assert resolve(BridgeConfig(), root, None) == Account("default", None)
    assert not home.exists()
    with pytest.raises(ValueError):
        resolve(cfg, root, "missing")
    for bad in ("relative", str(root), str(root / "nested"), str(tmp_path / ".codex"),
                str(tmp_path / ".codex-accounts"), str(tmp_path / ".codex-accounts" / "x")):
        with pytest.raises(ValueError):
            resolve(BridgeConfig(accounts={"work": {"codex_home": bad}}), root, "work")
    root.mkdir()
    (tmp_path / "link").symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve(BridgeConfig(accounts={"work": {"codex_home": str(tmp_path / "link")}}),
                root, "work")


# @pt.id KIT-ACCT-003-discovery
# @pt.item Credential-free account and profile discovery
# @pt.condition One named home is logged in and profile files contain secret markers
# @pt.expected Sorted account states and profile names are returned without profile contents
# @pt.target bridge.accounts.discover
# @pt.method unit
# @pt.legacy none (new kit)
def test_discovery(tmp_path, monkeypatch, fake_codex):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    logged = tmp_path / "logged"
    logged.mkdir()
    (logged / "auth.json").write_text("{}")
    profiles = tmp_path / ".codex-accounts"
    profiles.mkdir()
    for name in ("zeta", "alpha", ".hidden"):
        (profiles / f"{name}.json").write_text("SECRET-REFRESH")
    cfg = BridgeConfig(accounts={"z": {"codex_home": str(tmp_path / "empty")},
                                 "a": {"codex_home": str(logged)}}, default_account="a")
    result = discover(cfg, tmp_path / "project")
    assert result["default_account"] == "a"
    assert [a["name"] for a in result["accounts"]] == ["default", "a", "z"]
    assert [a["logged_in"] for a in result["accounts"]] == [True, True, False]
    assert [a["isolated"] for a in result["accounts"]] == [False, True, True]
    assert result["accounts"][0]["codex_home"] is None
    assert result["accounts"][1]["codex_home"] == str(logged.resolve())
    assert all(a["quota"] == "UNKNOWN" for a in result["accounts"])
    assert result["codex_switch"]["installed"] is True
    assert [p["name"] for p in result["codex_switch"]["profiles"]] == ["alpha", "zeta"]
    assert all(p["usable"] is False for p in result["codex_switch"]["profiles"])
    assert "SECRET-REFRESH" not in json.dumps(result)
    monkeypatch.setenv("PATH", str(tmp_path / "no-binaries"))
    assert accounts.codex_switch_profiles()["installed"] is False


# @pt.id KIT-ACCT-004-environment
# @pt.item Inherited and isolated account environments
# @pt.condition Environment and overrides are requested for default and named accounts
# @pt.expected Default inherits CODEX_HOME and named accounts force file storage without mutation
# @pt.target bridge.accounts.account_env, bridge.accounts.codex_overrides
# @pt.method unit
# @pt.legacy none (new kit)
def test_environment(tmp_path):
    base = {"CODEX_HOME": "/inherited", "OTHER": "value"}
    default = Account("default", None)
    named = Account("work", tmp_path)
    assert account_env(default, base) == base
    assert account_env(default, base) is not base
    assert account_env(named, base) == {"CODEX_HOME": str(tmp_path), "OTHER": "value"}
    assert base["CODEX_HOME"] == "/inherited"
    assert codex_overrides(default) == []
    assert codex_overrides(named) == ["-c", 'cli_auth_credentials_store="file"']


# @pt.id KIT-ACCT-005-noninteractive-login
# @pt.item Human-only login gate
# @pt.condition CLI login runs with stdin DEVNULL
# @pt.expected Exit 2 mentions interactive terminal and creates no account home
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none (new kit)
def test_noninteractive_login(tmp_path):
    home = tmp_path / "unused"
    script = (
        "from pathlib import Path; from bridge import cli; "
        "from bridge.config import BridgeConfig; "
        f"cli.find_root = lambda: Path({str(tmp_path)!r}); "
        f"cli.load_config = lambda root: BridgeConfig(accounts={ {'work': {'codex_home': str(home)}}!r}); "
        "cli.main(['account-login', 'work'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path), "CODEX_HOME": str(home)},
    )
    assert result.returncode == 2
    assert "interactive terminal" in result.stderr
    assert not home.exists()


# @pt.id KIT-ACCT-006-status-unavailable
# @pt.item Unavailable login status
# @pt.condition Codex binary is missing or times out
# @pt.expected Status is unknown and subprocess receives the isolated environment
# @pt.target bridge.accounts.login_status
# @pt.method unit
# @pt.legacy none (new kit)
def test_status_unavailable(tmp_path, monkeypatch):
    acc = Account("work", tmp_path)
    for error in (FileNotFoundError(), PermissionError(), OSError(),
                  subprocess.TimeoutExpired("codex", 20)):
        runner = Mock(side_effect=error)
        monkeypatch.setattr(accounts.subprocess, "run", runner)
        assert accounts.login_status(BridgeConfig(), acc) is None
        assert runner.call_args.kwargs["timeout"] == 20
        assert runner.call_args.kwargs["env"]["CODEX_HOME"] == str(tmp_path)


# @pt.id KIT-ACCT-007-login-guard
# @pt.item Isolated interactive login and global auth guard
# @pt.condition Human login succeeds or changes the temporary global auth fingerprint
# @pt.expected Correct argv and private home are used; global changes cause exit 3
# @pt.target bridge.cli._run, bridge.accounts.global_auth_fingerprint
# @pt.method unit
# @pt.legacy none (new kit)
def test_login_guard(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "project"
    home = tmp_path / "isolated"
    cfg = BridgeConfig(accounts={"work": {"codex_home": str(home)}})
    monkeypatch.setattr(cli, "find_root", lambda: root)
    monkeypatch.setattr(cli, "load_config", lambda root: cfg)
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: True))
    monkeypatch.setattr(cli.sys, "stdout", Mock(isatty=lambda: True))
    monkeypatch.setattr(cli, "login_status", lambda cfg, acc: True)
    runner = Mock()
    monkeypatch.setattr(cli.subprocess, "run", runner)
    args = cli.argparse.Namespace(cmd="account-login", name="work", device_auth=True)
    assert asyncio.run(cli._run(args)) is True
    assert home.stat().st_mode & 0o777 == 0o700
    assert runner.call_args.args[0] == ["codex", "login", "-c",
                                       'cli_auth_credentials_store="file"', "--device-auth"]
    assert runner.call_args.kwargs["env"]["CODEX_HOME"] == str(home.resolve())
    assert "stdin" not in runner.call_args.kwargs
    assert accounts.global_auth_fingerprint() is None
    global_auth = tmp_path / ".codex" / "auth.json"
    global_auth.parent.mkdir()
    global_auth.write_text("synthetic-before")
    before = accounts.global_auth_fingerprint()
    runner.side_effect = lambda *a, **kw: global_auth.write_text("synthetic-after")
    with pytest.raises(SystemExit) as error:
        asyncio.run(cli._run(args))
    assert error.value.code == 3
    assert "ERROR" in capsys.readouterr().err
    assert accounts.global_auth_fingerprint() != before
    args.name = "default"
    with pytest.raises(ValueError, match="configured named account"):
        asyncio.run(cli._run(args))


# @pt.id KIT-ACCT-008-list-surfaces
# @pt.item CLI and MCP account listing
# @pt.condition Account-list is invoked through either interface
# @pt.expected Discovery includes state/last_error and MCP validation errors use the safe wrapper
# @pt.target bridge.cli._run, bridge.server.bridge_list_accounts
# @pt.method unit
# @pt.legacy none (new kit)
def test_list_surfaces(tmp_path, monkeypatch):
    cfg = BridgeConfig(codex_bin=str(tmp_path / "missing"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "find_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda root: cfg)
    b = Bridge(tmp_path, cfg)
    monkeypatch.setattr(server, "bridge", lambda: b)
    expected = discover(cfg, tmp_path)
    for account in expected["accounts"]:
        account.update(state="available", last_error=None)
    assert asyncio.run(cli._run(cli.argparse.Namespace(cmd="account-list"))) == expected
    assert asyncio.run(server.bridge_list_accounts()) == expected
    monkeypatch.setattr(b, "list_accounts", Mock(side_effect=ValueError("bad account")))
    with pytest.raises(server.ToolError, match="bad account"):
        asyncio.run(server.bridge_list_accounts())
