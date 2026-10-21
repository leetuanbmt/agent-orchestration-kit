"""Bridge service: the one orchestration layer behind both the MCP server and the CLI."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .accounts import Account, AccountLock, discover, login_status, resolve, resolve_profile
from .config import AI_DIR, BridgeConfig, ensure_ai_dir, find_root, load_config, state_dir
from .executor import ADAPTERS, PLAN_SCHEMA, REVIEW_SCHEMA, Adapter, RunRequest, RunResult, \
    build_plan_prompt, build_prompt, build_review_prompt, build_rework_prompt, kill_stale
from .models import ACTIVE, TERMINAL, Actor, ReviewDecision, State, TaskSpec, TransitionError
from .orchestrator import ADAPTER_PROVIDERS, AgentConfig, OrchestratorConfig
from .orchestrator import load as load_orchestrator
from .quota import codex_home_for, read_quota, remaining_pct
from .review import diff, parse_command, run_verification, scope_violations, snapshot, \
    touches_sensitive
from .security import PathError, redact, validate_pattern
from .state import ConflictError, Store

# Outcomes that mean "the environment, not the code": park the task, never auto-retry.
PAUSE_OUTCOMES = {"quota", "auth", "network", "missing_binary"}
REBINDABLE = {State.READY, State.REWORK, State.FAILED, State.PAUSED}


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def resolve_account(orch: OrchestratorConfig, cfg: BridgeConfig, root: Path,
                    name: str) -> Account:
    if orch.source.startswith("legacy"):
        return resolve(cfg, root, name)
    profile = orch.accounts.get(name)
    if profile is None:
        raise ValueError(f"unknown account: {name}")
    return resolve_profile(name, profile.provider, profile.home, root)


class Bridge:
    def __init__(self, root: str | Path | None = None, cfg: BridgeConfig | None = None,
                 orch: OrchestratorConfig | None = None):
        self.root = find_root(root)
        self.cfg = cfg or load_config(self.root)
        self.orch = orch or load_orchestrator(self.root, self.cfg)
        # Legacy mode: agents are named after the `.ai/config.json` accounts and every task is
        # pinned to an account at creation, exactly as before roles existed.
        self.legacy = self.orch.source.startswith("legacy")
        ensure_ai_dir(self.root)
        self.state_dir = state_dir(self.root)
        database = self.state_dir / "bridge.db"
        legacy = self.root / AI_DIR / "bridge.db"
        migrated = legacy.exists() and not database.exists()
        if migrated:
            # SQLite sidecars may contain committed state after an interrupted runner.
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(legacy) + suffix)
                if source.exists():
                    os.replace(source, Path(str(database) + suffix))
        self.store = Store(database)
        if migrated:
            self.store.log(None, "bridge", "state_migrated",
                           {"from": str(legacy), "to": str(database)})
        self._adapters: dict[str, Adapter] = {}
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._workflows: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    # ---- helpers ---------------------------------------------------------------------------

    def _adapter(self, name: str) -> Adapter:
        if name not in self._adapters:
            self._adapters[name] = ADAPTERS[name](self.cfg, self.root)
        return self._adapters[name]

    def _account(self, name: str) -> Account:
        return resolve_account(self.orch, self.cfg, self.root, name)

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
            "objective": spec["objective"][:200],
            "attempts": row["attempts"],
            "max_attempts": self._max_attempts(row),
            "session_id": row["session_id"],
            "failure": row["failure"],
            "needs_human_approval": bool(row["needs_human"]),
            "updated_at": row["updated_at"],
        }

    def _max_attempts(self, row: dict[str, Any]) -> int:
        return int(row["spec"].get("max_attempts") or self.orch.max_attempts)

    def _timeout(self, agent: AgentConfig, requested: int | None) -> int:
        return min(requested or agent.timeout_s or self.cfg.default_timeout_s,
                   self.cfg.max_timeout_s)

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
        stored = self.store.quota(name)
        if stored is None:
            return "UNKNOWN"
        limits = stored["rate_limits"]
        return {"remaining_pct": remaining_pct(limits, time.time()),
                "observed_at": stored["observed_at"],
                "primary": limits.get("primary"), "secondary": limits.get("secondary")}

    def _remaining(self, name: str) -> float | None:
        quota = self._account_quota(name)
        return quota.get("remaining_pct") if isinstance(quota, dict) else None

    def _record_account(self, name: str, res: RunResult) -> None:
        if res.rate_limits:
            self.store.save_quota(name, res.rate_limits)
        if res.outcome in {"quota", "auth", "ok"}:
            self.store.set_account_state(
                name, "available" if res.outcome == "ok" else res.outcome,
                res.error if res.outcome != "ok" else None, actor=Actor.BRIDGE,
            )

    def _unavailable(self, name: str, need: str) -> str | None:
        """Why agent `name` cannot take work needing `need` right now; None when it can."""
        agent = self.orch.agents[name]
        if not agent.enabled:
            return "disabled"
        if agent.adapter == "client":
            return None if need == "read" else "the client cannot be started by the bridge"
        if not agent.can(need):
            return f"cannot {need}"
        if not self._adapter(agent.adapter).available():
            return f"{agent.adapter} binary not found"
        try:
            account = self._account(agent.account or "")
        except ValueError as e:
            return str(e)
        state = self.store.account_state(account.name)["state"]
        if state != "available":
            return f"account {account.name} {state}"
        if self.store.active_on_account(account.name) >= self._slots(account.name):
            return f"account {account.name} busy"
        return None

    def _pick(self, role: str, need: str) -> str:
        """First eligible agent of `role` in configured order."""
        reasons = []
        for name in self.orch.role(role).agents:
            reason = self._unavailable(name, need)
            if reason is None:
                return name
            reasons.append(f"{name}: {reason}")
        raise ValueError(f"no eligible {role} agent ({'; '.join(reasons)})")

    def _executors_on(self, account: str) -> list[str]:
        ordered = dict.fromkeys([*self.orch.role("executor").agents, *self.orch.agents])
        return [n for n in ordered if self.orch.agents[n].account == account
                and self.orch.agents[n].enabled and self.orch.agents[n].can("write")]

    # ---- accounts and agents ---------------------------------------------------------------

    def list_accounts(self) -> dict[str, Any]:
        listing = (discover(self.cfg, self.root) if self.legacy else
                   discover(self.cfg, self.root, [self._account(n) for n in self.orch.accounts]))
        for account in listing["accounts"]:
            account["quota"] = self._account_quota(account["name"])
            state = self.store.account_state(account["name"])
            account.update(state=state["state"], last_error=state["last_error"],
                           max_concurrency=self._slots(account["name"]))
        return listing

    def list_agents(self) -> dict[str, Any]:
        roles: dict[str, list[str]] = {}
        for role in self.orch.roles:
            for name in self.orch.role(role).agents:
                roles.setdefault(name, []).append(role)
        agents = []
        for name, agent in self.orch.agents.items():
            reason = self._unavailable(name, "read")
            agents.append({
                "agent": name, "adapter": agent.adapter,
                "provider": ADAPTER_PROVIDERS[agent.adapter], "account": agent.account,
                "model": agent.model, "read": agent.can("read"), "write": agent.can("write"),
                "allow_bash": agent.allow_bash, "roles": roles.get(name, []),
                "available": reason is None, "unavailable_reason": reason,
            })
        return {"source": self.orch.source, "stages": self.orch.workflow.stages,
                "roles": {r: self.orch.role(r).model_dump() for r in self.orch.roles},
                "agents": agents}

    def clear_account(self, name: str) -> dict[str, Any]:
        account = self._account(name)
        self.store.set_account_state(account.name, "available", actor=Actor.CLIENT)
        return self.store.account_state(account.name)

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
        with self.store.tx() as c:
            row = self.store.get(task_id)
            if row["state"] not in REBINDABLE:
                raise TransitionError(f"cannot reassign account while task is {row['state']}")
            old_agent = self._agent_of(row)
            old_account = row["account"] or ("default" if self.legacy else None)
            data = dict(row["spec"])
            if data.get("agent"):
                data["agent"] = name
            if data.get("account"):
                data["account"] = new.name
            spec = TaskSpec.model_validate(data)
            # Sessions belong to the old account home and cannot move between accounts.
            c.execute("UPDATE tasks SET agent=?, account=?, spec=?, spec_hash=?, session_id=NULL,"
                      " updated_at=? WHERE id=?",
                      (name, new.name, spec.model_dump_json(),
                       hashlib.sha256(spec.model_dump_json().encode()).hexdigest(),
                       time.time(), task_id))
            moved = {"from": old_agent, "to": name}
            if old_agent != old_account or name != new.name:
                moved |= {"from_account": old_account, "to_account": new.name}
            self.store._event(c, task_id, actor, event_kind, detail={**moved, **detail})
        return self._summary(self.store.get(task_id))

    def _quota_fallback(self, task_id: str) -> None:
        """Opt-in: move a task off an account below the quota threshold. Never on AUTH."""
        policy = self.orch.routing.quota_fallback
        if not policy.enabled:
            return
        row = self.store.get(task_id)
        current = self._agent_of(row)
        if current is None:
            return
        account = self.orch.agent(current).account or ""
        state = self.store.account_state(account)["state"]
        if state == "auth":
            return
        remaining = 0 if state == "quota" else self._remaining(account)
        if remaining is None or remaining >= policy.min_remaining_pct:
            return
        for name in policy.agents or self.orch.role("executor").agents:
            candidate = self.orch.agents[name]
            if name == current or candidate.account == account:
                continue
            if self._unavailable(name, "write"):
                continue
            if login_status(self.cfg, self._account(candidate.account or "")) is not True:
                continue
            to_remaining = self._remaining(candidate.account or "")
            if to_remaining is not None and to_remaining < policy.min_remaining_pct:
                continue
            self._rebind(task_id, name, Actor.BRIDGE, "account_auto_switched", {
                "reason": "quota" if state == "quota" else "below_threshold",
                "remaining_pct": remaining, "to_remaining_pct": to_remaining,
            })
            return
        self.store.log(task_id, Actor.BRIDGE, "auto_switch_skipped", {
            "account": account, "remaining_pct": remaining, "reason": "no eligible fallback",
        })

    def _route(self, task_id: str) -> None:
        """Bind an unpinned task to the first eligible executor; later runs keep that agent."""
        row = self.store.get(task_id)
        if self._agent_of(row) is not None:
            return
        name = self._pick("executor", "write")
        account = self.orch.agents[name].account
        self.store.update(task_id, agent=name, account=account)
        self.store.log(task_id, Actor.BRIDGE, "agent_routed",
                       {"role": "executor", "agent": name, "account": account})

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
        counts: dict[str, int] = {}
        for r in self.store.list(limit=10_000):
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        active = [r["id"] for r in self.store.list(limit=10_000) if r["state"] in ACTIVE]
        quota = {}
        for name in self.orch.accounts:
            remaining = self._remaining(name)
            quota[name] = remaining if remaining is not None else "UNKNOWN"
        return {
            "project_root": str(self.root),
            "database": str(self.store.path),
            "state_dir": str(self.state_dir),
            "orchestrator": {"source": self.orch.source, "stages": self.orch.workflow.stages},
            "codex": codex,
            "adapters": adapters,
            "sandbox": self.cfg.sandbox,
            "quota": quota,
            "active_tasks": active,
            "task_counts": counts,
            "ok": all(a["path"] for a in adapters.values())
                  and ("codex-cli" not in used or codex.get("logged_in", False)),
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
        h = hashlib.sha256(s.model_dump_json().encode()).hexdigest()
        return self.store.create(s, h, agent=agent, workflow_id=workflow_id, account=account)

    async def create_task(self, spec: dict[str, Any]) -> dict[str, Any]:
        row, dup = self._insert(*self._prepare(spec))
        return {**self._summary(row), "duplicate": dup}

    async def execute_task(self, task_id: str, wait: bool = False) -> dict[str, Any]:
        row = self.store.get(task_id)
        if row["state"] == State.BACKLOG:
            raise TransitionError(f"{task_id} has unfinished dependencies: "
                                  f"{row['spec']['depends_on']}")
        self._quota_fallback(task_id)
        self._route(task_id)
        row = self.store.get(task_id)
        prompt = build_prompt(task_id, row["spec"], self.cfg.protected_paths)
        return await self._start(task_id, "execute", prompt, None, {State.READY}, wait)

    async def resume_task(self, task_id: str, feedback: str = "",
                          wait: bool = False) -> dict[str, Any]:
        row = self.store.get(task_id)
        expect = {State.REWORK, State.FAILED, State.PAUSED}
        if row["state"] not in expect:
            raise TransitionError(f"{task_id} is {row['state']}; resume needs one of "
                                  f"{sorted(expect)}")
        self._quota_fallback(task_id)
        self._route(task_id)
        row = self.store.get(task_id)
        if not feedback:
            reviews = [e for e in self.store.events(task_id, 200) if e["kind"] == "review"]
            feedback = (reviews[-1]["detail"] or {}).get("notes", "") if reviews else ""
        attempts = self.store.attempts(task_id)
        last_ver = attempts[-1].get("verification") if attempts else None
        if row["session_id"]:
            prompt = build_rework_prompt(task_id, feedback, last_ver)
        else:
            prompt = build_prompt(task_id, row["spec"], self.cfg.protected_paths)
            if feedback:
                prompt += f"\n\n## Feedback from the previous attempt\n{feedback}"
        return await self._start(task_id, "resume", prompt, row["session_id"], expect, wait)

    async def _start(self, task_id: str, kind: str, prompt: str, session: str | None,
                     expect: set[State], wait: bool) -> dict[str, Any]:
        row = self.store.get(task_id)
        if row["attempts"] >= self._max_attempts(row):
            row = self.store.transition(task_id, State.BLOCKED, Actor.BRIDGE,
                                        {"reason": "attempt budget exhausted"}, expect,
                                        failure="BLOCKED: attempt budget exhausted")
            return self._summary(row)
        agent = self.orch.agent(self._agent_of(row) or "")
        account = self._account(agent.account or "")
        availability = self.store.account_state(account.name)
        if availability["state"] != "available":
            raise ValueError(
                f"account {account.name} unavailable ({availability['state']}: "
                f"{availability['last_error']}). Ask the user; once they confirm it is resolved "
                f"run: codex-bridge account-clear {account.name}"
            )
        used = row["attempts"] + 1  # budget counter; environment failures give it back
        seq = len(self.store.attempts(task_id)) + 1  # attempt row number, never reused
        slots = self._slots(account.name)
        lock = AccountLock(account, self.cfg.lock_dir, slots)
        lock.acquire()
        try:
            row = self.store.transition(task_id, State.RUNNING, Actor.CLIENT, {"attempt": seq},
                                        expect, account_limit=slots, attempts=used,
                                        runner_pid=os.getpid(), failure=None)
            self.store.start_attempt(task_id, seq, kind)
            coro = self._run(task_id, seq, used, prompt, session, lock)
        except BaseException:
            lock.release()
            raise
        if wait:
            await coro
        else:
            self._runs[task_id] = asyncio.create_task(coro)
        return self._summary(self.store.get(task_id))

    async def _run(self, task_id: str, n: int, used: int, prompt: str,
                   session: str | None, lock: AccountLock) -> None:
        try:
            try:
                await self._run_inner(task_id, n, used, prompt, session)
            except Exception as e:  # never leave a task stuck in RUNNING/VERIFYING
                self.store.log(task_id, Actor.BRIDGE, "error", {"error": redact(repr(e), 2000)})
                with contextlib.suppress(Exception):
                    row = self.store.get(task_id)
                    if row["state"] in ACTIVE:
                        self.store.transition(
                            task_id, State.FAILED if row["state"] == State.RUNNING
                            else State.PAUSED, Actor.BRIDGE, {"error": repr(e)},
                            failure=f"BRIDGE_ERROR: {redact(repr(e), 500)}")
            finally:
                lock.release()
            if self.store.get(task_id)["state"] == State.REVIEW and not self._stopping:
                await self._review_stage(task_id)
        finally:
            self._runs.pop(task_id, None)

    async def _run_inner(self, task_id: str, n: int, used: int, prompt: str,
                         session: str | None) -> None:
        row = self.store.get(task_id)
        spec, max_att = row["spec"], self._max_attempts(row)
        agent = self.orch.agent(self._agent_of(row) or "")
        account = self._account(agent.account or "")
        adapter = self._adapter(agent.adapter)
        sandbox = (spec.get("sandbox") or self.cfg.sandbox) if agent.writes else "read-only"
        before = snapshot(self.root, self.cfg.protected_paths)

        def cancelled() -> bool:
            return self._stopping or self.store.get(task_id)["state"] == State.CANCELLED

        retries = 0
        while True:
            res: RunResult = await adapter.run(
                RunRequest(prompt, account, sandbox, self._timeout(agent, spec.get("timeout_s")),
                           session_id=session, model=agent.model, allow_bash=agent.allow_bash),
                on_start=lambda pid: self.store.update(task_id, codex_pid=pid),
                on_session=lambda sid: self.store.update(task_id, session_id=sid),
                should_cancel=cancelled,
            )
            if res.session_id:
                session = res.session_id
                self.store.update(task_id, session_id=session, codex_pid=None)
            if res.outcome == "network" and retries < self.cfg.network_retries:
                retries += 1
                self.store.log(task_id, Actor.BRIDGE, "retry", {"error": res.error, "n": retries})
                await asyncio.sleep(self.cfg.retry_backoff_s)
                continue
            break

        self._record_account(account.name, res)
        changes = diff(self.root, before, self.cfg.protected_paths)
        record = dict(exit_code=res.exit_code, outcome=res.outcome, session_id=res.session_id,
                      final=res.final or {"raw": res.final_text[-3000:]}, usage=res.usage,
                      stderr_tail=res.stderr_tail, changes=changes)

        if res.outcome == "cancelled":
            self.store.finish_attempt(task_id, n, **record)
            if self._stopping and self.store.get(task_id)["state"] in ACTIVE:
                self.store.transition(task_id, State.PAUSED, Actor.BRIDGE,
                                      {"reason": "bridge shutdown"},
                                      failure="INTERRUPTED: bridge shut down during execution")
            return
        if res.outcome in PAUSE_OUTCOMES:
            self.store.finish_attempt(task_id, n, **record)
            # The attempt did not consume work budget when the environment failed.
            self.store.transition(task_id, State.PAUSED, Actor.BRIDGE, {"error": res.error},
                                  failure=f"{res.outcome.upper()}: {res.error}"[:1000],
                                  attempts=used - 1)
            return
        if res.outcome != "ok":  # timeout / crash
            self.store.finish_attempt(task_id, n, **record)
            dst = State.BLOCKED if used >= max_att else State.FAILED
            self.store.transition(task_id, dst, Actor.BRIDGE, {"outcome": res.outcome},
                                  failure=f"{res.outcome.upper()}: {res.error}"[:1000])
            return

        self.store.transition(task_id, State.VERIFYING, Actor.BRIDGE, {"attempt": n})
        violations = scope_violations(changes["files"], spec, self.cfg, changes["head_changed"])
        checks = (await run_verification(spec.get("verification_commands", []), self.root,
                                         self.cfg)
                  if "test" in self.orch.workflow.stages else [])
        agent_status = (res.final or {}).get("status", "unknown")
        passed = not violations and all(c["passed"] for c in checks) and agent_status == "completed"
        record["verification"] = {"passed": passed, "scope_violations": violations,
                                  "checks": checks, "agent_status": agent_status}
        self.store.finish_attempt(task_id, n, **record)
        needs_human = bool(row["needs_human"]) or touches_sensitive(changes["files"], self.cfg)
        if self.store.get(task_id)["state"] != State.VERIFYING:
            return  # cancelled while verifying
        if passed:
            dst, failure = State.REVIEW, None
        else:
            dst = State.REWORK if used < max_att else State.BLOCKED
            failure = "; ".join(violations + [f"check failed: {c['command']}" for c in checks
                                              if not c["passed"]]
                                + ([f"agent status: {agent_status}"]
                                   if agent_status != "completed" else []))[:1000]
        self.store.transition(task_id, dst, Actor.BRIDGE, record["verification"],
                              failure=failure, needs_human=int(needs_human))

    # ---- review ----------------------------------------------------------------------------

    async def _consult(self, name: str, prompt: str, schema: dict[str, Any],
                       task_id: str | None, cancel: Callable[[], bool]) -> RunResult | None:
        """One read-only run of a planner or reviewer agent; None when its account is busy."""
        agent = self.orch.agents[name]
        account = self._account(agent.account or "")
        lock = AccountLock(account, self.cfg.lock_dir, self._slots(account.name))
        try:
            lock.acquire()
        except ConflictError as e:
            self.store.log(task_id, Actor.BRIDGE, "agent_skipped",
                           {"agent": name, "reason": str(e)})
            return None
        try:
            res = await self._adapter(agent.adapter).run(
                RunRequest(prompt, account, "read-only", self._timeout(agent, None),
                           schema=schema, model=agent.model),
                should_cancel=cancel)
        finally:
            lock.release()
        self._record_account(account.name, res)
        return res

    async def _review_stage(self, task_id: str) -> None:
        try:
            if "review" not in self.orch.workflow.stages:
                self._apply_review(task_id, "approve", "Deterministic gates passed; the workflow "
                                   "has no review stage.", Actor.BRIDGE, "bridge")
                return
            try:
                name = self._pick("reviewer", "read")
            except ValueError as e:
                self.store.log(task_id, Actor.BRIDGE, "agent_review_skipped", {"reason": str(e)})
                return
            if self.orch.agents[name].adapter == "client":
                return  # the MCP client reviews through bridge_review_task
            row = self.store.get(task_id)
            prompt = build_review_prompt(task_id, row["spec"], self.store.attempts(task_id)[-1])
            res = await self._consult(
                name, prompt, REVIEW_SCHEMA, task_id,
                lambda: self._stopping or self.store.get(task_id)["state"] != State.REVIEW)
            if res is None:
                return
            try:
                if res.outcome != "ok":
                    raise ValueError(f"{res.outcome}: {res.error}")
                final = res.final or {}
                d = ReviewDecision(decision=final.get("decision"), notes=final.get("notes") or "")
            except (ValueError, ValidationError) as e:
                self.store.log(task_id, Actor.BRIDGE, "agent_review_failed",
                               {"agent": name, "error": redact(str(e), 1000)})
                return
            if self.store.get(task_id)["state"] == State.REVIEW:
                self._apply_review(task_id, d.decision, d.notes, Actor.AGENT, name)
        except Exception as e:  # the task stays in REVIEW; the client can still decide
            self.store.log(task_id, Actor.BRIDGE, "error", {"error": redact(repr(e), 2000)})

    def _apply_review(self, task_id: str, decision: str, notes: str, actor: Actor,
                      reviewer: str | None) -> dict[str, Any]:
        d = ReviewDecision(decision=decision, notes=notes)  # type: ignore[arg-type]
        row = self.store.get(task_id)
        if row["state"] != State.REVIEW:
            raise TransitionError(f"{task_id} is {row['state']}; review needs REVIEW")
        detail = d.model_dump() | ({"reviewer": reviewer} if reviewer else {})
        self.store.log(task_id, actor, "review", detail)
        if d.decision == "rework":
            row = self.store.transition(task_id, State.REWORK, actor, detail)
        elif d.decision == "block":
            row = self.store.transition(task_id, State.BLOCKED, actor, detail,
                                        failure=f"BLOCKED by review: {d.notes}"[:500])
        else:
            self._assert_gates(task_id, need_review=False)
            row = self.store.transition(task_id, State.APPROVED, actor, detail)
            if not row["needs_human"]:
                row = self._close(task_id, Actor.BRIDGE)
        out = self._summary(row)
        if row["state"] == State.APPROVED:
            out["next"] = (f"Human approval required. A person must run, in an interactive "
                           f"terminal: BRIDGE_PROJECT_ROOT={shlex.quote(str(self.root))} "
                           f"uv run --project {shlex.quote(str(Path(__file__).resolve().parents[1]))} "
                           f"codex-bridge approve {task_id}")
        return out

    async def review_task(self, task_id: str, decision: str, notes: str) -> dict[str, Any]:
        return self._apply_review(task_id, decision, notes, Actor.CLIENT, None)

    def _assert_gates(self, task_id: str, need_review: bool = True) -> None:
        attempts = self.store.attempts(task_id)
        last = attempts[-1] if attempts else {}
        ver = last.get("verification") or {}
        if last.get("outcome") != "ok" or not ver.get("passed"):
            raise TransitionError(f"{task_id}: quality gate failed (last attempt not verified)")
        if need_review and not any(
            e["kind"] == "review" and (e["detail"] or {}).get("decision") == "approve"
            for e in self.store.events(task_id, 500)
        ):
            raise TransitionError(f"{task_id}: quality gate failed (no approving review)")

    def _close(self, task_id: str, actor: Actor) -> dict[str, Any]:
        self._assert_gates(task_id)
        return self.store.transition(task_id, State.DONE, actor, {"gates": "passed"})

    def human_approve(self, task_id: str) -> dict[str, Any]:
        """Only reachable from the interactive CLI (`codex-bridge approve`), never from MCP."""
        row = self.store.get(task_id)
        if row["state"] != State.APPROVED:
            raise TransitionError(f"{task_id} is {row['state']}; human approval needs APPROVED")
        self.store.log(task_id, Actor.HUMAN, "human_approval", {"tty": True})
        return self._summary(self._close(task_id, Actor.HUMAN))

    # ---- task queries ----------------------------------------------------------------------

    async def cancel_task(self, task_id: str, reason: str) -> dict[str, Any]:
        row = self.store.transition(task_id, State.CANCELLED, Actor.CLIENT, {"reason": reason},
                                    failure=f"CANCELLED: {reason}"[:500])
        if row["runner_pid"] != os.getpid() and not _alive(row["runner_pid"]):
            kill_stale(row["codex_pid"])  # orphan from a dead runner
        task = self._runs.get(task_id)
        if task:  # the runner polls the state and kills the process group within ~1s
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 15)
        return self._summary(self.store.get(task_id))

    async def task_status(self, task_id: str, wait_s: float = 0) -> dict[str, Any]:
        deadline = time.monotonic() + min(max(wait_s, 0), 55)
        row = self.store.get(task_id)
        while row["state"] in ACTIVE and time.monotonic() < deadline:
            await asyncio.sleep(1)
            row = self.store.get(task_id)
        out = self._summary(row)
        attempts = self.store.attempts(task_id)
        if attempts:
            a = attempts[-1]
            out["last_attempt"] = {"n": a["n"], "outcome": a["outcome"],
                                   "changed_files": (a.get("changes") or {}).get("files"),
                                   "verification_passed": (a.get("verification") or {}).get(
                                       "passed")}
        return out

    async def task_result(self, task_id: str) -> dict[str, Any]:
        row = self.store.get(task_id)
        attempts = []
        for a in self.store.attempts(task_id):
            attempts.append({k: a.get(k) for k in (
                "n", "kind", "outcome", "exit_code", "session_id", "final", "usage", "changes",
                "verification", "stderr_tail")})
        return {
            **self._summary(row),
            "spec": row["spec"],
            "attempts": attempts,
            "reviews": [e["detail"] for e in self.store.events(task_id, 200)
                        if e["kind"] == "review"],
        }

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
                raise ValueError(f"planner {name} failed: "
                                 f"{res.outcome + ': ' + res.error if res else 'account busy'}")
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
                "tasks": [self._summary(r) for r in self.store.workflow_tasks(wid)]}

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
        try:
            self.store.set_workflow(wid, "RUNNING")
            while not self._stopping:
                step = next((t for t in self.store.workflow_tasks(wid)
                             if t["id"] not in waiting and self._actionable(t)), None)
                if step is None:
                    break
                try:
                    if step["state"] == State.READY:
                        await self.execute_task(step["id"], wait=True)
                    else:
                        await self.resume_task(step["id"], wait=True)
                except (ValueError, ConflictError) as e:
                    waiting[step["id"]] = redact(str(e), 500)
            states = {t["id"]: t["state"] for t in self.store.workflow_tasks(wid)}
            if states and all(s == State.DONE for s in states.values()):
                final = "DONE"
            elif all(s in TERMINAL for s in states.values()):
                final = "INCOMPLETE"
            else:
                final = "WAITING"  # a review, a human approval or an account needs attention
            self.store.set_workflow(wid, final, {"tasks": states, "waiting": waiting})
        except Exception as e:
            self.store.set_workflow(wid, "WAITING", {"error": redact(repr(e), 2000)})
        finally:
            self._workflows.pop(wid, None)

    # ---- lifecycle -------------------------------------------------------------------------

    def recover(self) -> list[str]:
        """Park tasks whose runner process died mid-run (crash, kill, closed session)."""
        recovered = []
        for row in self.store.list(limit=10_000):
            if row["state"] not in ACTIVE:
                continue
            if row["runner_pid"] != os.getpid() and _alive(row["runner_pid"]):
                continue  # another live process (e.g. the CLI) owns this run
            killed = kill_stale(row["codex_pid"])
            for a in self.store.attempts(row["id"]):
                if a["ended_at"] is None:
                    self.store.finish_attempt(row["id"], a["n"], outcome="interrupted")
            self.store.transition(row["id"], State.PAUSED, Actor.BRIDGE,
                                  {"reason": "runner died", "killed_orphan": killed},
                                  failure="INTERRUPTED: runner process exited during execution")
            recovered.append(row["id"])
        return recovered

    async def shutdown(self) -> None:
        self._stopping = True
        pending = [*self._runs.values(), *self._workflows.values()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)
