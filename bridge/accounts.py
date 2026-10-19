"""Isolated account resolution and credential-free discovery."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import BridgeConfig
from .state import ConflictError


@dataclass(frozen=True)
class Account:
    name: str
    codex_home: Path | None


def resolve(cfg: BridgeConfig, root: Path, name: str | None) -> Account:
    name = cfg.default_account if name is None else name
    if name == "default":
        return Account(name, None)
    if name not in cfg.accounts:
        raise ValueError(f"unknown account: {name}")
    home = Path(cfg.accounts[name].codex_home).expanduser()
    if not home.is_absolute():
        raise ValueError("codex_home must be absolute")
    home = home.resolve()
    snapshots = (Path.home() / ".codex-accounts").resolve()
    isolated_home = home.parent == snapshots / "homes"
    if (home.is_relative_to(root.resolve()) or home == (Path.home() / ".codex").resolve()
            or (home.is_relative_to(snapshots) and not isolated_home)):
        raise ValueError("codex_home must be isolated from the project and global accounts")
    return Account(name, home)


def account_env(acc: Account, base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    if acc.codex_home is not None:
        env["CODEX_HOME"] = str(acc.codex_home)
    return env


def codex_overrides(acc: Account) -> list[str]:
    return ["-c", 'cli_auth_credentials_store="file"'] if acc.codex_home is not None else []


def login_status(cfg: BridgeConfig, acc: Account) -> bool | None:
    try:
        result = subprocess.run(
            [cfg.codex_bin, "login", "status", *codex_overrides(acc)],
            env=account_env(acc, os.environ), timeout=20, capture_output=True, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode == 0


def global_auth_fingerprint() -> str | None:
    try:
        return hashlib.sha256((Path.home() / ".codex" / "auth.json").read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def codex_switch_profiles() -> dict:
    return {
        "installed": shutil.which("codex-switch") is not None,
        "profiles": sorted(p.stem for p in (Path.home() / ".codex-accounts").glob("*.json")
                           if not p.name.startswith(".")),
    }


def discover(cfg: BridgeConfig, root: Path) -> dict:
    accounts = []
    for name in ["default", *sorted(cfg.accounts)]:
        acc = resolve(cfg, root, name)
        accounts.append({
            "name": name, "codex_home": str(acc.codex_home) if acc.codex_home else None,
            "isolated": acc.codex_home is not None, "logged_in": login_status(cfg, acc),
            "quota": "UNKNOWN",
        })
    switch = codex_switch_profiles()
    homes = []
    homes_error = None
    if switch["installed"]:
        try:
            result = subprocess.run(
                ["codex-switch", "home", "list", "--json"], timeout=20,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                homes_error = f"home list exited {result.returncode}"
            else:
                rows = json.loads(result.stdout)
                if not isinstance(rows, list) or any(
                    not isinstance(row, dict)
                    or not isinstance(row.get("name"), str)
                    or not isinstance(row.get("codex_home"), str)
                    or not isinstance(row.get("logged_in"), bool)
                    for row in rows
                ):
                    raise ValueError("invalid homes")
                configured = {a["codex_home"] for a in accounts if a["isolated"]}
                homes = [{
                    "name": row["name"], "codex_home": row["codex_home"],
                    "logged_in": row["logged_in"],
                    "configured": str(Path(row["codex_home"]).expanduser().resolve()) in configured,
                    "config_snippet": {"accounts": {
                        row["name"]: {"codex_home": row["codex_home"]}}},
                } for row in rows]
        except subprocess.TimeoutExpired:
            homes_error = "home list timed out"
        except OSError:
            homes_error = "home list unavailable"
        except (ValueError, UnicodeError):
            homes_error = "invalid home list JSON"
    return {
        "default_account": cfg.default_account, "accounts": accounts,
        "codex_switch": {
            "installed": switch["installed"],
            "homes": homes,
            **({"homes_error": homes_error} if homes_error else {}),
            "profiles": [{"name": name, "usable": False, "hint":
                          "not isolated; add it to .ai/config.json accounts with its own "
                          "codex_home, then a human runs: codex-bridge account-login <name>"}
                         for name in switch["profiles"]],
        },
    }


class AccountLock:
    def __init__(self, acc: Account, lock_dir: str | None):
        self.acc = acc
        key = hashlib.sha256((str(acc.codex_home) if acc.codex_home else "default").encode())
        self.path = Path(lock_dir or tempfile.gettempdir()) / (
            f"codex-bridge-acct-{key.hexdigest()[:16]}.lock")
        homes = [Path.home() / ".codex"]
        if acc.codex_home is not None:
            homes.append(acc.codex_home)
        if os.environ.get("CODEX_HOME"):
            homes.append(Path(os.environ["CODEX_HOME"]))
        if any(self.path.resolve().is_relative_to(home.resolve()) for home in homes):
            raise ValueError("lock_dir must be outside CODEX_HOME and ~/.codex")
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            handle.close()
            raise ConflictError(
                f"account {self.acc.name} is busy in another bridge process") from e
        except BaseException:
            handle.close()
            raise
        self._file = handle

    def release(self) -> None:
        if self._file is not None:
            # Keep the inode: unlinking would let waiters lock different files for one account.
            self._file.close()
            self._file = None
