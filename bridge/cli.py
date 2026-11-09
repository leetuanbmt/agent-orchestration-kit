"""Command-line front for the same bridge service (`agent-bridge ...`).

Without a subcommand it opens the TUI for the project in the current directory. `approve` is
the human gate: it requires an interactive terminal and a typed confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import install, orchestrator
from .accounts import (KEYCHAIN_SERVICE, account_env, global_auth_fingerprint,
                       global_claude_identity, global_login_marker, keychain, keychain_account,
                       load_registry, login_argv, login_status,
                       pick_account, register, resolve_profile, shell_use, unregister)
from .config import AI_DIR, BridgeConfig, ensure_ai_dir, find_root, load_config
from .executor import AGENT_ENV
from .registry import registry_dir
from .service import Bridge, dumps, project_tasks, resolve_account


def _human_confirm(task_id: str) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("refused: human action needs an interactive terminal (human approval only)",
              file=sys.stderr)
        return False
    typed = input(f"Type the task id to confirm {task_id}: ").strip()
    return typed == task_id


def _server_entry() -> dict[str, Any]:
    """Call the installed tool by name when this process is it; otherwise go through uv."""
    on_path = shutil.which(install.NAME)
    if not install.CHECKOUT or (on_path and
                                Path(on_path).resolve() == Path(sys.argv[0]).resolve()):
        return install.tool_server()  # a wheel install has no checkout for `uv run --project`
    return install.uv_server()


# Commands a person reads: a table in a terminal, JSON with --json or through a pipe.
VIEWS = {"account-list": "accounts_view", "health": "health_view", "agents": "agents_view",
         "list": "tasks_view", "tasks": "projects_view", "usage": "usage_view",
         "config-check": "config_view", "config-migrate": "migrate_view",
         "project-account-add": "account_added_view", "config-set": "config_change_view",
         "config-unset": "config_change_view", "project-agent-add": "agent_change_view",
         "project-agent-remove": "agent_change_view"}
HUMAN_ACCOUNT_CMDS = {"account-login", "account-add", "account-remove", "account-set-token",
                      "project-account-add", "project-account-remove",
                      "project-agent-add", "project-agent-remove",
                      "project-account-limits", "config-set", "config-unset"}
# Adapter per provider for `project-account-add`; google and opencode only plan and review.
_PROVIDER_ADAPTER = {"openai": "codex-cli", "anthropic": "claude-cli", "google": "agy-cli",
                     "opencode": "opencode-cli"}
EXAMPLES = install.KIT / "examples"


def _example_files() -> list[Path]:
    """The orchestrator examples, sorted; each `<name>.accounts.yaml` is their companion."""
    return sorted(p for p in EXAMPLES.glob("*.yaml") if not p.name.endswith(".accounts.yaml"))


# Commands that drive agent runs: a stop signal lets them kill their agent and park the task.
RUN_CMDS = {"execute", "resume", "plan", "run-workflow"}
# A caller's timeout (SIGALRM from `alarm`, SIGTERM from `timeout`) or Ctrl-C; the default action
# would end the bridge at once and leave its agent's process group running with the task RUNNING.
STOP_SIGNALS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGALRM")
                     if hasattr(signal, name))
_signalled: list[int] = []


def _on_stop_signals(stop: Any) -> None:
    def handler(signum: int) -> None:
        _signalled.append(signum)
        stop()
    loop = asyncio.get_running_loop()
    for sig in STOP_SIGNALS:
        # ponytail: Windows event loops have no signal handlers; Ctrl-C keeps its default there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, handler, sig)


def _config_here() -> Any:
    try:
        return load_config(find_root())
    except RuntimeError:  # account-add works outside a project too
        return BridgeConfig()


def _pick(args: argparse.Namespace, known: dict[str, str]) -> str:
    """The account id `args.name` means; `--provider` settles a name shared by providers."""
    return pick_account(args.name, getattr(args, "provider", None), known)


def _login(cfg: Any, acc: Any, device_auth: bool, api_key: bool = False) -> Any:
    """Run the provider's own login inside the account's home; refuse if the global one moved."""
    key = None
    if api_key:
        if acc.provider != "openai" or acc.home is None:
            raise ValueError("--with-api-key is for an openai account with its own home")
        # Typed here and piped to codex: never on a command line, in the env or in a log.
        key = getpass.getpass("OpenAI API key (not echoed): ").strip()
        if not key.startswith("sk-"):
            raise ValueError("that is not an OpenAI API key (sk-...); nothing was changed")
    if acc.home is not None:
        acc.home.mkdir(mode=0o700, parents=True, exist_ok=True)
    markers = {"openai": lambda: global_auth_fingerprint(),
               "anthropic": lambda: global_claude_identity(cfg),
               "google": lambda: global_login_marker(cfg, "google")}
    marker = markers.get(acc.provider) if acc.home is not None else None
    if acc.provider == "google":
        print("agy: sign in in the window that opens, then quit it (Ctrl+D twice).",
              file=sys.stderr)
    before = marker() if marker else None
    try:
        subprocess.run(login_argv(cfg, acc, device_auth, api_key), env=account_env(acc, os.environ),
                       check=False, input=key, text=True)
    finally:
        if marker and marker() != before:
            print("ERROR: global auth was modified", file=sys.stderr)
            raise SystemExit(3)
    return login_status(cfg, acc)


def _providers(orch: Any) -> dict[str, str]:
    """Provider by account name: the project's accounts plus the unreferenced registry ones."""
    return {k: a.provider for k, a in {**orch.catalog, **orch.accounts}.items()}


def _set_token(args: argparse.Namespace) -> dict[str, Any]:
    root = find_root()
    cfg = load_config(root)
    orch = orchestrator.load(root, cfg)
    name = _pick(args, _providers(orch))
    acc = resolve_account(orch, cfg, root, name)
    if acc.credential != "keychain":
        raise ValueError(f"account {name} needs `credential: keychain` in "
                         f"{orchestrator.account_owner(orch, name)}")
    item = ["-s", KEYCHAIN_SERVICE, "-a", keychain_account(acc)]
    if args.delete:
        done = keychain("delete-generic-password", *item)
        return {"account": name, "deleted": done is not None and done.returncode == 0}
    print("Paste the Anthropic Console API key (sk-ant-api...)" if acc.credential_kind == "api_key"
          else "Paste the token `claude setup-token` printed", "; it is not echoed.",
          sep="", file=sys.stderr)
    # -w last: `security` prompts on the terminal itself, so the token never passes through
    # argv or this process.
    done = subprocess.run(["security", "add-generic-password", "-U", *item,
                           "-l", f"agent-bridge {name}", "-w"], check=False)
    if done.returncode != 0:
        raise SystemExit(done.returncode)
    return {"account": name, "stored": True, "logged_in": login_status(cfg, acc) is True}


def _managed_paths() -> tuple[Path, Path, Path]:
    root = find_root()
    ai = root / AI_DIR
    if not (ai / orchestrator.ORCHESTRATOR_FILE).exists():
        raise ValueError(f"{ai / orchestrator.ORCHESTRATOR_FILE} is missing: start one with "
                         "`agent-bridge config-init` first")
    return root, ai / orchestrator.ORCHESTRATOR_FILE, ai / orchestrator.MANAGED_FILE


def _write_managed(yaml_path: Path, path: Path, data: dict) -> None:
    """Only a merged config that loads is written, and atomically."""
    import yaml
    orchestrator.parse(yaml_path.read_text(), str(yaml_path), load_registry(), data, str(path))
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text("# Project accounts. Managed by `agent-bridge project-account-*`; agents and "
                   "roles live in orchestrator.yaml.\n"
                   + yaml.safe_dump(data, sort_keys=True))
    os.replace(tmp, path)


_MANAGED_HEADER = ("# Project accounts. Managed by `agent-bridge project-account-*`; agents and "
                   "roles live in orchestrator.yaml.\n")


def _config_migrate(args: argparse.Namespace) -> dict[str, Any]:
    import datetime

    import yaml
    root = find_root()
    plan = orchestrator.migration_plan(root, load_config(root))
    accounts_text = _MANAGED_HEADER + yaml.safe_dump(plan["accounts_yaml"], sort_keys=True)
    orch_text = yaml.safe_dump(plan["orchestrator_yaml"], sort_keys=False)
    if not plan["changed"]:
        return {"changed": False, "applied": False, "moves": [],
                "note": "already on the one-owner layout; nothing written"}
    if not args.apply:
        return {"changed": True, "applied": False, "moves": plan["moves"],
                ".ai/accounts.yaml": accounts_text, ".ai/orchestrator.yaml": orch_text,
                "note": "comments in orchestrator.yaml are not kept; the original goes to "
                        ".ai/backup-<UTC stamp>/ on --apply. Nothing was written."}
    ai = root / AI_DIR
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = ai / f"backup-{stamp}"
    backup.mkdir(parents=True)
    for name in (orchestrator.ORCHESTRATOR_FILE, orchestrator.MANAGED_FILE):
        if (ai / name).exists():
            shutil.copy2(ai / name, backup / name)
    for name, text in ((orchestrator.MANAGED_FILE, accounts_text),
                       (orchestrator.ORCHESTRATOR_FILE, orch_text)):
        tmp = (ai / name).with_suffix(".yaml.tmp")
        tmp.write_text(text)
        os.replace(tmp, ai / name)
    return {"changed": True, "applied": True, "backup": str(backup), "moves": plan["moves"]}


# --- Reusable helpers for commands that change .ai files after a preview ---------------------

def _backup_and_write(root: Path, texts: dict[str, str]) -> Path | None:
    """Back up the existing files among `texts` (name -> new text) to .ai/backup-<UTC stamp>/,
    then write each atomically. Returns the backup directory (None when nothing existed)."""
    import datetime
    ai = root / AI_DIR
    ensure_ai_dir(root)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    existing = [n for n in texts if (ai / n).exists()]
    backup = ai / f"backup-{stamp}" if existing else None
    if backup is not None:
        backup.mkdir(parents=True)
        for name in existing:
            shutil.copy2(ai / name, backup / name)
    for name, text in texts.items():
        target = ai / name
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, target)
    return backup


def _read_text(path: Path) -> str | None:
    return path.read_text() if path.exists() else None


def _load_layout(texts: dict[str, str | None]) -> tuple[Any, BridgeConfig]:
    """Load the whole configuration from in-memory file texts (name -> text or None when the
    file is absent), exactly as `orchestrator.load` does from disk. ValueError names the problem."""
    import yaml
    from pydantic import ValidationError
    try:
        cfg = BridgeConfig.model_validate(json.loads(texts.get("config.json") or "{}"))
    except json.JSONDecodeError as e:
        raise ValueError(f".ai/config.json: invalid JSON: {e}") from None
    except ValidationError as e:
        raise ValueError(".ai/config.json: " + "; ".join(
            f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())) from None
    registry = load_registry()
    orch_text = texts.get(orchestrator.ORCHESTRATOR_FILE)
    if orch_text is None:
        return orchestrator.with_verify_settings(orchestrator.legacy(cfg, registry), cfg), cfg
    if cfg.accounts or cfg.auto_switch.enabled:
        raise ValueError(".ai/config.json: accounts and auto_switch moved to orchestrator.yaml; "
                         "remove them from .ai/config.json")
    try:
        managed = yaml.safe_load(texts.get(orchestrator.MANAGED_FILE) or "") or {}
    except yaml.YAMLError as e:
        raise ValueError(f".ai/accounts.yaml: invalid YAML: {e}") from None
    if not isinstance(managed, dict) or set(managed) - orchestrator.MANAGED_KEYS:
        raise ValueError(f".ai/accounts.yaml: expected only "
                         f"{sorted(orchestrator.MANAGED_KEYS)}")
    orch = orchestrator.parse(orch_text, f".ai/{orchestrator.ORCHESTRATOR_FILE}", registry,
                              managed, f".ai/{orchestrator.MANAGED_FILE}")
    return orchestrator.with_verify_settings(orch, cfg), cfg


_ORCH_KEYS = ("version", "agents", "roles", "routing", "workflow", "security")
_SECURITY_KEYS = frozenset({"sandbox", "verify_sandbox", "verify_writable", "verify_allowlist",
                            "protected_paths", "sensitive_paths", "env_deny", "env_allow",
                            "claude_hook_settings", "ignore_user_config"})
_SECURITY_NOTE = "security setting: .ai/config.json is tracked, so this applies to every clone"
_MISSING = object()


def _file_of(first: str, legacy: bool) -> str:
    """The file that owns a key's first segment."""
    config_keys = [k for k in BridgeConfig.model_fields if k != "accounts"]
    if legacy:
        if first in config_keys:
            return "config.json"
        raise ValueError(f"{first!r} is not a .ai/config.json setting and this project has no "
                         f".ai/orchestrator.yaml (legacy mode); start one with `agent-bridge "
                         f"config-init`. config.json keys: {', '.join(config_keys)}")
    if first == "accounts":
        return orchestrator.MANAGED_FILE
    if first in _ORCH_KEYS:
        return orchestrator.ORCHESTRATOR_FILE
    if first in config_keys:
        return "config.json"
    raise ValueError(f"unknown setting {first!r}; the first segment must be one of: accounts "
                     f"(.ai/accounts.yaml), {', '.join(_ORCH_KEYS)} (.ai/orchestrator.yaml), "
                     f"{', '.join(config_keys)} (.ai/config.json)")


def _dig(data: Any, parts: list[str]) -> Any:
    for part in parts:
        if not isinstance(data, dict) or part not in data:
            return _MISSING
        data = data[part]
    return data


def _put(data: dict, parts: list[str], value: Any) -> None:
    node = data
    for depth, part in enumerate(parts[:-1]):
        nxt = node.get(part)
        if nxt is None:
            nxt = node[part] = {}
        elif isinstance(nxt, str) and parts[0] == "roles" and depth == 1:
            nxt = node[part] = {"agents": [nxt]}  # `executor: codex` -> `{agents: [codex]}`
        if not isinstance(nxt, dict):
            raise ValueError(f"cannot set {'.'.join(parts)}: {'.'.join(parts[:depth + 1])} "
                             f"is {type(nxt).__name__}, not a mapping")
        node = nxt
    node[parts[-1]] = value


def _drop(data: dict, parts: list[str]) -> None:
    chain = [data]
    for part in parts[:-1]:
        chain.append(chain[-1][part])
    del chain[-1][parts[-1]]
    # A mapping left empty below the top level goes too (limits: {} means nothing).
    for depth in range(len(parts) - 1, 1, -1):
        if chain[depth] != {}:
            break
        del chain[depth - 1][parts[depth - 1]]


def _config_change(args: argparse.Namespace, unset: bool) -> dict[str, Any]:
    """`config-set` and `config-unset`: change one key in the file that owns it, validated as a
    whole before anything is written."""
    import yaml
    root = find_root()
    ai = root / AI_DIR
    parts = args.key.split(".")
    if not all(parts):
        raise ValueError(f"bad key {args.key!r}: use a dotted path such as roles.executor.default")
    last = parts[-1].lower()
    if last != "token_env" and any(w in last for w in ("token", "secret", "password")):
        raise ValueError(f"refused: {args.key} looks like a secret; .ai files are tracked and "
                         "hold no secrets (a variable name goes in token_env)")
    value: Any = None
    if not unset:
        try:
            value = yaml.safe_load(args.value)
        except yaml.YAMLError as e:
            raise ValueError(f"value is not valid YAML: {e}") from None
    legacy = not (ai / orchestrator.ORCHESTRATOR_FILE).exists()
    name = _file_of(parts[0], legacy)
    names = (orchestrator.ORCHESTRATOR_FILE, orchestrator.MANAGED_FILE, "config.json")
    texts = {n: _read_text(ai / n) for n in names}
    try:
        old_orch, old_cfg = _load_layout(texts)
    except ValueError:
        old_orch = old_cfg = None  # an already broken config may be what this change repairs
    try:
        if name == "config.json":
            data = json.loads(texts[name] or "{}")
        else:
            data = yaml.safe_load(texts[name] or "") or {}
    except ValueError as e:  # JSONDecodeError and, below, YAMLError
        raise ValueError(f".ai/{name}: invalid JSON: {e}") from None
    except yaml.YAMLError as e:
        raise ValueError(f".ai/{name}: invalid YAML: {e}") from None
    if not isinstance(data, dict):
        raise ValueError(f".ai/{name}: expected a mapping at the top level")
    if parts[0] == "accounts" and len(parts) > 1:
        account = parts[1]
        raw_orch = yaml.safe_load(texts[orchestrator.ORCHESTRATOR_FILE] or "") or {}
        declared_here = isinstance(raw_orch.get("accounts"), dict) and account in raw_orch["accounts"]
        from_registry = (old_orch is not None and account in old_orch.accounts_from_registry
                         and account not in (data.get("accounts") or {}))
        if declared_here or (from_registry and (unset or len(parts) > 2)):
            owner = (f"accounts.{account} in .ai/orchestrator.yaml" if declared_here else
                     "the machine registry")
            raise ValueError(f"account {account} is declared outside .ai/accounts.yaml (owner: "
                             f"{owner}); run `agent-bridge config-migrate` to move it there")
    old = _dig(data, parts)
    if unset:
        if old is _MISSING:
            return {"file": f".ai/{name}", "key": args.key, "changed": False, "applied": False,
                    "removed": False, "old": None,
                    "note": "the key is not set; nothing written"}
        _drop(data, parts)
    else:
        if old is not _MISSING and old == value:
            return {"file": f".ai/{name}", "key": args.key, "changed": False, "applied": False,
                    "old": old, "new": value, "note": "unchanged; nothing written"}
        _put(data, parts, value)
    if name == "config.json":
        new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    elif name == orchestrator.MANAGED_FILE:
        new_text = _MANAGED_HEADER + yaml.safe_dump(data, sort_keys=True)
    else:
        new_text = yaml.safe_dump(data, sort_keys=False)
    new_texts = {**texts, name: new_text}
    new_orch, new_cfg = _load_layout(new_texts)
    before = (set(orchestrator.deprecated(old_orch, old_cfg)) if old_orch is not None else set())
    created = [d for d in orchestrator.deprecated(new_orch, new_cfg) if d not in before]
    if created:
        raise ValueError("refused: this change would create an outdated layout: "
                         + "; ".join(created))
    out: dict[str, Any] = {"file": f".ai/{name}", "key": args.key, "changed": True,
                           "old": None if old is _MISSING else old,
                           "removed": unset, "applied": bool(args.apply)}
    if not unset:
        out["new"] = value
    if name == "config.json" and parts[0] in _SECURITY_KEYS:
        out["security"] = True
        out["security_note"] = _SECURITY_NOTE
    if name != "config.json":
        out["comments_note"] = f"comments in .ai/{name} are not kept"
    if args.apply:
        backup = _backup_and_write(root, {name: new_text})
        out["backup"] = str(backup) if backup else None
    else:
        out["note"] = ("Nothing was written. --apply backs the file up to .ai/backup-<UTC "
                       "stamp>/ and writes it.")
    return out


def _project_account_add(args: argparse.Namespace) -> dict[str, Any]:
    root, yaml_path, path = _managed_paths()
    data = orchestrator.load_managed(path)
    current = orchestrator.load(root, load_config(root))
    agent = args.agent or f"agent-{args.name}"
    if args.name in current.accounts_in_orchestrator or args.name in (data.get("accounts") or {}):
        raise ValueError(f"account {args.name} already exists; pick another name "
                         f"(change it in {orchestrator.account_owner(current, args.name)})")
    if agent in current.agents:
        raise ValueError(f"agent {agent} already exists; pick another name")
    login = getattr(args, "login", None)
    provider = args.provider
    home = None
    if login:
        target = load_registry().get(login)
        if target is None:
            raise ValueError(f"unknown login {login!r}; registry logins: "
                             f"{sorted(load_registry())}")
        if provider not in (None, target.get("provider")):
            raise ValueError(f"--provider {provider} does not match login {login} "
                             f"({target.get('provider')})")
        if args.home:
            raise ValueError("--login supplies the home; drop --home")
        provider = target.get("provider")
        account: dict[str, Any] = {"login": login}
        resolve_home = target.get("home")
    else:
        if provider is None:
            raise ValueError("give --provider (or --login to use a machine registry login)")
        home = Path(args.home).expanduser() if args.home else None
        if home is None and provider != "opencode":
            home = registry_dir() / "homes" / args.name  # a fresh login home, like account-add
        account = {"provider": provider}
        if home is not None:
            account["home"] = str(home.resolve())
        resolve_home = account.get("home")
    if args.credential:
        account["credential"] = args.credential
    if args.token_env:
        account["token_env"] = args.token_env
    if args.credential_kind != "oauth_token":
        account["credential_kind"] = args.credential_kind
    if limits := _window_limits(args):
        account["limits"] = limits
    adapter = _PROVIDER_ADAPTER[provider]
    spec: dict[str, Any] = {"adapter": adapter, "account": args.name}
    if adapter not in orchestrator.READ_ONLY_ADAPTERS:
        spec["allow_write"] = True
    if args.model:
        spec["model"] = args.model
    # Only the account goes into accounts.yaml; agents and roles are the human's to paste.
    data = {**data, "accounts": {**(data.get("accounts") or {}), args.name: account}}
    paste = {"agents": f"{agent}: {json.dumps(spec)}"}
    command = f"agent-bridge project-agent-add {agent} --account {args.name} --role executor --apply"
    if args.executor:
        paste["roles.executor"] = f"put {agent} first in the agents list of roles.executor"
    # The isolation check routing applies, before anything is written.
    resolve_profile(args.name, provider, resolve_home, root, args.token_env,
                    args.credential, args.credential_kind)
    _write_managed(yaml_path, path, data)
    if home is not None and not home.exists():
        home.mkdir(mode=0o700, parents=True)
        if provider == "openai":
            (home / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
    if args.credential == "keychain":
        step = f"agent-bridge account-set-token {args.name}"
    elif args.token_env:
        step = f"export {args.token_env}=... in the shell that starts Claude Code"
    else:
        step = f"agent-bridge account-login {args.name}"
    return {"account": args.name, "agent": agent, "home": resolve_home,
            "executor": args.executor, "file": str(path), "paste": paste, "next": step,
            "agent_command": command}


_BASH_WARNING = ("allow_bash lets this agent run shell commands in Claude Code's sandbox (writes "
                 "only to the worktree and $TMPDIR, no network); the macOS Keychain stays "
                 "reachable, so give it trusted work only")


def _edit_orchestrator(args: argparse.Namespace, edit: Any) -> dict[str, Any]:
    """Run `edit(data, orch)` on a copy of orchestrator.yaml's data, validate the whole result,
    and preview it (or back it up and write it with --apply). Nothing is written on any error."""
    import copy

    import yaml
    root, path, managed = _managed_paths()
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    new = copy.deepcopy(data)
    out = edit(new, orchestrator.load(root, load_config(root)))
    out |= {"file": f".ai/{orchestrator.ORCHESTRATOR_FILE}", "applied": False}
    if new == data:
        return out | {"changed": False, "note": "unchanged; nothing written"}
    new_text = yaml.safe_dump(new, sort_keys=False)
    orchestrator.parse(new_text, str(path), load_registry(), orchestrator.load_managed(managed),
                       str(managed))
    out |= {"changed": True, "applied": bool(args.apply),
            "comments_note": f"comments in .ai/{orchestrator.ORCHESTRATOR_FILE} are not kept"}
    if args.apply:
        backup = _backup_and_write(root, {orchestrator.ORCHESTRATOR_FILE: new_text})
        out["backup"] = str(backup) if backup else None
    else:
        out["note"] = ("Nothing was written. --apply backs the file up to .ai/backup-<UTC "
                       "stamp>/ and writes it.")
    return out


def _project_agent_add(args: argparse.Namespace) -> dict[str, Any]:
    def edit(data: dict, orch: Any) -> dict[str, Any]:
        name = args.agent
        if name in orch.agents:
            raise ValueError(f"agent {name} already exists; pick another name")
        if args.default and not args.role:
            raise ValueError("--default needs --role")
        if args.account:
            profile = orch.accounts.get(args.account)
            if profile is None:
                raise ValueError(f"unknown account {args.account!r}; known: "
                                 f"{sorted(orch.accounts)} (declare it with `agent-bridge "
                                 "project-account-add`)")
            adapter = _PROVIDER_ADAPTER[profile.provider]
            spec: dict[str, Any] = {"adapter": adapter, "account": args.account}
            if adapter not in orchestrator.READ_ONLY_ADAPTERS:
                spec["allow_write"] = True
        else:
            adapter, spec = "client", {"adapter": "client"}
            if args.model:
                raise ValueError("a client agent takes no model")
        if args.model:
            spec["model"] = args.model
        out: dict[str, Any] = {"action": "add", "agent": name, "spec": spec, "roles": {},
                               "warnings": []}
        if args.allow_bash:
            if adapter != "claude-cli":
                raise ValueError("--allow-bash only applies to claude-cli agents")
            spec["allow_bash"] = True
            out["warnings"].append(_BASH_WARNING)
        data.setdefault("agents", {})[name] = spec
        if args.role:
            roles = data.setdefault("roles", {})
            cur = roles.get(args.role)
            if cur is None:
                roles[args.role] = ({"agents": [name], "default": name} if args.default else name)
            else:
                role = {"agents": [cur]} if isinstance(cur, str) else cur
                if not isinstance(role, dict) or not isinstance(role.get("agents"), list):
                    raise ValueError(f"roles.{args.role} has an unexpected shape; edit it with "
                                     "config-set")
                role["agents"] = ([name, *role["agents"]] if args.default
                                  else [*role["agents"], name])
                if args.default:
                    role["default"] = name
                roles[args.role] = role
            now = roles[args.role]
            out["roles"][args.role] = ([now] if isinstance(now, str) else now["agents"])
            out["default"] = name if args.default else None
        return out
    return _edit_orchestrator(args, edit)


def _pinned_tasks(agent: str) -> list[str]:
    """Unfinished tasks pinned to `agent`; empty when the bridge state cannot be read."""
    try:
        from .models import TERMINAL
        root = find_root()
        b = Bridge(root, load_config(root), create=False)
        return [r["id"] for r in b.store.list(None, 200)
                if r["agent"] == agent and r["state"] not in TERMINAL]
    except Exception:  # noqa: BLE001 - a warning only; the change does not depend on it
        return []


def _project_agent_remove(args: argparse.Namespace) -> dict[str, Any]:
    def edit(data: dict, orch: Any) -> dict[str, Any]:
        name = args.agent
        agents = data.get("agents") or {}
        if name not in agents:
            if name in orch.agents:
                raise ValueError(f"agent {name} is not declared in .ai/orchestrator.yaml; "
                                 f"change it in .ai/{orchestrator.MANAGED_FILE}")
            return {"action": "remove", "agent": name, "roles": {}, "pinned_tasks": []}
        roles = data.get("roles") or {}
        out: dict[str, Any] = {"action": "remove", "agent": name, "roles": {}}
        for role, cur in list(roles.items()):
            if cur == name:
                raise ValueError(f"refused: roles.{role} would be left with no agent; give it "
                                 f"another agent first (config-set roles.{role}.agents ...)")
            if isinstance(cur, dict) and name in (cur.get("agents") or []):
                left = [a for a in cur["agents"] if a != name]
                if not left:
                    raise ValueError(f"refused: roles.{role} would be left with no agent; give "
                                     f"it another agent first (config-set roles.{role}.agents "
                                     "...)")
                cur["agents"] = left
                out["roles"][role] = left
            if isinstance(cur, dict) and cur.get("default") == name:
                del cur["default"]
                out["roles"].setdefault(role, cur["agents"])
        del agents[name]
        out["pinned_tasks"] = _pinned_tasks(name)
        return out
    return _edit_orchestrator(args, edit)


def _own_account(name: str, accounts: dict, path: Path, root: Path) -> None:
    """Refuse an account accounts.yaml does not own, saying which file does."""
    if name in accounts:
        return
    orch = orchestrator.load(root, load_config(root))
    if name in orch.accounts_in_orchestrator or name in orch.accounts_from_registry:
        raise ValueError(f"{name} is not in {path}; change it in "
                         f"{orchestrator.account_owner(orch, name)}")
    raise ValueError(f"{name} is not in {path}")


def _window_limits(args: argparse.Namespace) -> dict[str, float]:
    """The provider-window limits typed on the command line (percent used that stops work)."""
    return {key: value for key, value in (("five_hour_pct", args.five_hour_pct),
                                          ("weekly_pct", args.weekly_pct)) if value is not None}


def _project_account_limits(args: argparse.Namespace) -> dict[str, Any]:
    """Set or clear the 5-hour and weekly limits of an account in .ai/accounts.yaml; other
    limit keys (budget_usd, pricing, ...) are kept."""
    root, yaml_path, path = _managed_paths()
    data = orchestrator.load_managed(path)
    accounts = dict(data.get("accounts") or {})
    _own_account(args.name, accounts, path, root)
    entry = dict(accounts[args.name])
    limits = dict(entry.get("limits") or {})
    if args.clear:
        limits.pop("five_hour_pct", None)
        limits.pop("weekly_pct", None)
    new = _window_limits(args)
    if not new and not args.clear:
        raise ValueError("give --five-hour-pct, --weekly-pct or --clear")
    limits |= new
    if limits:
        entry["limits"] = limits
    else:
        entry.pop("limits", None)
    accounts[args.name] = entry
    _write_managed(yaml_path, path, {**data, "accounts": accounts})
    return {"account": args.name, "limits": entry.get("limits") or {}}


def _project_account_remove(args: argparse.Namespace) -> dict[str, Any]:
    root, yaml_path, path = _managed_paths()
    data = orchestrator.load_managed(path)
    accounts = dict(data.get("accounts") or {})
    _own_account(args.name, accounts, path, root)
    legacy_agents = data.get("agents") or {}
    users = sorted(n for n, a in orchestrator.load(root, load_config(root)).agents.items()
                   if a.account == args.name and n not in legacy_agents)
    if users:
        raise ValueError(f"account {args.name} is still used by agent(s) {', '.join(users)}; "
                         "remove them first: "
                         + "; ".join(f"agent-bridge project-agent-remove {u} --apply"
                                     for u in users))
    typed = input(f"Type the account name to remove {args.name}: ").strip()
    if typed != args.name:
        raise SystemExit(2)
    cfg = load_config(root)
    acc = resolve_account(orchestrator.load(root, cfg), cfg, root, args.name)
    entry = accounts.pop(args.name)
    agents = {k: v for k, v in (data.get("agents") or {}).items()
              if v.get("account") != args.name}
    gone = set(data.get("agents") or {}) - set(agents)
    executor = [a for a in data.get("executor") or [] if a not in gone]
    data = {"accounts": accounts, **({"agents": agents} if agents else {}),
            **({"executor": executor} if executor else {})}
    _write_managed(yaml_path, path, data)
    deleted = False
    if entry.get("credential") == "keychain":
        done = keychain("delete-generic-password", "-s", KEYCHAIN_SERVICE,
                        "-a", keychain_account(acc))
        deleted = done is not None and done.returncode == 0
    # The home may hold a login worth keeping, so it is never deleted here.
    return {"account": args.name, "agents_removed": sorted(gone), "token_deleted": deleted,
            "home_kept": entry.get("home")}


def _limit_warnings(orch: orchestrator.OrchestratorConfig) -> list[str]:
    """Budgets that fail closed on every run whose cost is unknown, and budgets on logins whose
    costs are only list-price estimates."""
    out = []
    for name, profile in sorted(orch.accounts.items()):
        limits = profile.limits
        if limits is None or not limits.enabled or limits.budget_usd is None:
            continue
        if limits.pricing is None and limits.reserve_usd == 0:
            out.append(f"accounts.{name}.limits: budget_usd without pricing or reserve_usd: a run "
                       "whose CLI reports no cost (Codex) or that fails before reporting blocks "
                       "the budget until the period resets")
        if profile.provider in ("anthropic", "openai"):
            out.append(f"accounts.{name}.limits: budget_usd counts list-price estimates (the "
                       "CLI's total_cost_usd or your pricing), not your bill; on a subscription "
                       "login (Pro, Max, ChatGPT) they are not charges, so use the *_pct limits")
    return out


def _read_spec(path: str) -> dict[str, Any]:
    """The task spec in a JSON file, or a ValueError the CLI prints as one line."""
    try:
        data = json.loads(Path(path).read_text())
    except OSError as e:
        raise ValueError(f"cannot read spec {path}: {e.strerror or e}") from None
    except ValueError as e:  # not JSON, or not UTF-8
        raise ValueError(f"spec {path} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ValueError(f"spec {path} must be a JSON object, not {type(data).__name__}")
    return data


async def _run(args: argparse.Namespace) -> Any:
    if args.cmd == "serve":
        from .server import _serve
        # Cancelling the server runs its shutdown, which stops and parks every run it owns.
        _on_stop_signals(asyncio.current_task().cancel)  # type: ignore[union-attr]
        await _serve()
        return None
    if args.cmd == "init":
        changed = install.install(Path(args.dir), args.force, _server_entry())
        print("\n".join(f"updated {c}" for c in changed) or "already up to date")
        print("next steps:\n"
              "  1. agent-bridge health       check agent CLIs and logins\n"
              "  2. claude                    open Claude Code here once and trust the folder\n"
              "  3. agent-bridge              terminal dashboard (or: agent-bridge web)")
        return None
    if args.cmd == "uninstall":
        install.uninstall(Path(args.dir), args.purge)
        return None
    if args.cmd == "storage":
        from . import storage
        if args.storage_cmd == "relink":
            return storage.relink(args.old_root, Path(args.new_root), apply=args.apply)
        if args.storage_cmd == "prune":
            return storage.prune(apply=args.apply)
        if args.storage_cmd == "gc":
            return storage.gc(args.logs_older_than, apply=args.apply)
        if args.storage_cmd == "rollback":
            return storage.rollback(Path(args.root), yes=args.yes)
        if args.storage_cmd == "cleanup-legacy":
            return storage.cleanup_legacy(apply=args.apply)
        try:
            here = find_root()
        except RuntimeError:
            here = None
        return storage.migrate(here, apply=getattr(args, "apply", False))
    if args.cmd == "tasks":  # works anywhere: reads every known project's tasks
        try:
            here = find_root()
        except RuntimeError:
            here = None
        return project_tasks(args.state, args.limit, here)
    if args.cmd == "config-check":
        root = find_root()
        cfg_here = load_config(root)
        orch = orchestrator.load(root, cfg_here)
        out: dict[str, Any] = {
            "ok": True, "source": orch.source, "stages": orch.workflow.stages,
            "accounts": sorted(orch.accounts), "agents": sorted(orch.agents),
            "roles": {r: orch.role(r).agents for r in orch.roles}}
        if warnings := _limit_warnings(orch):
            out["warnings"] = warnings
        if moved := orchestrator.deprecated(orch, cfg_here):
            out["deprecated"] = moved
        errors = []
        for name in sorted(orch.accounts):
            try:  # the same isolation check routing applies, so a bad home fails here first
                resolve_account(orch, load_config(root), root, name)
            except ValueError as e:
                errors.append(str(e))
        if errors:
            out |= {"ok": False, "errors": errors}
        return out
    if args.cmd == "config-init" and args.example is None:
        for path in _example_files():
            header = []
            for line in path.read_text().splitlines():
                if not line.startswith("#"):
                    break
                header.append(line.lstrip("# "))
            print(f"{path.stem:<16} {' '.join(header).split('. ')[0].rstrip('.')}")
        print(f"\nBridge settings for .ai/config.json: {install.KIT / AI_DIR / 'config.example.json'}")
        return None
    # Choosing roles, accounts and fallback is the user's decision, like the account commands.
    if ((args.cmd in HUMAN_ACCOUNT_CMDS or args.cmd in ("config-init", "config-migrate"))
            and not (sys.stdin.isatty() and sys.stdout.isatty())):
        print(f"refused: {args.cmd} needs an interactive terminal (human only)", file=sys.stderr)
        raise SystemExit(2)
    if args.cmd == "config-init":
        root = find_root()
        target = root / AI_DIR / orchestrator.ORCHESTRATOR_FILE
        accounts_target = root / AI_DIR / orchestrator.MANAGED_FILE
        for existing in (target, accounts_target):
            if existing.exists():
                print(f"refused: {existing} exists; edit it or remove it first", file=sys.stderr)
                raise SystemExit(1)
        ensure_ai_dir(root)
        shutil.copyfile(EXAMPLES / f"{args.example}.yaml", target)
        written = [target]
        accounts_source = EXAMPLES / f"{args.example}.accounts.yaml"
        if accounts_source.exists():
            shutil.copyfile(accounts_source, accounts_target)
            written.append(accounts_target)
        print("wrote " + "\nwrote ".join(map(str, written)) +
              f"\nEdit accounts in {accounts_target} and agents in {target}, "
              "then run: agent-bridge config-check")
        return None
    if args.cmd == "config-migrate":
        return _config_migrate(args)
    if args.cmd in ("config-set", "config-unset"):
        return _config_change(args, unset=args.cmd == "config-unset")
    if args.cmd == "account-set-token":
        return _set_token(args)
    if args.cmd == "project-account-add":
        return _project_account_add(args)
    if args.cmd == "project-account-remove":
        return _project_account_remove(args)
    if args.cmd == "project-agent-add":
        return _project_agent_add(args)
    if args.cmd == "project-agent-remove":
        return _project_agent_remove(args)
    if args.cmd == "project-account-limits":
        return _project_account_limits(args)
    if args.cmd == "account-add":
        acc = register(args.name, args.provider, args.home)
        print(f"registered {acc.name} ({acc.provider}) at {acc.home or 'its global login'}")
        cfg = _config_here()
        logged_in = login_status(cfg, acc) is True or _login(cfg, acc, args.device_auth)
        return {"account": acc.name, "provider": acc.provider, "logged_in": logged_in}
    if args.cmd == "account-remove":
        name = _pick(args, {k: e["provider"] for k, e in load_registry().items()})
        typed = input(f"Type the account name to remove {args.name}: ").strip()
        if typed != args.name:
            raise SystemExit(2)
        return {"account": name, "home_deleted": unregister(name)}
    if args.cmd in {"account-list", "account-login", "account-use"}:
        try:
            root = find_root()
        except RuntimeError:
            # Outside a project only machine-wide accounts exist; switching is the common case.
            if args.cmd != "account-use":
                raise
            registry = load_registry()
            name = _pick(args, {k: e["provider"] for k, e in registry.items()})
            if name not in registry:
                raise
            root, cfg = None, BridgeConfig()
            acc = resolve_profile(name, registry[name]["provider"], registry[name].get("home"),
                                  None)
        if root is not None:
            cfg = load_config(root)
            if args.cmd == "account-list":
                return Bridge(root, cfg, create=False).list_accounts()
            orch = orchestrator.load(root, cfg)
            name = _pick(args, {k: a.provider for k, a in orch.accounts.items()})
            acc = resolve_account(orch, cfg, root, name)
        if args.cmd == "account-use":
            if args.shell:
                if acc.provider == "google":
                    # agy's account home is a whole HOME; a shell inside it would lose yours.
                    raise ValueError(f'use: eval "$(agent-bridge account-use {acc.name})"')
                shell = os.environ.get("SHELL", "/bin/sh")
                print(f"agent-bridge: {shell} using account {acc.name}; exit to return",
                      file=sys.stderr)
                subprocess.run([shell], env=account_env(acc, os.environ), check=False)
                return None
            print(shell_use(acc))
            return None
        if acc.home is None and acc.provider != "opencode":
            raise ValueError("account-login requires a configured named account (with its own "
                             "home)")
        return _login(cfg, acc, args.device_auth, getattr(args, "with_api_key", False))
    readonly = args.cmd in {"health", "status", "result", "list", "agents", "workflow-status",
                            "usage", "account-show", "account-verify"}
    if args.cmd == "status" and args.wait > 0:
        readonly = False
    b = Bridge(create=False) if readonly else Bridge()
    if args.cmd in {"execute", "resume", "run-workflow"}:
        b.progress = lambda line: print(line, file=sys.stderr, flush=True)
    if not readonly and args.cmd not in {"approve", "reassign"}:
        b.recover()
    if args.cmd in RUN_CMDS:
        _on_stop_signals(b.stop)
    elif args.cmd not in {"approve", "reassign"}:  # those wait on input(); keep the default
        _on_stop_signals(asyncio.current_task().cancel)  # type: ignore[union-attr]
    match args.cmd:
        case "account-clear":
            return b.clear_account(_pick(args, _providers(b.orch)))
        case "account-show":
            return b.show_account(_pick(args, _providers(b.orch)))
        case "account-verify":
            return b.verify_account(_pick(args, _providers(b.orch)))
        case "health":
            return await b.health()
        case "create":
            return await b.create_task(_read_spec(args.spec))
        case "execute":
            return await b.execute_task(args.task_id, wait=True)
        case "resume":
            return await b.resume_task(args.task_id, args.feedback, wait=True)
        case "status":
            return await b.task_status(args.task_id, args.wait)
        case "result":
            return await b.task_result(args.task_id)
        case "list":
            args.task_total = await b.count_tasks(args.state)
            return await b.list_tasks(args.state, args.limit)
        case "agents":
            return b.list_agents()
        case "usage":
            return b.usage_report(args.account)
        case "plan":
            return await b.plan(args.objective, args.context, wait=True)
        case "run-workflow":
            return await b.run_workflow(args.workflow_id, wait=True)
        case "workflow-status":
            return await b.workflow_status(args.workflow_id)
        case "cancel":
            return await b.cancel_task(args.task_id, args.reason)
        case "review":
            return await b.review_task(args.task_id, args.decision, args.notes)
        case "reassign":
            if not _human_confirm(args.task_id):
                raise SystemExit(2)
            return b.reassign_account(args.task_id, args.agent)
        case "approve":
            if not _human_confirm(args.task_id):
                raise SystemExit(2)
            return b.human_approve(args.task_id)
    return None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="agent-bridge", formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="getting started:\n"
               "  agent-bridge init          set up the kit in this git repository\n"
               "  agent-bridge health        check agent CLIs, logins and quota\n"
               "  agent-bridge config-init   list example role setups (optional)\n"
               "  claude                     open Claude Code in the project and ask for a "
               "change\n"
               "\n"
               "docs: https://github.com/leetuanbmt/agent-orchestration-kit#quick-start")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")
    i = sub.add_parser("init", help="install the kit into a project (default: this directory)")
    i.add_argument("dir", nargs="?", default=".")
    i.add_argument("--force", action="store_true", help="overwrite conflicting kit files")
    u = sub.add_parser("uninstall", help="remove the kit from a project, keeping user edits")
    u.add_argument("dir", nargs="?", default=".")
    u.add_argument("--purge", action="store_true", help="also delete .ai/ (config); task history stays in the bridge state directory")
    sub.add_parser("tui", help="interactive dashboard (the default without a subcommand)")
    web = sub.add_parser("web", help="localhost progress dashboard")
    web.add_argument("--port", type=int, default=8765,
                     help="port on 127.0.0.1; 0 picks a free one (default 8765)")
    web.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    web.add_argument("--read-only", action="store_true", help="view only: no actions from the page")
    sub.add_parser("serve", help="run the MCP stdio server")
    sub.add_parser("health", help="adapters, logins, quota and task counts; exit 1 when "
                              "not OK")
    sub.add_parser("config-check", help="validate .ai/orchestrator.yaml (or the legacy config)")
    sub.add_parser("config-init", help="HUMAN ONLY: start .ai/orchestrator.yaml from an "
                                       "example; without one, list the examples").add_argument(
        "example", nargs="?", choices=[p.stem for p in _example_files()])
    sub.add_parser("config-migrate", help="HUMAN ONLY: move accounts to .ai/accounts.yaml and "
                                          "agents/roles to orchestrator.yaml; previews "
                                          "unless --apply").add_argument(
        "--apply", action="store_true", help="write the files after a backup")
    cset = sub.add_parser("config-set", help="HUMAN ONLY: set one setting (dotted key, YAML "
                                             "value) in the .ai file that owns it; previews "
                                             "unless --apply")
    cset.add_argument("key", help="e.g. roles.executor.default, accounts.cx.limits.weekly_pct, "
                                  "verify_sandbox")
    cset.add_argument("value", help="parsed as YAML: 5, true, null, [a, b], {k: v} or a string")
    cunset = sub.add_parser("config-unset", help="HUMAN ONLY: remove one setting from the .ai "
                                                 "file that owns it; previews unless --apply")
    cunset.add_argument("key")
    for cmd in (cset, cunset):
        cmd.add_argument("--apply", action="store_true", help="write the file after a backup")
    sub.add_parser("agents", help="list agents, roles and availability")
    sub.add_parser("usage", help="per-account usage, limits, remaining quota and reset "
                                 "times").add_argument("account", nargs="?")
    sub.add_parser("account-list", help="usable accounts on this machine").add_argument(
        "--all", action="store_true",
        help="also list switch profiles, homes not added and CLIs without an account")
    sub.add_parser("account-show", help="one account: credential reference (never the "
                   "value), login, quota, limits, agents, role defaults").add_argument("name")
    sub.add_parser("account-verify", help="check an account's login and credential; exit 1 "
                   "if it cannot run").add_argument("name")
    clear = sub.add_parser("account-clear", help="clear a QUOTA/AUTH unavailable mark on an "
                                                 "account after a human fixed it")
    clear.add_argument("name")
    login = sub.add_parser("account-login", help="HUMAN ONLY: log in an isolated account")
    login.add_argument("name")
    how = login.add_mutually_exclusive_group()
    how.add_argument("--device-auth", action="store_true")
    how.add_argument("--with-api-key", action="store_true",
                     help="openai: log Codex in with an API key, typed at a hidden prompt")
    add = sub.add_parser("account-add", help="HUMAN ONLY: create a machine-wide account and "
                                             "log in (fresh login in its own home)")
    add.add_argument("name")
    add.add_argument("--provider", required=True,
                     choices=["openai", "anthropic", "google", "opencode"])
    add.add_argument("--home", help="register an existing isolated home (e.g. a codex-switch "
                                    "home) instead of creating one")
    add.add_argument("--device-auth", action="store_true")
    padd = sub.add_parser("project-account-add", help="HUMAN ONLY: declare an account in "
                                                      ".ai/accounts.yaml and print the agent line to "
                                                      "paste into .ai/orchestrator.yaml")
    padd.add_argument("name")
    padd.add_argument("--provider", choices=sorted(_PROVIDER_ADAPTER),
                      help="required unless --login names a registry login")
    padd.add_argument("--login", help="use this machine registry login (provider and home come "
                                      "from it; no new home is created)")
    padd.add_argument("--home", help="auth home (default: a new ~/.agent-bridge/homes/<name>)")
    cred = padd.add_mutually_exclusive_group()
    cred.add_argument("--credential", choices=["keychain"],
                      help="anthropic: keep a setup-token in the macOS Keychain")
    cred.add_argument("--token-env", help="anthropic: AGENT_BRIDGE_CLAUDE_<NAME>_TOKEN")
    padd.add_argument("--credential-kind", choices=["oauth_token", "api_key"],
                      default="oauth_token", help="anthropic: what the secret is: a "
                      "`claude setup-token` token (default) or a Console API key")
    padd.add_argument("--agent", help="agent name (default: agent-<name>)")
    padd.add_argument("--model")
    padd.add_argument("--executor", action="store_true",
                      help="also print a hint to put the agent first in roles.executor "
                           "(writes nothing)")
    plim = sub.add_parser("project-account-limits", help="HUMAN ONLY: set or clear the 5-hour and "
                                                         "weekly limits of an account in "
                                                         ".ai/accounts.yaml")
    plim.add_argument("name")
    plim.add_argument("--clear", action="store_true", help="remove both window limits first")
    for cmd in (padd, plim):
        cmd.add_argument("--five-hour-pct", type=float,
                         help="stop taking work at this percent of the 5-hour window used")
        cmd.add_argument("--weekly-pct", type=float,
                         help="stop taking work at this percent of the weekly window used")
    sub.add_parser("project-account-remove", help="HUMAN ONLY: remove an account from "
                                                  ".ai/accounts.yaml and its Keychain token; "
                                                  "its home is kept"
                   ).add_argument("name")
    pag = sub.add_parser("project-agent-add", help="HUMAN ONLY: add an agent to .ai/orchestrator.yaml "
                                                   "(and to a role); previews unless --apply")
    pag.add_argument("agent")
    target = pag.add_mutually_exclusive_group(required=True)
    target.add_argument("--account", help="the project account the agent runs on (its provider "
                                          "picks the adapter)")
    target.add_argument("--adapter", choices=["client"],
                        help="client: the MCP caller itself, no account")
    pag.add_argument("--model")
    pag.add_argument("--allow-bash", action="store_true",
                     help="claude-cli only; Bash runs in Claude Code's sandbox (see security.md)")
    pag.add_argument("--role", help="also add the agent to this role")
    pag.add_argument("--default", action="store_true", help="make it the role's default (first)")
    prem = sub.add_parser("project-agent-remove", help="HUMAN ONLY: remove an agent and its role "
                                                       "entries from .ai/orchestrator.yaml; "
                                                       "previews unless --apply")
    prem.add_argument("agent")
    for cmd in (pag, prem):
        cmd.add_argument("--apply", action="store_true", help="write the file after a backup")
    token = sub.add_parser("account-set-token", help="HUMAN ONLY: store a setup-token or API "
                                                     "key in the macOS Keychain for an "
                                                     "account with `credential: keychain`")
    token.add_argument("name")
    token.add_argument("--delete", action="store_true", help="remove the stored token")
    sub.add_parser("account-remove", help="HUMAN ONLY: forget a machine-wide account and "
                                          "delete the home it created").add_argument("name")
    use = sub.add_parser("account-use", help="point your interactive CLI at an account: "
                                             'eval "$(agent-bridge account-use <name>)"')
    use.add_argument("name")
    use.add_argument("--shell", action="store_true", help="open a subshell using the account")
    for cmd in (clear, login, use, token, sub.choices["account-remove"]):
        cmd.add_argument("--provider", help="which account, when one name is used by several "
                                            "providers")
    c = sub.add_parser("create", help="create a task from a JSON spec file")
    c.add_argument("spec")
    for name, text in (("execute", "start the executor agent on a READY task"),
                       ("result", "full evidence of a task: patch, checks, scope violations, "
                                  "reviews")):
        sub.add_parser(name, help=text).add_argument("task_id")
    r = sub.add_parser("resume", help="re-run a REWORK, FAILED or PAUSED task, continuing its "
                                      "session")
    r.add_argument("task_id")
    r.add_argument("--feedback", default="", help="notes for the agent's next run")
    s = sub.add_parser("status", help="compact status of a task; --wait blocks until it leaves "
                                      "RUNNING/VERIFYING")
    s.add_argument("task_id")
    s.add_argument("--wait", type=float, default=0, help="seconds to wait, at most 55")
    st = sub.add_parser("storage", help="where bridge state lives; migrate, relink, prune and "
                                        "clean it up").add_subparsers(dest="storage_cmd",
                                                                      required=True)
    st.add_parser("status", help="show the state location and any legacy storage")
    sl = st.add_parser("relink", help="A human runs this after moving a repository")
    sl.add_argument("old_root")
    sl.add_argument("new_root")
    sl.add_argument("--apply", action="store_true")
    sm = st.add_parser("migrate", help="A human runs this after closing every Claude session, TUI and agent-bridge web")
    sm.add_argument("--apply", action="store_true")
    sr = st.add_parser("rollback", help="A human runs this to move a migrated central store aside")
    sr.add_argument("root")
    sr.add_argument("--yes", action="store_true")
    sc = st.add_parser("cleanup-legacy", help="A human runs this to back up verified legacy files")
    sc.add_argument("--apply", action="store_true")
    sp = st.add_parser("prune", help="Preview removal of missing project roots from the registry")
    sp.add_argument("--apply", action="store_true")
    sg = st.add_parser("gc", help="Preview deletion of old logs of finished tasks")
    sg.add_argument("--logs-older-than", type=float, required=True)
    sg.add_argument("--apply", action="store_true")
    tk = sub.add_parser("tasks", help="tasks of every project and worktree agent-bridge has "
                                      "run in, grouped by project")
    tk.add_argument("--state")
    tk.add_argument("--limit", type=int, default=10, help="tasks shown per project")
    ls = sub.add_parser("list", help="tasks of this project, newest first")
    ls.add_argument("--state")
    ls.add_argument("--limit", type=int, default=50)
    x = sub.add_parser("cancel", help="cancel a non-terminal task")
    x.add_argument("task_id")
    x.add_argument("--reason", required=True, help="why the task is cancelled")
    v = sub.add_parser("review", help="record the client review of a task in REVIEW")
    v.add_argument("task_id")
    v.add_argument("--decision", choices=["approve", "rework", "block"], required=True)
    v.add_argument("--notes", required=True)
    pl = sub.add_parser("plan", help="run the planner agent on an objective")
    pl.add_argument("objective")
    pl.add_argument("--context", default="")
    for name, text in (("run-workflow", "run the tasks of a planned workflow in dependency "
                                         "order"),
                       ("workflow-status", "status of a workflow and its tasks")):
        sub.add_parser(name, help=text).add_argument("workflow_id")
    sub.add_parser("approve", help="HUMAN ONLY: close an APPROVED task").add_argument("task_id")
    reass = sub.add_parser("reassign", help="HUMAN ONLY: move a task to another agent or account")
    reass.add_argument("task_id")
    reass.add_argument("agent")
    for name in VIEWS:
        sub.choices[name].add_argument("--json", action="store_true",
                                       help="print the data as JSON")
    args = p.parse_args(argv)
    if os.environ.get(AGENT_ENV):
        print("refused: the bridge cannot run inside an agent it started", file=sys.stderr)
        raise SystemExit(2)
    if args.cmd == "web":
        from . import webui

        webui.serve(args.port, args.open, read_only=args.read_only)
        return
    if args.cmd in (None, "tui"):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            p.print_help()
            raise SystemExit(0 if args.cmd is None else 2)
        from .tui import run_tui
        run_tui()
        return
    try:
        out = asyncio.run(_run(args))
    except asyncio.CancelledError:
        if not _signalled:
            raise
        raise SystemExit(128 + _signalled[0]) from None
    except (ValueError, KeyError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    if out is not None:
        view = VIEWS.get(args.cmd)
        if view and not getattr(args, "json", False) and sys.stdout.isatty():
            from rich.console import Console

            from . import render
            console = Console()
            extra = ({"width": console.width, "show_all": args.all}
                     if args.cmd == "account-list" else
                     {"project": str(find_root()), "total": args.task_total}
                     if args.cmd == "list" else {})
            console.print(getattr(render, view)(out, **extra))
        else:
            print(dumps(out))
            if args.cmd == "list" and len(out) < args.task_total:
                from .render import task_list_notice
                print(task_list_notice(len(out), args.task_total), file=sys.stderr)
    if args.cmd == "storage" and any(e["status"] in ("failed", "corrupt", "changed")
                                     for e in out.get("sources", []) + out.get("entries", [])):
        raise SystemExit(1)
    if _signalled:
        raise SystemExit(128 + _signalled[0])  # the run was stopped; its task is PAUSED
    if args.cmd in ("health", "config-check", "account-verify") and not out["ok"]:
        raise SystemExit(1)  # scripts and CI can gate on both


if __name__ == "__main__":
    main()
