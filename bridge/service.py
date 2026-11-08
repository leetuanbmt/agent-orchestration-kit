"""Bridge service: the one orchestration layer behind both the MCP server and the CLI."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
import shlex
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import osutil, progress, storage
from .accounts import Account, AccountLock, account_key, binary, discover, load_registry, \
    login_status, resolve, resolve_profile
from .config import AI_DIR, BridgeConfig, ensure_ai_dir, find_root, load_config, state_dir
from .executor import ADAPTERS, AGENT_ENV, PLAN_SCHEMA, Adapter, RunRequest, RunResult, \
    build_plan_prompt, build_prompt, build_rework_prompt, kill_stale
from .gate import LIMIT_REPORT_TTL_S as LIMIT_REPORT_TTL_S
from .gate import AccountGate
from .models import ACTIVE, TERMINAL, Actor, ReviewDecision, State, TaskSpec, TransitionError, \
    leftover_failure
from .orchestrator import ADAPTER_PROVIDERS, PROVIDERS, AgentConfig, OrchestratorConfig
from .orchestrator import load as load_orchestrator
from .quota import codex_home_for, read_quota, remaining_pct
from .registry import remember_project
from .review import EvidenceError, content_hashes, current_head, diff, parse_command, protected_dirty, \
    escaping_symlinks, scope_violations, snapshot, untracked
from .runner import _write_attempt_log_line as _write_attempt_log_line
from .sandbox import describe as describe_sandbox
from .runner import TaskRunner
from .security import hook_settings_error, matches, PathError, process_started, redact, runner_alive, validate_pattern
from .state import ConflictError, LimitExceeded, Store, overview

REBINDABLE = {State.READY, State.REWORK, State.FAILED, State.PAUSED}
# How long cancel waits for the runner to stop: one poll plus the agent's TERM and KILL grace.
CANCEL_WAIT_S = 15
# A workflow task whose account (or worktree) slot is held by another bridge process retries
# after this delay, doubling up to the maximum plus up to 1 s of jitter, until
# routing.slot_wait_s has passed.
SLOT_WAIT_FIRST_S = 5.0
SLOT_WAIT_MAX_S = 60.0


def _alive(pid: int | None) -> bool:
    return osutil.alive(pid)


def _attempt_log_path(root: Path, task_id: str, n: int) -> Path:
    return state_dir(root) / "logs" / f"{task_id}-{n}.jsonl"


def resolve_account(orch: OrchestratorConfig, cfg: BridgeConfig, root: Path,
                    name: str) -> Account:
    if orch.source.startswith("legacy") and (name == "default" or name in cfg.accounts):
        return resolve(cfg, root, name)
    profile = orch.accounts.get(name)
    if profile is None:
        raise ValueError(f"unknown account: {name}")
    return resolve_profile(name, profile.provider, profile.home, root)


class Bridge:
    def __init__(self, root: str | Path | None = None, cfg: BridgeConfig | None = None,
                 orch: OrchestratorConfig | None = None, *, create: bool = True):
        if os.environ.get(AGENT_ENV):
            # Every agent the bridge starts carries this variable, so do its children. An agent
            # that imports the bridge (e.g. to call human_approve) is refused here as the CLI and
            # the MCP server already refuse it. A process that unsets the variable gets past
            # this: approval is a human-only step by process discipline, not an OS boundary.
            raise RuntimeError("agent-bridge refuses to run inside an agent it started "
                               f"({AGENT_ENV} is set)")
        self.root = find_root(root)
        self.cfg = cfg or load_config(self.root)
        self.orch = orch or load_orchestrator(self.root, self.cfg)
        # Legacy mode: agents are named after the `.ai/config.json` accounts and every task is
        # pinned to an account at creation, exactly as before roles existed.
        self.legacy = self.orch.source.startswith("legacy")
        if create:
            ensure_ai_dir(self.root)
        location = storage.locate(self.root, create=create)
        self.state_dir = state_dir(self.root) if create else location.dir
        self.storage = location.kind
        database = self.state_dir / "bridge.db"
        legacy = self.root / AI_DIR / "bridge.db"
        if not create and not database.exists() and legacy.exists():
            self.state_dir = legacy.parent
            database = legacy
        migrated = create and legacy.exists() and not database.exists()
        if migrated:
            # SQLite sidecars may contain committed state after an interrupted runner.
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(legacy) + suffix)
                if source.exists():
                    os.replace(source, Path(str(database) + suffix))
        shared = storage.home() / "accounts.db"
        if create or database.is_file():
            self.store = Store(database, shared=shared if create or shared.is_file() else None)
        else:
            self.store = Store(Path(":memory:"))
        if migrated:
            self.store.log(None, "bridge", "state_migrated",
                           {"from": str(legacy), "to": str(database)})
        if create:
            remember_project(self.root)
        self._adapters: dict[str, Adapter] = {}
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._workflows: dict[str, asyncio.Task[None]] = {}
        self.progress: Callable[[str], None] | None = None
        self._stopping = False
        self.gate = AccountGate(self)
        self.runner = TaskRunner(self)

    # ---- helpers ---------------------------------------------------------------------------

    def _adapter(self, name: str) -> Adapter:
        if name not in self._adapters:
            self._adapters[name] = ADAPTERS[name](self.cfg, self.root)
        return self._adapters[name]

    def _account(self, name: str) -> Account:
        return resolve_account(self.orch, self.cfg, self.root, name)

    def _key(self, name: str) -> str | None:
        try:
            return account_key(self._account(name))
        except ValueError:
            return None

    def _slots(self, account: str) -> int:
        profile = self.orch.accounts.get(account)
        return profile.max_concurrency if profile else 1

    def _agent_of(self, row: dict[str, Any]) -> str | None:
        if row["agent"]:
            return row["agent"]
        return (row["account"] or "default") if self.legacy else None

    def _summary(self, row: dict[str, Any]) -> dict[str, Any]:
        spec = row["spec"]
        return {
            "task_id": row["id"],
            "agent": self._agent_of(row),
            "account": row["account"] or ("default" if self.legacy else None),
            "workflow_id": row["workflow_id"],
            "state": row["state"],
            "runner_alive": (row["runner_pid"] == os.getpid() or self._runner_alive(row))
            if row["state"] in {State.RUNNING, State.VERIFYING} else None,
            "objective": spec["objective"][:200],
            "attempts": row["attempts"],
            "max_attempts": self._max_attempts(row),
            "session_id": row["session_id"],
            "failure": row["failure"],
            "needs_human_approval": bool(row["needs_human"]),
            # A reviewer agent is still deciding: wait instead of reviewing over it.
            "review_pending": row["state"] == State.REVIEW and row["id"] in self._runs,
            "updated_at": row["updated_at"],
        }

    def _max_attempts(self, row: dict[str, Any]) -> int:
        return int(row["spec"].get("max_attempts") or self.orch.max_attempts)

    def _timeout(self, agent: AgentConfig, requested: int | None) -> int:
        return min(requested or agent.timeout_s or self.cfg.default_timeout_s,
                   self.cfg.max_timeout_s)

    def _account_homes(self) -> list[Path]:
        """Auth homes of every configured account; no agent may read them."""
        homes = set()
        for name in self.orch.accounts:
            try:
                home = self._account(name).home
            except ValueError:
                continue
            if home is not None:
                homes.add(home)
        return sorted(homes)

    def _account_quota(self, name: str) -> dict[str, Any] | str:
        try:
            account = self._account(name)
        except ValueError:
            return "UNKNOWN"
        if account.provider == "openai":
            found = read_quota(codex_home_for(account))
            if found:
                return found
        # Providers without readable rollouts: the limits the CLI reported on its last run.
        stored = self.store.quota(name, key=account_key(account))
        if stored is None:
            return "UNKNOWN"
        limits = stored["rate_limits"]
        return {"remaining_pct": remaining_pct(limits, time.time()),
                "observed_at": stored["observed_at"],
                "primary": limits.get("primary"), "secondary": limits.get("secondary")}

    # ---- delegates to the account gate and the task runner ---------------------------------
    # The facade and the runner call these rather than the parts, so a method replaced on a
    # Bridge instance applies to every caller.

    @property
    def _reports(self) -> dict[tuple[str, float], tuple[float, int, dict[str, Any]]]:
        return self.gate._reports

    def _account_state(self, name: str, *, key: str | None) -> dict[str, Any]:
        """The account state before work starts on it (a lapsed QUOTA mark is lifted)."""
        return self.gate._refresh_account_marks(name, key=key)

    def _usage_key(self, name: str) -> str:
        return self.gate._usage_key(name)

    @staticmethod
    def _period_starts(now: float) -> dict[str, float]:
        return AccountGate._period_starts(now)

    def _limit_report(self, name: str, reserve: float = 0.0) -> dict[str, Any]:
        return self.gate._limit_report(name, reserve)

    def _reserve(self, name: str, task_id: str | None) -> int | None:
        return self.gate._reserve(name, task_id)

    def _settle_open(self, usage_id: int | None, cost: float | None, source: str) -> None:
        self.gate._settle_open(usage_id, cost, source)

    def _record_account(self, name: str, res: RunResult, usage_id: int | None = None,
                        task_id: str | None = None) -> bool:
        return self.gate._record_account(name, res, usage_id, task_id)

    def _unavailable(self, name: str, need: str) -> str | None:
        return self.gate._unavailable(name, need)

    def _pick(self, role: str, need: str, spec: dict[str, Any] | None = None) -> str:
        return self.gate._pick(role, need, spec)

    def _executors_on(self, account: str) -> list[str]:
        return self.gate._executors_on(account)

    def _quota_fallback(self, task_id: str) -> None:
        self.gate._quota_fallback(task_id)

    def _route(self, task_id: str) -> bool:
        return self.gate._route(task_id)

    def _unroute(self, task_id: str, error: ConflictError | LimitExceeded) -> None:
        self.gate._unroute(task_id, error)

    async def _start(self, task_id: str, kind: str, prompt: str, session: str | None,
                     expect: set[State], wait: bool, actor: Actor) -> dict[str, Any]:
        return await self.runner._start(task_id, kind, prompt, session, expect, wait, actor)

    async def _run(self, task_id: str, n: int, used: int, prompt: str,
                   session: str | None, lock: AccountLock, usage_id: int | None = None) -> None:
        await self.runner._run(task_id, n, used, prompt, session, lock, usage_id)

    async def _run_inner(self, task_id: str, n: int, used: int, prompt: str,
                         session: str | None, lock: AccountLock,
                         usage_id: int | None = None) -> None:
        await self.runner._run_inner(task_id, n, used, prompt, session, lock, usage_id)

    async def _review_stage(self, task_id: str) -> None:
        await self.runner._review_stage(task_id)

    def _snapshot(self) -> dict[str, Any]:
        """The worktree before a run, compared with review.diff afterwards."""
        return snapshot(self.root, self.cfg.protected_paths)

    def _attempt_log(self, task_id: str, n: int) -> Path:
        """Where attempt `n` of a task writes its output lines."""
        return _attempt_log_path(self.root, task_id, n)

    # ---- accounts and agents ---------------------------------------------------------------

    def list_accounts(self) -> dict[str, Any]:
        only_config = set(self.orch.accounts) == {"default", *self.cfg.accounts}
        listing = (discover(self.cfg, self.root) if self.legacy and only_config else
                   discover(self.cfg, self.root, [self._account(n) for n in self.orch.accounts]))
        registry = load_registry()
        for account in listing["accounts"]:
            account["quota"] = self._account_quota(account["name"])
            if self.gate._limits(account["name"]) is not None:
                account["limits"] = self._limit_report(
                    account["name"], reserve=self.gate._next_run_reserve(account["name"]))
            state, _ = self.gate._effective_account_state(account["name"],
                                                          key=self._key(account["name"]))
            account.update(state=state["state"], last_error=state["last_error"],
                           max_concurrency=self._slots(account["name"]),
                           source="registry" if account["name"] in registry else "project")
        # Agent CLIs installed on this machine that no account here uses yet (no login probe:
        # some CLIs need the network or start a service just to answer).
        used = {a.get("provider", "openai") for a in listing["accounts"]}
        listing["unconfigured"] = [
            {"provider": provider, "binary": path,
             "hint": f"agent-bridge account-add <name> --provider {provider}"}
            for provider in sorted(PROVIDERS - used)
            if (path := shutil.which(binary(self.cfg, provider)))]
        return listing

    def usage_report(self, name: str | None = None) -> dict[str, Any]:
        """Per-account state, limit checks with spend, and provider quota; read-only.

        Limits are human-owned config (.ai/orchestrator.yaml); this only reports them."""
        if name is not None and name not in self.orch.accounts:
            raise ValueError(f"unknown account {name!r}; known: {sorted(self.orch.accounts)}")
        names = [name] if name is not None else list(self.orch.accounts)
        # Read-only: the stored state as is (no QUOTA lift) and spend without settling a dead
        # owner's reservation.
        return {"accounts": [
            {"name": n, "provider": self.orch.accounts[n].provider,
             "state": self.store.account_state(n, key=self._key(n)),
             "limits": self.gate._evaluate(n, self.gate._next_run_reserve(n), expire=False),
             "quota": self._account_quota(n)}
            for n in names], "generated_at": time.time()}

    def list_agents(self, logins: dict[str, bool | None] | None = None) -> dict[str, Any]:
        """`logins` (account → logged in, from a listing already made) skips the login probes.

        A logged-out account only shows here: routing keeps relying on the AUTH pause, so a
        stale probe never blocks work after the human logs in.
        """
        roles: dict[str, list[str]] = {}
        for role in self.orch.roles:
            for name in self.orch.role(role).agents:
                roles.setdefault(name, []).append(role)
        reasons = {name: self._unavailable(name, "read") for name in self.orch.agents}
        if logins is None:
            logins = {}
            for name in {a.account for n, a in self.orch.agents.items()
                         if a.account and reasons[n] is None}:
                logins[name] = login_status(self.cfg, self._account(name))
        agents = []
        for name, agent in self.orch.agents.items():
            reason = reasons[name]
            if reason is None and agent.account and logins.get(agent.account) is False:
                reason = f"account {agent.account} logged out"
            agents.append({
                "agent": name, "adapter": agent.adapter,
                "provider": ADAPTER_PROVIDERS[agent.adapter], "account": agent.account,
                "model": agent.model, "read": agent.can("read"), "write": agent.can("write"),
                "allow_bash": agent.allow_bash, "roles": roles.get(name, []),
                "available": reason is None, "unavailable_reason": reason,
            })
        return {"source": self.orch.source, "stages": self.orch.workflow.stages,
                "roles": {r: self.orch.role(r).model_dump() for r in self.orch.roles},
                "quota_fallback": self.orch.routing.quota_fallback.model_dump(),
                "agents": agents}

    def clear_account(self, name: str) -> dict[str, Any]:
        account = self._account(name)
        self.store.set_account_state(account.name, "available", actor=Actor.CLIENT,
                                     key=account_key(account))
        return self.store.account_state(account.name, key=account_key(account))

    def reassign_account(self, task_id: str, target: str) -> dict[str, Any]:
        """Human move of a task to another agent (or to the first executor on an account)."""
        if target not in self.orch.agents:
            matches = self._executors_on(target)
            if not matches:
                raise ValueError(f"unknown agent or account: {target}")
            target = matches[0]
        return self._rebind(task_id, target, Actor.HUMAN, "account_reassigned", {})

    def _rebind(self, task_id: str, name: str, actor: Actor, event_kind: str,
                detail: dict[str, Any]) -> dict[str, Any]:
        agent = self.orch.agent(name)
        if not agent.enabled or not agent.can("write"):
            raise ValueError(f"agent {name} cannot execute tasks")
        new = self._account(agent.account or "")

        def change(row: dict[str, Any]) -> tuple[str, str, TaskSpec, dict[str, Any]]:
            old_agent = self._agent_of(row)
            old_account = row["account"] or ("default" if self.legacy else None)
            data = dict(row["spec"])
            if data.get("agent"):
                data["agent"] = name
            if data.get("account"):
                data["account"] = new.name
            moved = {"from": old_agent, "to": name}
            if old_agent != old_account or name != new.name:
                moved |= {"from_account": old_account, "to_account": new.name}
            return name, new.name, TaskSpec.model_validate(data), {**moved, **detail}

        return self._summary(self.store.rebind(task_id, REBINDABLE, change, actor, event_kind))

    async def health(self) -> dict[str, Any]:
        codex: dict[str, Any] = {"path": self._adapter("codex-cli").available()}
        if codex["path"]:
            for key, args in (("version", ["--version"]), ("auth", ["login", "status"])):
                try:
                    out = subprocess.run([self.cfg.codex_bin, *args], capture_output=True,
                                         text=True, timeout=20, check=False)
                    line = (out.stdout or out.stderr).strip().splitlines()
                    codex[key] = redact(line[0] if line else "", 200)
                    if key == "auth":
                        codex["logged_in"] = out.returncode == 0
                except (OSError, subprocess.TimeoutExpired) as e:
                    codex[key] = f"error: {e}"
        used = sorted({a.adapter for a in self.orch.agents.values()
                       if a.enabled and a.adapter != "client"})
        adapters = {}
        for name in used:
            path = self._adapter(name).available()
            adapters[name] = {"path": path,
                              "version": self._adapter(name).version() if path else None}
        counts = self.store.task_counts()
        active = [r["id"] for r in self.store.list(limit=10_000) if r["state"] in ACTIVE]
        quota = {}
        for name in self.orch.accounts:
            remaining = self.gate._remaining(name)
            quota[name] = remaining if remaining is not None else "UNKNOWN"
        limits: dict[str, dict[str, Any]] = {}
        for name in self.orch.accounts:
            if self.gate._limits(name) is not None:
                report = self._limit_report(name, reserve=self.gate._next_run_reserve(name))
                limits[name] = {"status": report["status"], "reason": report["reason"]}
        storage_notice = storage_fix = None
        if self.storage == "legacy":
            storage_notice = ("legacy storage: run `agent-bridge storage status`, then "
                              "`agent-bridge storage migrate --apply` after closing Claude sessions")
            storage_fix = ("run `agent-bridge storage status`, then "
                           "`agent-bridge storage migrate --apply` after closing Claude sessions")
        elif written := storage.legacy_writes(self.state_dir):
            source, count = written
            storage_notice = (f"legacy database {source} has {count} events written after "
                              "migration (an old agent-bridge process is still running?)")
            storage_fix = ("stop the old agent-bridge process (close the Claude sessions that "
                           "still run it), then check `agent-bridge storage status`")
        try:
            dirty = protected_dirty(self.root, self.cfg)
        except EvidenceError as e:  # cannot tell: do not report the project as fine
            dirty = [f"(git status failed: {redact(str(e), 200)})"]
        verify_sandbox = describe_sandbox(self.orch.workflow.verify_sandbox)

        def item(code: str, message: str, fix: str) -> dict[str, str]:
            return {"code": code, "message": message, "fix": fix}

        # Exactly the causes of `ok` being false, in a stable order.
        problems = [item("adapter_missing", f"{name} is not installed",
                         f"install the {name.removesuffix('-cli')} CLI (or set its *_bin in "
                         ".ai/config.json)")
                    for name, info in adapters.items() if not info["path"]]
        if "codex-cli" in used and adapters["codex-cli"]["path"] \
                and not codex.get("logged_in", False):
            problems.append(item("codex_logged_out", "codex is not logged in",
                                 "run `codex login` yourself, or "
                                 "`agent-bridge account-add <name> --provider openai`"))
        if dirty:
            problems.append(item("protected_dirty",
                                 "protected files differ from HEAD and would be loaded as they "
                                 f"are: {', '.join(dirty)}",
                                 "inspect them (git diff) and restore what you did not change "
                                 "yourself"))
        # Worth knowing, but never a reason for `ok` to be false.
        warnings = []
        if storage_notice:
            message = ("the project still uses legacy storage" if self.storage == "legacy"
                       else storage_notice)
            warnings.append(item("storage", message, storage_fix or storage_notice))
        if "unavailable" in verify_sandbox:
            problems.append(item("verify_unconfined", f"verification sandbox: {verify_sandbox}",
                                 "confine verification commands by running the bridge on a "
                                 "Linux kernel with Landlock, or have a human set "
                                 "workflow.verify_sandbox: none in .ai/orchestrator.yaml "
                                 "to accept unconfined checks"))
        elif self.orch.workflow.verify_sandbox == "none":
            warnings.append(item("verify_unconfined", "verification checks run unconfined by choice",
                                 "run the bridge on a Linux kernel with Landlock and set "
                                 "workflow.verify_sandbox: auto in .ai/orchestrator.yaml "
                                 "to confine verification commands"))
        for name in self.orch.accounts:
            try:
                key = account_key(self._account(name))
            except ValueError:  # misconfigured: list_accounts reports it
                continue
            mark, _ = self.gate._effective_account_state(name, key=key)  # lapsed QUOTA: available
            error = redact(mark.get("last_error") or "", 200)
            detail = f": {error}" if error else ""
            if mark.get("state") == "quota":
                warnings.append(item("account_quota",
                                     f"account {name} is marked out of quota{detail}",
                                     "wait for the quota window to reset, or enable "
                                     "routing.quota_fallback in .ai/orchestrator.yaml"))
            elif mark.get("state") == "auth":
                warnings.append(item("account_auth",
                                     f"account {name} needs a new login{detail}",
                                     "a human logs the account in again with "
                                     f"`agent-bridge account-login {name}`"))
        for name, info in limits.items():
            if info["status"] == "limited":
                warnings.append(item("account_limited",
                                     f"account {name} is over its limits: {info['reason']}",
                                     "wait for the limit window to reset, or have a human "
                                     f"change accounts.{name}.limits in .ai/orchestrator.yaml"))
        return {
            "project_root": str(self.root),
            "database": str(self.store.path),
            "state_dir": str(self.state_dir),
            "storage": self.storage,
            "storage_notice": storage_notice,
            "protected_dirty": dirty,
            "orchestrator": {"source": self.orch.source, "stages": self.orch.workflow.stages},
            "codex": codex,
            "adapters": adapters,
            "sandbox": self.cfg.sandbox,
            "verify_sandbox": verify_sandbox,
            "quota": quota,
            "limits": limits,
            "active_tasks": active,
            "task_counts": counts,
            "ok": not problems,
            "problems": problems,
            "warnings": warnings,
        }

    # ---- tasks -----------------------------------------------------------------------------

    def _bind(self, s: TaskSpec) -> tuple[str | None, str | None]:
        """(agent, account) a new task is pinned to; (None, None) = routed at first run."""
        if s.agent is not None:
            agent = self.orch.agent(s.agent)
            if not agent.enabled or not agent.can("write"):
                raise ValueError(f"agent {s.agent} cannot execute tasks")
            if s.account is not None and s.account != agent.account:
                raise ValueError(f"agent {s.agent} uses account {agent.account}, not {s.account}")
            return s.agent, agent.account
        if self.legacy:
            s.account = resolve(self.cfg, self.root, s.account).name
            return s.account, s.account
        if s.account is not None:
            account = self._account(s.account).name
            matches = self._executors_on(account)
            if not matches:
                raise ValueError(f"no executor agent uses account {account}")
            return matches[0], account
        if self.orch.role("executor").strategy == "explicit":
            raise ValueError("roles.executor uses strategy explicit: name an agent, one of "
                             f"{self.orch.role('executor').agents}")
        return None, None

    def _prepare(self, spec: dict[str, Any]) -> tuple[TaskSpec, str | None, str | None]:
        s = TaskSpec.model_validate(spec)
        s.sandbox = s.sandbox or self.cfg.sandbox
        agent, account = self._bind(s)
        try:
            s.allowed_paths = [validate_pattern(p, self.root, self.cfg.protected_paths)
                               for p in s.allowed_paths]
            if links := escaping_symlinks(self.root, s.allowed_paths):
                raise PathError(f"symlink escapes the project: {links[0]}")
            s.excluded_paths = [validate_pattern(p, self.root, []) for p in s.excluded_paths]
        except PathError as e:
            raise ValueError(str(e)) from e
        for c in s.verification_commands:
            parse_command(c, self.cfg)
        if s.timeout_s and s.timeout_s > self.cfg.max_timeout_s:
            raise ValueError(f"timeout_s exceeds max_timeout_s ({self.cfg.max_timeout_s})")
        return s, agent, account

    def _insert(self, s: TaskSpec, agent: str | None, account: str | None,
                workflow_id: str | None = None) -> tuple[dict[str, Any], bool]:
        # A planned task dedups only inside its own workflow; matching another workflow's live task
        # would hand back a row this workflow never lists or drives.
        h = hashlib.sha256((s.model_dump_json() + (workflow_id or "")).encode()).hexdigest()
        return self.store.create(s, h, agent=agent, workflow_id=workflow_id, account=account)

    async def create_task(self, spec: dict[str, Any]) -> dict[str, Any]:
        row, dup = self._insert(*self._prepare(spec))
        return {**self._summary(row), "duplicate": dup}

    async def execute_task(self, task_id: str, wait: bool = False,
                           actor: Actor = Actor.CLIENT) -> dict[str, Any]:
        row = self.store.get(task_id)
        if row["state"] == State.BACKLOG:
            raise TransitionError(f"{task_id} has unfinished dependencies: "
                                  f"{row['spec']['depends_on']}")
        self._quota_fallback(task_id)
        routed = self._route(task_id)
        row = self.store.get(task_id)
        prompt = build_prompt(task_id, row["spec"], self.cfg.protected_paths)
        try:
            return await self._start(task_id, "execute", prompt, None, {State.READY}, wait,
                                     actor)
        except (ConflictError, LimitExceeded) as e:
            if routed:
                self._unroute(task_id, e)
            raise

    async def resume_task(self, task_id: str, feedback: str = "", wait: bool = False,
                          actor: Actor = Actor.CLIENT) -> dict[str, Any]:
        row = self.store.get(task_id)
        expect = {State.REWORK, State.FAILED, State.PAUSED}
        if row["state"] not in expect:
            raise TransitionError(f"{task_id} is {row['state']}; resume needs one of "
                                  f"{sorted(expect)}")
        self._quota_fallback(task_id)
        routed = self._route(task_id)
        row = self.store.get(task_id)
        attempts = self.store.attempts(task_id)
        if not feedback:
            for attempt in reversed(attempts):
                review = attempt.get("review")
                if isinstance(review, dict):
                    feedback = review.get("notes", "")
                    break
                if attempt.get("outcome") == "ok":
                    break
            if not any(a.get("review") is not None for a in attempts):
                clean = next((a for a in reversed(attempts) if a.get("outcome") == "ok"),
                             None)
                reviews = [e for e in self.store.events(task_id, 200)
                           if e["kind"] == "review" and isinstance(e["detail"], dict)
                           and (clean is None or
                                e["ts"] >= (clean.get("ended_at") or clean["started_at"]))]
                feedback = reviews[-1]["detail"].get("notes", "") if reviews else ""
        last_ver = attempts[-1].get("verification") if attempts else None
        if row["session_id"]:
            prompt = build_rework_prompt(task_id, feedback, last_ver)
        else:
            prompt = build_prompt(task_id, row["spec"], self.cfg.protected_paths)
            if feedback:
                prompt += f"\n\n## Feedback from the previous attempt\n{feedback}"
        try:
            return await self._start(task_id, "resume", prompt, row["session_id"], expect, wait,
                                     actor)
        except (ConflictError, LimitExceeded) as e:
            if routed:
                self._unroute(task_id, e)
            raise

    def _hook_settings(self, agent: AgentConfig) -> str | None:
        if agent.adapter == "claude-cli" and self.cfg.claude_hook_settings:
            path = self.cfg.claude_hook_settings
            if error := hook_settings_error(path, self.root):
                raise ValueError(f"claude_hook_settings: {error}")
            return path
        return None

    def stop(self) -> None:
        """Stop every run of this process within ~1 s: agents and checks are killed, the tasks
        are PAUSED (INTERRUPTED) and can be resumed or cancelled."""
        self._stopping = True

    # ---- review ----------------------------------------------------------------------------

    async def _consult(self, name: str, prompt: str, schema: dict[str, Any],
                       task_id: str | None, cancel: Callable[[], bool]) -> RunResult | None:
        """One read-only run of a planner or reviewer agent; None when its account is busy or
        over its limit."""
        agent = self.orch.agents[name]
        hook_settings = self._hook_settings(agent)
        account = self._account(agent.account or "")
        lock = AccountLock(account, self.cfg.lock_dir, self._slots(account.name))
        try:
            lock.acquire()
        except ConflictError as e:
            self.store.log(task_id, Actor.BRIDGE, "agent_skipped",
                           {"agent": name, "reason": str(e)})
            return None
        try:
            usage_id = self._reserve(account.name, task_id)
        except LimitExceeded as e:
            lock.release()
            self.store.log(task_id, Actor.BRIDGE, "agent_skipped",
                           {"agent": name, "reason": f"account {account.name} over limit: {e}"})
            return None
        except BaseException:
            lock.release()
            raise
        try:
            try:
                res = await self._adapter(agent.adapter).run(
                    RunRequest(prompt, account, "read-only", self._timeout(agent, None),
                               schema=schema, model=agent.model, hook_settings=hook_settings,
                               read_deny=self._account_homes(),
                               keep_fds=() if (fd := lock.fileno()) is None else (fd,)),
                    should_cancel=cancel)
            finally:
                lock.release()
            self._record_account(account.name, res, usage_id, task_id)
        finally:
            self._settle_open(usage_id, None, "unavailable")
        return res

    def _apply_review(self, task_id: str, decision: str, notes: str, actor: Actor,
                      reviewer: str | None) -> dict[str, Any]:
        d = ReviewDecision(decision=decision, notes=notes)  # type: ignore[arg-type]
        row = self.store.get(task_id)
        if row["state"] != State.REVIEW:
            raise TransitionError(f"{task_id} is {row['state']}; review needs REVIEW")
        detail = d.model_dump() | ({"reviewer": reviewer} if reviewer else {})
        detail["actor"] = str(actor)
        if d.decision == "rework":
            row = self.store.transition(task_id, State.REWORK, actor, detail,
                                        expect={State.REVIEW}, attempt_review=detail)
        elif d.decision == "block":
            row = self.store.transition(task_id, State.BLOCKED, actor, detail,
                                        expect={State.REVIEW}, attempt_review=detail,
                                        failure=("BLOCKED by review: " + redact(d.notes, 8000))[:500])
        else:
            self._assert_gates(task_id, need_review=False)
            row = self.store.transition(task_id, State.APPROVED, actor, detail,
                                        expect={State.REVIEW}, attempt_review=detail)
            if not row["needs_human"]:
                row = self._close(task_id, Actor.BRIDGE)
        out = self._summary(row)
        if row["state"] == State.APPROVED:
            out["next"] = (f"Human approval required. A person approves it in the dashboard "
                           f"(run `agent-bridge` in {shlex.quote(str(self.root))}, select the task, "
                           f"press a) or runs, in an interactive "
                           f"terminal: BRIDGE_PROJECT_ROOT={shlex.quote(str(self.root))} "
                           f"uv run --project {shlex.quote(str(Path(__file__).resolve().parents[1]))} "
                           f"agent-bridge approve {task_id}")
        return out

    async def review_task(self, task_id: str, decision: str, notes: str) -> dict[str, Any]:
        return self._apply_review(task_id, decision, notes, Actor.CLIENT, None)

    def _assert_gates(self, task_id: str, need_review: bool = True) -> None:
        attempts = self.store.attempts(task_id)
        last = attempts[-1] if attempts else {}
        ver = last.get("verification") or {}
        if last.get("outcome") != "ok" or not ver.get("passed"):
            raise TransitionError(f"{task_id}: quality gate failed (last attempt not verified)")
        review = last.get("review")
        approved = isinstance(review, dict) and review.get("decision") == "approve"
        if need_review and review is None:
            approved = any(
                e["kind"] == "review" and isinstance(e["detail"], dict)
                and e["detail"].get("decision") == "approve"
                and e["ts"] >= last["started_at"]
                for e in self.store.events(task_id, 500)
            )
        if need_review and not approved:
            raise TransitionError(f"{task_id}: quality gate failed (no approving review)")
        self._assert_verified_content(task_id, last)

    def _assert_verified_content(self, task_id: str, last: dict[str, Any]) -> None:
        """Approval and closing bind to the verified HEAD and touched files' content."""
        verified_head = (last.get("changes") or {}).get("verified_head")
        if verified_head:
            state = self.store.get(task_id)["state"]
            fix = ("send it back (review --decision rework) and resume it to verify again"
                   if state == State.REVIEW else
                   "restore the verified HEAD, or cancel the task and create a new one")
            try:
                head = current_head(self.root)
            except (OSError, subprocess.SubprocessError, EvidenceError) as exc:
                raise TransitionError(f"{task_id}: cannot read HEAD since verification "
                                      f"({verified_head[:8]}); {fix}") from exc
            if not head:
                raise TransitionError(f"{task_id}: cannot read HEAD since verification "
                                      f"({verified_head[:8]}); {fix}")
            if head != verified_head:
                raise TransitionError(f"{task_id}: HEAD changed since verification: "
                                      f"{verified_head[:8]} -> {head[:8]}; {fix}")
        state = self.store.get(task_id)["state"]
        fix = ("send it back (review --decision rework) and resume it to verify again"
               if state == State.REVIEW else
               "restore the verified content, or cancel the task and create a new one")
        baseline = last.get("baseline")
        if baseline is not None:
            new = (untracked(self.root) - set(baseline["files"])
                   - set((last.get("changes") or {}).get("files", []))
                   - set((last.get("verification") or {}).get("changed_by_verification", []))
                   - self.store.other_attempt_files_since(task_id, last["ended_at"]))
            new = {path for path in new if not matches(path, self.cfg.protected_paths)}
            if new:
                raise TransitionError(f"{task_id}: new untracked files since verification: "
                                      f"{', '.join(sorted(new))}; {fix}")
        bound = (last.get("changes") or {}).get("after_hashes")
        if bound is None:  # verified before approvals were bound to content
            self.store.log(task_id, Actor.BRIDGE, "approval_unbound",
                           {"reason": "attempt verified without content hashes"})
            return
        now = content_hashes(self.root, list(bound))
        moved = sorted(f for f in bound if now[f] != bound[f])
        if moved:
            raise TransitionError(f"{task_id}: worktree changed since verification: "
                                  f"{', '.join(moved)}; {fix}")

    def _close(self, task_id: str, actor: Actor,
               event: tuple[str, Any] | None = None) -> dict[str, Any]:
        self._assert_gates(task_id)
        return self.store.transition(task_id, State.DONE, actor, {"gates": "passed"},
                                     extra_event=event)

    def human_approve(self, task_id: str) -> dict[str, Any]:
        """Only reachable from the interactive CLI (`agent-bridge approve`), never from MCP."""
        row = self.store.get(task_id)
        if row["state"] != State.APPROVED:
            raise TransitionError(f"{task_id} is {row['state']}; human approval needs APPROVED")
        return self._summary(self._close(task_id, Actor.HUMAN,
                                         ("human_approval", {"tty": True})))

    # ---- task queries ----------------------------------------------------------------------

    async def cancel_task(self, task_id: str, reason: str) -> dict[str, Any]:
        running = self.store.get(task_id)["state"] in ACTIVE
        row = self.store.transition(task_id, State.CANCELLED, Actor.CLIENT, {"reason": reason},
                                    failure=f"CANCELLED: {reason}"[:500])
        owner = row["runner_pid"]
        if owner != os.getpid() and not self._runner_alive(row):
            await asyncio.to_thread(kill_stale, row["codex_pid"], row["codex_started"])
        elif running:
            # The runner (this process or another one, e.g. a CLI `execute`) polls the state,
            # kills its agent or verification process group within ~1 s, then releases the
            # account lock and clears runner_pid. Signalling it instead could stop an MCP server
            # that owns other runs.
            deadline = time.monotonic() + CANCEL_WAIT_S
            while (self.store.get(task_id)["runner_pid"] == owner
                   and time.monotonic() < deadline):
                await asyncio.sleep(0.2)
        task = self._runs.get(task_id)
        if task:  # a background run here, maybe in its review stage
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), CANCEL_WAIT_S)
        return self._summary(self.store.get(task_id))

    async def task_status(self, task_id: str, wait_s: float = 0) -> dict[str, Any]:
        deadline = time.monotonic() + min(max(wait_s, 0), 55)
        row = self.store.get(task_id)
        while ((row["state"] in ACTIVE or task_id in self._runs)
               and time.monotonic() < deadline):
            await asyncio.sleep(1)
            row = self.store.get(task_id)
        out = self._summary(row)
        attempts = self.store.attempts(task_id)
        if attempts:
            a = attempts[-1]
            log_path = self.state_dir / "logs" / f"{task_id}-{a['n']}.jsonl"
            out["last_attempt"] = {"n": a["n"], "outcome": a["outcome"],
                                   "log": str(log_path) if log_path.exists() else None,
                                   "changed_files": (a.get("changes") or {}).get("files"),
                                   "verification_passed": (a.get("verification") or {}).get(
                                       "passed")}
        return out

    async def task_result(self, task_id: str) -> dict[str, Any]:
        row = self.store.get(task_id)
        attempts = []
        for a in self.store.attempts(task_id):
            log_path = self.state_dir / "logs" / f"{task_id}-{a['n']}.jsonl"
            attempts.append({k: a.get(k) for k in (
                "n", "kind", "outcome", "exit_code", "session_id", "final", "usage", "changes",
                "verification", "stderr_tail")})
            attempts[-1]["log"] = str(log_path) if log_path.exists() else None
        return {
            **self._summary(row),
            "spec": row["spec"],
            "attempts": attempts,
            "reviews": [e["detail"] for e in self.store.events(task_id, 200)
                        if e["kind"] == "review"],
        }

    async def count_tasks(self, state: str | None = None) -> int:
        if state:
            State(state)
        return self.store.count(state)

    async def list_tasks(self, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if state:
            State(state)
        return [self._summary(r) for r in self.store.list(state, min(limit, 200))]

    # ---- workflows -------------------------------------------------------------------------

    async def plan(self, objective: str, context: str = "", wait: bool = False) -> dict[str, Any]:
        """Run the planner agent; its tasks become a workflow (state PLANNING -> READY)."""
        if "plan" not in self.orch.workflow.stages:
            raise ValueError("the workflow has no plan stage; create tasks with "
                             "bridge_create_task")
        name = self._pick("planner", "read")
        if self.orch.agents[name].adapter == "client":
            raise ValueError("the planner is the MCP client: split the work yourself and call "
                             "bridge_create_task")
        wid = self.store.create_workflow(objective, name, "PLANNING")["id"]
        coro = self._plan(wid, name, objective, context)
        if wait:
            await coro
        else:
            self._workflows[wid] = asyncio.create_task(coro)
        return self._workflow(wid)

    async def _plan(self, wid: str, name: str, objective: str, context: str) -> None:
        try:
            prompt = build_plan_prompt(objective, context, self.cfg.protected_paths)
            res = await self._consult(name, prompt, PLAN_SCHEMA, None, lambda: self._stopping)
            if res is None or res.outcome != "ok" or not isinstance(res.final, dict):
                why = (res.outcome + ": " + res.error if res
                       else "account busy or over its limit")
                raise ValueError(f"planner {name} failed: {why}")
            planned = res.final.get("tasks") or []
            if not planned:
                raise ValueError(f"planner {name} returned no tasks")
            # Validate every task before creating any, so a bad plan leaves nothing behind.
            prepared = []
            for i, task in enumerate(planned):
                deps = task.get("depends_on") or []
                if any(not isinstance(d, int) or not 0 <= d < i for d in deps):
                    raise ValueError(f"task {i}: depends_on must list earlier task indices")
                prepared.append((self._prepare({**task, "depends_on": []}), deps))
            ids: list[str] = []
            for (s, agent, account), deps in prepared:
                s.depends_on = [ids[d] for d in deps]
                ids.append(self._insert(s, agent, account, wid)[0]["id"])
            self.store.set_workflow(wid, "READY", {"tasks": ids, "notes": res.final.get("notes")})
        except Exception as e:
            self.store.set_workflow(wid, "FAILED", {"error": redact(str(e), 2000)})
        finally:
            self._workflows.pop(wid, None)

    async def workflow_status(self, wid: str, wait_s: float = 0) -> dict[str, Any]:
        deadline = time.monotonic() + min(max(wait_s, 0), 55)
        while (self.store.workflow(wid)["state"] in {"PLANNING", "RUNNING"}
               and time.monotonic() < deadline):
            await asyncio.sleep(1)
        return self._workflow(wid)

    def _workflow(self, wid: str) -> dict[str, Any]:
        wf = self.store.workflow(wid)
        return {"workflow_id": wid, "objective": wf["objective"], "state": wf["state"],
                "planner": wf["planner"], "detail": wf["detail"],
                # Planned commands run on this machine: show them before run_workflow.
                "tasks": [{**self._summary(r),
                           "verification_commands": r["spec"].get("verification_commands", [])}
                          for r in self.store.workflow_tasks(wid)]}

    async def run_workflow(self, wid: str, wait: bool = False) -> dict[str, Any]:
        """Drive a workflow's tasks through execute, test and review until it needs a decision."""
        wf = self.store.workflow(wid)
        if wf["state"] in {"PLANNING", "FAILED"}:
            raise TransitionError(f"workflow {wid} is {wf['state']}")
        if wid not in self._workflows:
            coro = self._drive(wid)
            if wait:
                await coro
            else:
                self._workflows[wid] = asyncio.create_task(coro)
        return self._workflow(wid)

    def _actionable(self, row: dict[str, Any]) -> bool:
        if row["state"] in {State.READY, State.REWORK, State.FAILED}:
            return True  # _start blocks the task once its attempt budget is spent
        return (row["state"] == State.PAUSED and (row["failure"] or "").startswith("QUOTA")
                and self.orch.routing.quota_fallback.enabled)

    async def _drive(self, wid: str) -> None:
        # ponytail: tasks run one at a time; one worktree has a single writer anyway.
        waiting: dict[str, str] = {}
        # Tasks waiting for a slot another bridge process holds: task id -> deadline and next
        # try (monotonic), current delay, reason, and the deadline as wall-clock time.
        slots: dict[str, dict[str, Any]] = {}
        slot_wait_s = self.orch.routing.slot_wait_s
        shown = False  # the workflow detail lists waiting_for_slot

        def slot_busy(name: str, spec: dict[str, Any]) -> bool:
            """Executor `name` can take the task once a slot frees up (or already can)."""
            if self.gate._unscopable(self.orch.agents[name], spec):
                return False
            why = self.gate._unavailable(name, "write")
            return why is None or why.endswith((" busy", " busy in another bridge process"))

        try:
            self.store.set_workflow(wid, "RUNNING")
            while not self._stopping:
                tasks = [t for t in self.store.workflow_tasks(wid)
                         if t["id"] not in waiting and self._actionable(t)]
                actionable = {t["id"] for t in tasks}
                for tid in [k for k in slots if k not in actionable]:
                    del slots[tid]  # it left an actionable state (e.g. cancelled): wait ends
                if slots or shown:
                    self.store.set_workflow(wid, "RUNNING", {"waiting_for_slot": [
                        {"task_id": k, "reason": v["reason"], "until": v["until"]}
                        for k, v in slots.items()]} if slots else None)
                    shown = bool(slots)
                now = time.monotonic()
                step = next((t for t in tasks
                             if t["id"] not in slots or slots[t["id"]]["next"] <= now), None)
                if step is None:
                    if not slots:
                        break
                    # Short steps so a stop or a cancel ends the wait within about a second.
                    pause = min(v["next"] for v in slots.values()) - now
                    await asyncio.sleep(min(max(pause, 0), 1.0))
                    continue
                tid = step["id"]
                try:
                    if step["state"] == State.READY:
                        await self.execute_task(tid, wait=True, actor=Actor.BRIDGE)
                    else:
                        await self.resume_task(tid, wait=True, actor=Actor.BRIDGE)
                except (ValueError, ConflictError) as e:
                    reason = redact(str(e), 500)
                    # Routing an unbound task refuses with a ValueError when every executor is
                    # out; that is a slot conflict too while one of them is only busy (or freed
                    # up since). Other ValueErrors (limits, transitions) are not temporary.
                    if isinstance(e, ValueError) and not (
                            self._agent_of(row := self.store.get(tid)) is None
                            and str(e).startswith("no eligible executor agent")
                            and any(slot_busy(n, row["spec"])
                                    for n in self.orch.role("executor").agents)):
                        slots.pop(tid, None)
                        waiting[tid] = reason
                        continue
                    slot = slots.get(tid)
                    now = time.monotonic()
                    if slot_wait_s <= 0:
                        waiting[tid] = reason
                    elif slot is None:
                        slots[tid] = {"deadline": now + slot_wait_s, "delay": SLOT_WAIT_FIRST_S,
                                      "next": now + SLOT_WAIT_FIRST_S + random.uniform(0, 1),
                                      "reason": reason, "until": time.time() + slot_wait_s}
                        self.store.log(tid, Actor.BRIDGE, "slot_wait",
                                       {"workflow_id": wid, "reason": reason,
                                        "wait_s": slot_wait_s})
                    elif now >= slot["deadline"]:
                        del slots[tid]
                        self.store.log(tid, Actor.BRIDGE, "slot_wait_expired",
                                       {"workflow_id": wid, "reason": reason,
                                        "wait_s": slot_wait_s})
                        waiting[tid] = reason
                    else:
                        delay = min(slot["delay"] * 2, SLOT_WAIT_MAX_S)
                        # The last try falls on the deadline, so expiry is never late.
                        slot.update(delay=delay, reason=reason, next=min(
                            now + delay + random.uniform(0, 1), slot["deadline"]))
                else:
                    slots.pop(tid, None)
            rows = self.store.workflow_tasks(wid)
            states = {t["id"]: t["state"] for t in rows}
            unreachable: dict[str, str] = {}
            while True:
                added = False
                for task in rows:
                    tid = task["id"]
                    if states[tid] != State.BACKLOG or tid in unreachable:
                        continue
                    for dependency in task["spec"]["depends_on"]:
                        if (states.get(dependency) in {State.CANCELLED, State.BLOCKED}
                                or dependency in unreachable):
                            unreachable[tid] = dependency
                            added = True
                            break
                if not added:
                    break
            if states and all(s == State.DONE for s in states.values()):
                final = "DONE"
            elif all(s in TERMINAL or tid in unreachable for tid, s in states.items()):
                final = "INCOMPLETE"
            else:
                final = "WAITING"  # a review, a human approval or an account needs attention
            detail = {"tasks": states, "waiting": waiting}
            if unreachable:
                detail["unreachable"] = unreachable
            self.store.set_workflow(wid, final, detail)
        except Exception as e:
            self.store.set_workflow(wid, "WAITING", {"error": redact(repr(e), 2000)})
        finally:
            self._workflows.pop(wid, None)

    # ---- lifecycle -------------------------------------------------------------------------

    def _leftover(self, row: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
        """Scope violations an interrupted attempt left, diffed against its stored baseline."""
        if not baseline:
            return []  # attempts from older versions: nothing to compare with
        try:
            changes = diff(self.root, baseline, self.cfg.protected_paths)
        except EvidenceError:
            return []
        return scope_violations(changes["files"], row["spec"], self.cfg,
                                changes["head_changed"], root=self.root)

    @staticmethod
    def _runner_alive(row: dict[str, Any]) -> bool:
        """The task's runner process still runs. A pid the OS reused for another process has a
        different start time, so it does not count (rows from older versions have none)."""
        return runner_alive(row["runner_pid"], row.get("runner_started"))

    def recover(self) -> list[str]:
        """Park tasks and workflows whose process died mid-run (crash, kill, closed session)."""
        recovered = []
        for row in self.store.list(limit=10_000):
            if row["state"] not in ACTIVE:
                continue
            if row["runner_pid"] != os.getpid() and self._runner_alive(row):
                continue  # another live process (e.g. the CLI) owns this run
            killed = kill_stale(row["codex_pid"], row["codex_started"])
            leftover: list[str] = []
            for a in self.store.attempts(row["id"]):
                if a["ended_at"] is None:
                    leftover = self._leftover(row, a.get("baseline"))
                    self.store.finish_attempt(row["id"], a["n"], outcome="interrupted", **(
                        {"verification": {"passed": False, "scope_violations": leftover,
                                          "checks": [], "interrupted": True}} if leftover else {}))
            if leftover:  # resuming would take these changes as the next attempt's baseline
                self.store.transition(row["id"], State.BLOCKED, Actor.BRIDGE,
                                      {"reason": "runner died", "killed_orphan": killed,
                                       "scope_violations": leftover},
                                      failure=leftover_failure(leftover))
            else:
                self.store.transition(row["id"], State.PAUSED, Actor.BRIDGE,
                                      {"reason": "runner died", "killed_orphan": killed},
                                      failure="INTERRUPTED: runner process exited during execution",
                                      attempts=max(row["attempts"] - 1, 0))
            recovered.append(row["id"])
        for wf in self.store.workflows_in("PLANNING", "RUNNING"):
            owner = wf["runner_pid"]
            if (owner != os.getpid() and _alive(owner)
                    and wf["runner_started"] in (None, process_started(owner))):
                continue  # its owner is alive (a reused pid has another start time)
            planning = wf["state"] == "PLANNING"
            self.store.set_workflow(wf["id"], "FAILED" if planning else "WAITING", {
                "error": f"interrupted: the process {'planning' if planning else 'driving'} "
                         "it exited; " + ("plan again" if planning else "run it again")})
            recovered.append(wf["id"])
        return recovered

    async def shutdown(self) -> None:
        self._stopping = True
        pending = [*self._runs.values(), *self._workflows.values()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def project_tasks(state: str | None = None, limit: int = 10,
                  here: Path | None = None) -> dict[str, Any]:
    """Tasks of every project agent-bridge has run in (and `here`), and of their git worktrees
    (each keeps its own tasks), most recently active project first."""
    roots = progress.discover_roots(here)
    projects = []
    for root in roots:
        try:
            db = storage.find_store(root)
        except (OSError, subprocess.CalledProcessError, ValueError):
            continue  # The store cannot be resolved.
        if not db.exists():
            continue
        try:
            data = overview(db, state, limit)
        except sqlite3.Error:
            continue  # locked mid-migration or not a bridge database
        if not data["counts"]:
            continue
        projects.append({"project": Path(root).name, "root": root, "root_missing": not Path(root).is_dir(),
                         "counts": data["counts"],
                         "tasks": [{"task_id": t["id"], "state": t["state"],
                                    "agent": t.get("agent") or t.get("account"),
                                    "objective": (t.get("spec") or {}).get("objective", ""),
                                    "updated_at": t["updated_at"]} for t in data["tasks"]]})
    projects.sort(key=lambda p: max((t["updated_at"] for t in p["tasks"]), default=0),
                  reverse=True)
    return {"projects": projects}


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)
