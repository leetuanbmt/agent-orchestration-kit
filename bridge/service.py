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
from pathlib import Path
from typing import Any

from .accounts import AccountLock, discover, login_status, resolve
from .config import AI_DIR, BridgeConfig, ensure_ai_dir, find_root, load_config, state_dir
from .executor import CodexAdapter, RunResult, build_prompt, build_rework_prompt, kill_stale
from .models import ACTIVE, Actor, ReviewDecision, State, TaskSpec, TransitionError
from .quota import codex_home_for, read_quota
from .review import diff, parse_command, run_verification, scope_violations, snapshot, \
    touches_sensitive
from .security import PathError, redact, validate_pattern
from .state import Store

# Outcomes that mean "the environment, not the code": park the task, never auto-retry.
PAUSE_OUTCOMES = {"quota", "auth", "network", "missing_binary"}


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


class Bridge:
    def __init__(self, root: str | Path | None = None, cfg: BridgeConfig | None = None):
        self.root = find_root(root)
        self.cfg = cfg or load_config(self.root)
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
        self.adapter = CodexAdapter(self.cfg, self.root)
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    # ---- helpers ---------------------------------------------------------------------------

    def _summary(self, row: dict[str, Any]) -> dict[str, Any]:
        spec = row["spec"]
        return {
            "task_id": row["id"],
            "account": row["account"] or "default",
            "state": row["state"],
            "objective": spec["objective"][:200],
            "attempts": row["attempts"],
            "max_attempts": spec.get("max_attempts") or self.cfg.max_attempts,
            "session_id": row["session_id"],
            "failure": row["failure"],
            "needs_human_approval": bool(row["needs_human"]),
            "updated_at": row["updated_at"],
        }

    def _max_attempts(self, row: dict[str, Any]) -> int:
        return int(row["spec"].get("max_attempts") or self.cfg.max_attempts)

    def _account_quota(self, name: str) -> dict[str, Any] | str:
        try:
            account = resolve(self.cfg, self.root, name)
        except ValueError:
            return "UNKNOWN"
        return read_quota(codex_home_for(account)) or "UNKNOWN"

    # ---- tools -----------------------------------------------------------------------------

    def list_accounts(self) -> dict[str, Any]:
        listing = discover(self.cfg, self.root)
        for account in listing["accounts"]:
            account["quota"] = self._account_quota(account["name"])
            state = self.store.account_state(account["name"])
            account.update(state=state["state"], last_error=state["last_error"])
        return listing

    def clear_account(self, name: str) -> dict[str, Any]:
        account = resolve(self.cfg, self.root, name)
        self.store.set_account_state(account.name, "available", actor=Actor.CLAUDE)
        return self.store.account_state(account.name)

    def reassign_account(self, task_id: str, account: str) -> dict[str, Any]:
        return self._rebind_account(task_id, account, Actor.HUMAN, "account_reassigned", {})

    def _rebind_account(self, task_id: str, new_account_name: str, actor: Actor,
                        event_kind: str, detail: dict[str, Any]) -> dict[str, Any]:
        new = resolve(self.cfg, self.root, new_account_name)
        with self.store.tx() as c:
            row = self.store.get(task_id)
            if row["state"] not in {State.READY, State.REWORK, State.FAILED, State.PAUSED}:
                raise TransitionError(f"cannot reassign account while task is {row['state']}")
            spec = TaskSpec.model_validate({**row["spec"], "account": new.name})
            # Sessions belong to the old CODEX_HOME and cannot move between accounts.
            c.execute("UPDATE tasks SET account=?, spec=?, spec_hash=?, session_id=NULL, "
                      "updated_at=? WHERE id=?",
                      (new.name, spec.model_dump_json(),
                       hashlib.sha256(spec.model_dump_json().encode()).hexdigest(),
                       time.time(), task_id))
            self.store._event(c, task_id, actor, event_kind,
                              detail={"from": row["account"] or "default", "to": new.name,
                                      **detail})
        return self._summary(self.store.get(task_id))

    def _auto_switch(self, task_id: str) -> None:
        policy = self.cfg.auto_switch
        if not policy.enabled:
            return
        row = self.store.get(task_id)
        current = row["account"] or "default"
        state = self.store.account_state(current)["state"]
        if state == "auth":
            return
        quota = self._account_quota(current) if state != "quota" else None
        remaining = (0 if state == "quota" else
                     quota.get("remaining_pct") if isinstance(quota, dict) else None)
        if remaining is None or remaining >= policy.min_remaining_pct:
            return
        for name in policy.fallback_accounts:
            if name == current:
                continue
            try:
                account = resolve(self.cfg, self.root, name)
            except ValueError:
                continue
            if self.store.account_state(name)["state"] != "available":
                continue
            if login_status(self.cfg, account) is not True:
                continue
            quota = self._account_quota(name)
            candidate_remaining = quota.get("remaining_pct") if isinstance(quota, dict) else None
            if candidate_remaining is not None and candidate_remaining < policy.min_remaining_pct:
                continue
            if self.store.db.execute(
                f"SELECT 1 FROM tasks WHERE COALESCE(account, 'default')=? "
                f"AND state IN ({','.join('?' * len(ACTIVE))}) LIMIT 1",
                (name, *ACTIVE),
            ).fetchone():
                continue
            self._rebind_account(task_id, name, Actor.BRIDGE, "account_auto_switched", {
                "reason": "quota" if state == "quota" else "below_threshold",
                "remaining_pct": remaining, "to_remaining_pct": candidate_remaining,
            })
            return
        self.store.log(task_id, Actor.BRIDGE, "auto_switch_skipped", {
            "account": current, "remaining_pct": remaining, "reason": "no eligible fallback",
        })

    async def health(self) -> dict[str, Any]:
        codex: dict[str, Any] = {"path": self.adapter.available()}
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
        counts: dict[str, int] = {}
        for r in self.store.list(limit=10_000):
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        active = [r["id"] for r in self.store.list(limit=10_000) if r["state"] in ACTIVE]
        quota = {}
        for name in ["default", *sorted(self.cfg.accounts)]:
            snapshot = self._account_quota(name)
            remaining = snapshot["remaining_pct"] if isinstance(snapshot, dict) else None
            quota[name] = remaining if remaining is not None else "UNKNOWN"
        return {
            "project_root": str(self.root),
            "database": str(self.store.path),
            "state_dir": str(self.state_dir),
            "codex": codex,
            "sandbox": self.cfg.sandbox,
            "quota": quota,
            "active_tasks": active,
            "task_counts": counts,
            "ok": bool(codex["path"]) and codex.get("logged_in", False),
        }

    async def create_task(self, spec: dict[str, Any]) -> dict[str, Any]:
        s = TaskSpec.model_validate(spec)
        s.sandbox = s.sandbox or self.cfg.sandbox
        s.account = resolve(self.cfg, self.root, s.account).name
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
        h = hashlib.sha256(s.model_dump_json().encode()).hexdigest()
        row, dup = self.store.create(s, h)
        return {**self._summary(row), "duplicate": dup}

    async def execute_task(self, task_id: str, wait: bool = False) -> dict[str, Any]:
        row = self.store.get(task_id)
        if row["state"] == State.BACKLOG:
            raise TransitionError(f"{task_id} has unfinished dependencies: "
                                  f"{row['spec']['depends_on']}")
        self._auto_switch(task_id)
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
        self._auto_switch(task_id)
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
        account = resolve(self.cfg, self.root, row["account"] or "default")
        availability = self.store.account_state(account.name)
        if availability["state"] != "available":
            raise ValueError(
                f"account {account.name} unavailable ({availability['state']}: "
                f"{availability['last_error']}). Ask the user; once they confirm it is resolved "
                f"run: codex-bridge account-clear {account.name}"
            )
        used = row["attempts"] + 1  # budget counter; environment failures give it back
        seq = len(self.store.attempts(task_id)) + 1  # attempt row number, never reused
        lock = AccountLock(account, self.cfg.lock_dir)
        lock.acquire()
        try:
            row = self.store.transition(task_id, State.RUNNING, Actor.CLAUDE, {"attempt": seq},
                                        expect, attempts=used, runner_pid=os.getpid(), failure=None)
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
            await self._run_inner(task_id, n, used, prompt, session)
        except Exception as e:  # never leave a task stuck in RUNNING/VERIFYING
            self.store.log(task_id, Actor.BRIDGE, "error", {"error": redact(repr(e), 2000)})
            with contextlib.suppress(Exception):
                row = self.store.get(task_id)
                if row["state"] in ACTIVE:
                    self.store.transition(task_id, State.FAILED if row["state"] == State.RUNNING
                                          else State.PAUSED, Actor.BRIDGE, {"error": repr(e)},
                                          failure=f"BRIDGE_ERROR: {redact(repr(e), 500)}")
        finally:
            lock.release()
            self._runs.pop(task_id, None)

    async def _run_inner(self, task_id: str, n: int, used: int, prompt: str,
                         session: str | None) -> None:
        row = self.store.get(task_id)
        spec, max_att = row["spec"], self._max_attempts(row)
        sandbox = spec.get("sandbox") or self.cfg.sandbox
        timeout = min(spec.get("timeout_s") or self.cfg.default_timeout_s, self.cfg.max_timeout_s)
        before = snapshot(self.root, self.cfg.protected_paths)

        def cancelled() -> bool:
            return self._stopping or self.store.get(task_id)["state"] == State.CANCELLED

        account = resolve(self.cfg, self.root, row["account"] or "default")
        retries = 0
        while True:
            res: RunResult = await self.adapter.run(
                prompt, account=account, sandbox=sandbox, timeout_s=timeout, session_id=session,
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

        if res.outcome in {"quota", "auth", "ok"}:
            self.store.set_account_state(
                account.name, "available" if res.outcome == "ok" else res.outcome,
                res.error if res.outcome != "ok" else None, actor=Actor.BRIDGE,
            )
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
        checks = await run_verification(spec.get("verification_commands", []), self.root, self.cfg)
        codex_status = (res.final or {}).get("status", "unknown")
        passed = not violations and all(c["passed"] for c in checks) and codex_status == "completed"
        record["verification"] = {"passed": passed, "scope_violations": violations,
                                  "checks": checks, "codex_status": codex_status}
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
                                + ([f"codex status: {codex_status}"]
                                   if codex_status != "completed" else []))[:1000]
        self.store.transition(task_id, dst, Actor.BRIDGE, record["verification"],
                              failure=failure, needs_human=int(needs_human))

    async def cancel_task(self, task_id: str, reason: str) -> dict[str, Any]:
        row = self.store.transition(task_id, State.CANCELLED, Actor.CLAUDE, {"reason": reason},
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

    async def review_task(self, task_id: str, decision: str, notes: str) -> dict[str, Any]:
        d = ReviewDecision(decision=decision, notes=notes)  # type: ignore[arg-type]
        row = self.store.get(task_id)
        if row["state"] != State.REVIEW:
            raise TransitionError(f"{task_id} is {row['state']}; review needs REVIEW")
        self.store.log(task_id, Actor.CLAUDE, "review", d.model_dump())
        if d.decision == "rework":
            row = self.store.transition(task_id, State.REWORK, Actor.CLAUDE, d.model_dump())
        elif d.decision == "block":
            row = self.store.transition(task_id, State.BLOCKED, Actor.CLAUDE, d.model_dump(),
                                        failure=f"BLOCKED by review: {d.notes}"[:500])
        else:
            self._assert_gates(task_id, need_review=False)
            row = self.store.transition(task_id, State.APPROVED, Actor.CLAUDE, d.model_dump())
            if not row["needs_human"]:
                row = self._close(task_id, Actor.BRIDGE)
        out = self._summary(row)
        if row["state"] == State.APPROVED:
            out["next"] = (f"Human approval required. A person must run, in an interactive "
                           f"terminal: BRIDGE_PROJECT_ROOT={shlex.quote(str(self.root))} "
                           f"uv run --project {shlex.quote(str(Path(__file__).resolve().parents[1]))} "
                           f"codex-bridge approve {task_id}")
        return out

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
        if self._runs:
            await asyncio.gather(*self._runs.values(), return_exceptions=True)


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)
