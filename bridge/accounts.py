"""Isolated account resolution and credential-free discovery, per provider."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import BridgeConfig
from .state import ConflictError

# Env var that points each provider's CLI at an isolated auth home. agy has no such variable
# and keeps its login under $HOME/.gemini, so its isolated home is a whole HOME (probed on
# agy 1.4.5). opencode has none at all: its only account is the global login.
HOME_ENV = {"openai": "CODEX_HOME", "anthropic": "CLAUDE_CONFIG_DIR", "google": "HOME"}
GLOBAL_HOME = {"openai": ".codex", "anthropic": ".claude", "google": ".gemini"}
# Machine-wide accounts created by `agent-bridge account-add`, usable from every project.
REGISTRY_DIR = ".agent-bridge"
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class Account:
    name: str
    home: Path | None  # None = the provider's inherited global login, used read-only
    provider: str = "openai"

    @property
    def codex_home(self) -> Path | None:
        return self.home


def resolve_profile(name: str, provider: str, home: str | None, root: Path | None) -> Account:
    """`root` is the project the account is used from; None when registering it machine-wide."""
    if home is None:
        return Account(name, None, provider)
    path = Path(home).expanduser()
    if not path.is_absolute():
        raise ValueError(f"account {name}: home must be absolute")
    path = path.resolve()
    global_home = (Path.home() / GLOBAL_HOME[provider]).resolve()
    snapshots = (Path.home() / ".codex-accounts").resolve()
    shared = (root is not None and path.is_relative_to(root.resolve())) or path == global_home
    shared |= path == Path.home().resolve()
    if provider == "openai":
        # codex-switch snapshot profiles hold copied tokens; only its fresh-login homes count.
        shared |= path.is_relative_to(snapshots) and path.parent != snapshots / "homes"
    else:
        shared |= path.is_relative_to(global_home)
    if shared:
        raise ValueError(f"account {name}: home must be isolated from the project and the "
                         "global login")
    return Account(name, path, provider)


def resolve(cfg: BridgeConfig, root: Path, name: str | None) -> Account:
    """Legacy `.ai/config.json` accounts: Codex homes keyed by name, plus `default`."""
    name = cfg.default_account if name is None else name
    if name == "default":
        return Account(name, None)
    if name not in cfg.accounts:
        raise ValueError(f"unknown account: {name}")
    try:
        return resolve_profile(name, "openai", cfg.accounts[name].codex_home, root)
    except ValueError as e:
        raise ValueError("codex_home must be isolated from the project and global accounts"
                         if "isolated" in str(e) else "codex_home must be absolute") from e


def account_env(acc: Account, base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    if acc.home is not None:
        env[HOME_ENV[acc.provider]] = str(acc.home)
    return env


def codex_overrides(acc: Account) -> list[str]:
    # File credential store keeps an isolated Codex home off the shared OS keychain entry.
    if acc.provider != "openai" or acc.home is None:
        return []
    return ["-c", 'cli_auth_credentials_store="file"']


def binary(cfg: BridgeConfig, provider: str) -> str:
    return {"openai": cfg.codex_bin, "anthropic": cfg.claude_bin, "google": cfg.agy_bin,
            "opencode": cfg.opencode_bin}[provider]


def login_status(cfg: BridgeConfig, acc: Account) -> bool | None:
    exe = binary(cfg, acc.provider)
    argv = {"openai": [exe, "login", "status", *codex_overrides(acc)],
            "anthropic": [exe, "auth", "status"],
            # agy has no status command; listing models needs a signed-in account.
            "google": [exe, "models"],
            "opencode": [exe, "auth", "list"]}[acc.provider]
    try:
        result = subprocess.run(argv, env=account_env(acc, os.environ), timeout=20,
                                capture_output=True, text=True, check=False,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if acc.provider == "anthropic":
        try:
            return bool(json.loads(result.stdout).get("loggedIn"))
        except (ValueError, AttributeError):
            return False
    if acc.provider == "opencode":
        return result.returncode == 0 and "stored" in result.stdout
    return result.returncode == 0


def login_argv(cfg: BridgeConfig, acc: Account, device_auth: bool = False) -> list[str]:
    """The interactive login a human runs for this account (inside its home, if it has one)."""
    exe = binary(cfg, acc.provider)
    return {"openai": [exe, "login", *codex_overrides(acc),
                       *(["--device-auth"] if device_auth else [])],
            "anthropic": [exe, "auth", "login"],
            # agy signs in on its first interactive start; the human exits it afterwards.
            "google": [exe],
            "opencode": [exe, "auth", "login"]}[acc.provider]


def global_login_marker(cfg: BridgeConfig, provider: str) -> object:
    """Something that changes when the provider's global login changes, read without tokens."""
    if provider == "openai":
        return global_auth_fingerprint()
    if provider == "anthropic":
        return global_claude_identity(cfg)
    if provider == "google":
        token = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        with contextlib.suppress(OSError):
            st = token.stat()
            return st.st_mtime_ns, st.st_size
    return None


# ---- machine-wide registry --------------------------------------------------------------------


def registry_dir() -> Path:
    return Path.home() / REGISTRY_DIR


def load_registry() -> dict[str, dict[str, str | None]]:
    path = registry_dir() / "accounts.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object")
    return data


def _save_registry(data: dict[str, dict[str, str | None]]) -> None:
    base = registry_dir()
    base.mkdir(mode=0o700, exist_ok=True)
    tmp = base / "accounts.json.tmp"
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, base / "accounts.json")


def register(name: str, provider: str, home: str | None = None) -> Account:
    """Record a machine-wide account; without `home`, create a fresh one under the registry."""
    if name == "default" or not _NAME.fullmatch(name):
        raise ValueError(f"invalid or reserved account name: {name!r} (use [a-z0-9_-])")
    if provider not in HOME_ENV and provider != "opencode":
        raise ValueError(f"unknown provider {provider!r}")
    if provider not in HOME_ENV:
        raise ValueError(f"{provider} has no isolated accounts; its global login is used "
                         "(opencode auth login)")
    data = load_registry()
    if name in data:
        raise ValueError(f"account {name} already exists")
    if home is None:
        path = registry_dir() / "homes" / name
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        if provider == "openai":
            # Interactive `codex` started with this CODEX_HOME must find the file-stored login.
            (path / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        home = str(path)
    acc = resolve_profile(name, provider, home, None)
    for other, entry in data.items():
        if entry.get("home") and Path(entry["home"]).resolve() == acc.home:
            raise ValueError(f"{acc.home} is already the home of account {other}")
    data[name] = {"provider": provider, "home": str(acc.home)}
    _save_registry(data)
    return acc


def unregister(name: str) -> bool:
    """Forget a registry account; delete its home only when the registry created it."""
    data = load_registry()
    entry = data.pop(name, None)
    if entry is None:
        raise ValueError(f"no registry account {name!r}")
    _save_registry(data)
    home = Path(entry["home"]) if entry.get("home") else None
    owned = home is not None and home.parent == registry_dir() / "homes"
    if owned and home.is_dir() and not home.is_symlink():
        shutil.rmtree(home)
    return owned


def shell_use(acc: Account) -> str:
    """Shell code that points interactive CLIs at this account (`eval "$(...)"`)."""
    var = HOME_ENV.get(acc.provider)
    if acc.provider == "google":
        if acc.home is None:
            return "unset -f agy 2>/dev/null; true"
        return f"agy() {{ HOME={shlex.quote(str(acc.home))} command agy \"$@\"; }}"
    if var is None:
        return "true  # opencode always uses its global login"
    if acc.home is None:
        return f"unset {var}"
    return f"export {var}={shlex.quote(str(acc.home))}"


def claude_switch_profiles() -> dict:
    """claude-switch profiles hold copied tokens: listed so the user knows they exist, never used."""
    if shutil.which("claude-switch") is None:
        return {"installed": False, "profiles": []}
    try:
        out = subprocess.run(["claude-switch", "list"], capture_output=True, text=True,
                             timeout=20, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return {"installed": True, "profiles": []}
    profiles = []
    for line in out.stdout.splitlines():
        parts = line.lstrip("* ").split()
        if parts and _NAME.fullmatch(parts[0]):
            profiles.append({"name": parts[0], "email": parts[1] if len(parts) > 1 else None})
    return {"installed": True, "profiles": profiles}


def global_auth_fingerprint() -> str | None:
    try:
        return hashlib.sha256((Path.home() / ".codex" / "auth.json").read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def global_claude_identity(cfg: BridgeConfig) -> str | None:
    """Hash of who the global Claude login is, to detect a login that escaped its own home."""
    env = {k: v for k, v in os.environ.items() if k != HOME_ENV["anthropic"]}
    try:
        out = subprocess.run([cfg.claude_bin, "auth", "status"], env=env, timeout=20,
                             capture_output=True, text=True, check=False,
                             stdin=subprocess.DEVNULL)
        data = json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    who = [data.get(k) for k in ("loggedIn", "authMethod", "email", "orgId", "subscriptionType")]
    return hashlib.sha256(json.dumps(who).encode()).hexdigest()


def codex_switch_profiles() -> dict:
    return {
        "installed": shutil.which("codex-switch") is not None,
        "profiles": sorted(p.stem for p in (Path.home() / ".codex-accounts").glob("*.json")
                           if not p.name.startswith(".")),
    }


def discover(cfg: BridgeConfig, root: Path, profiles: list[Account] | None = None) -> dict:
    if profiles is None:
        profiles = [resolve(cfg, root, name) for name in ["default", *sorted(cfg.accounts)]]
    accounts = []
    for acc in profiles:
        accounts.append({
            "name": acc.name, "provider": acc.provider,
            "home": str(acc.home) if acc.home else None,
            "codex_home": str(acc.home) if acc.home and acc.provider == "openai" else None,
            "isolated": acc.home is not None, "logged_in": login_status(cfg, acc),
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
                configured = {a["codex_home"] for a in accounts if a["codex_home"]}
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
        "claude_switch": claude_switch_profiles(),
        "codex_switch": {
            "installed": switch["installed"],
            "homes": homes,
            **({"homes_error": homes_error} if homes_error else {}),
            "profiles": [{"name": name, "usable": False, "hint":
                          "not isolated; add it as an account with its own home, then a "
                          "human runs: agent-bridge account-login <name>"}
                         for name in switch["profiles"]],
        },
    }


class AccountLock:
    """Cross-process slot lock: an account runs at most `slots` agent processes at once."""

    def __init__(self, acc: Account, lock_dir: str | None, slots: int = 1):
        self.acc = acc
        seed = str(acc.home) if acc.home else "default"
        if acc.provider != "openai":  # openai keeps the original lock names
            seed = f"{acc.provider}:{seed}"
        key = hashlib.sha256(seed.encode()).hexdigest()[:16]
        base = Path(lock_dir or tempfile.gettempdir())
        self.paths = [base / (f"agent-bridge-acct-{key}.lock" if i == 0 else
                              f"agent-bridge-acct-{key}-{i}.lock") for i in range(slots)]
        self.path = self.paths[0]
        homes = [Path.home() / home for home in GLOBAL_HOME.values()]
        if acc.home is not None:
            homes.append(acc.home)
        homes += [Path(os.environ[v]) for v in HOME_ENV.values()
                  if v != "HOME" and os.environ.get(v)]
        if any(self.path.resolve().is_relative_to(home.resolve()) for home in homes):
            raise ValueError("lock_dir must be outside the account homes")
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for path in self.paths:
            handle = path.open("a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            except BaseException:
                handle.close()
                raise
            self._file = handle
            return
        raise ConflictError(f"account {self.acc.name} is busy in another bridge process")

    def release(self) -> None:
        if self._file is not None:
            # Keep the inode: unlinking would let waiters lock different files for one account.
            self._file.close()
            self._file = None
