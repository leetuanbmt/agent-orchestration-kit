"""Task runner: start a task's agent run, verify the result and hand it to review."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .accounts import AccountLock, account_key
from .executor import REVIEW_SCHEMA, RunRequest, RunResult, build_review_prompt, describe_event
from .models import ACTIVE, Actor, ReviewDecision, State, leftover_failure, run_failure_state, \
    verdict
from .review import EvidenceError, content_hashes, current_head, diff, protected_after, recheck, \
    run_verification, scope_violations, touches_sensitive
from .security import process_started, redact
from .state import LimitExceeded

if TYPE_CHECKING:
    from .service import Bridge
    from .state import Store


def _write_attempt_log_line(log: Any, stream: str, line: str) -> None:
    log.write(json.dumps({"ts": time.time(), "stream": stream,
                          "line": redact(line, 100000)}, ensure_ascii=False) + "\n")
    log.flush()


class TaskRunner:
    """One task run: the start gate, the agent run, verification and the review stage.

    Everything else comes from the Bridge. The account gate is reached through the Bridge's
    delegates where it has one (`_reserve`, `_record_account`, `_settle_open`, `_pick`,
    `_limit_report`), so a method replaced on the Bridge instance applies to runs too."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    @property
    def store(self) -> Store:
        return self.bridge.store

    async def _start(self, task_id: str, kind: str, prompt: str, session: str | None,
                     expect: set[State], wait: bool, actor: Actor) -> dict[str, Any]:
        b = self.bridge
        row = self.store.get(task_id)
        if row["attempts"] >= b._max_attempts(row):
            row = self.store.transition(task_id, State.BLOCKED, Actor.BRIDGE,
                                        {"reason": "attempt budget exhausted"}, expect,
                                        failure="BLOCKED: attempt budget exhausted")
            return b._summary(row)
        agent = b.orch.agent(b._agent_of(row) or "")
        account = b._account(agent.account or "")
        availability = b.gate._refresh_account_marks(account.name, key=account_key(account))
        if availability["state"] != "available":
            raise ValueError(
                f"account {account.name} unavailable ({availability['state']}: "
                f"{availability['last_error']}). Ask the user; once they confirm it is resolved "
                f"run: agent-bridge account-clear {account.name}"
            )
        if bad := b.gate._unscopable(agent, row["spec"]):
            raise ValueError(
                f"claude-cli cannot scope writes to {bad}: Claude permission rules cannot hold "
                "',', '(', ')', quotes or whitespace. Rename the paths, or ask the user to "
                f"run: agent-bridge reassign {task_id} <codex agent>")
        b._hook_settings(agent)
        used = row["attempts"] + 1  # budget counter; environment failures give it back
        seq = len(self.store.attempts(task_id)) + 1  # attempt row number, never reused
        slots = b._slots(account.name)
        lock = AccountLock(account, b.cfg.lock_dir, slots)
        lock.acquire()
        try:
            usage_id = b._reserve(account.name, task_id)
        except LimitExceeded as e:
            try:
                self.store.log(task_id, Actor.BRIDGE, "limit_blocked",
                               {"account": account.name, "reason": str(e)})
            finally:
                lock.release()
            raise LimitExceeded(
                f"account {account.name} over its configured limit: {e}. It takes work again "
                "after the reset; with routing.quota_fallback enabled the bridge moves the task "
                "to another eligible agent; a human can change the limit in "
                ".ai/orchestrator.yaml") from None
        except BaseException:
            lock.release()
            raise
        try:
            row = self.store.transition(task_id, State.RUNNING, actor, {"attempt": seq},
                                        expect, account_limit=slots, attempts=used,
                                        runner_pid=os.getpid(),
                                        runner_started=self.store._started, failure=None)
            self.store.start_attempt(task_id, seq, kind)
            coro = self._run(task_id, seq, used, prompt, session, lock, usage_id)
        except BaseException:
            b._settle_open(usage_id, 0.0, "actual")  # the run never started
            lock.release()
            raise
        if wait:
            await coro
        else:
            b._runs[task_id] = asyncio.create_task(coro)
        return b._summary(self.store.get(task_id))

    async def _run(self, task_id: str, n: int, used: int, prompt: str,
                   session: str | None, lock: AccountLock, usage_id: int | None = None) -> None:
        b = self.bridge
        try:
            try:
                await self._run_inner(task_id, n, used, prompt, session, lock, usage_id)
            except EvidenceError as e:  # no evidence, no verdict: pause, never pass or fail
                self._park_without_evidence(task_id, n, used, e)
            except Exception as e:  # never leave a task stuck in RUNNING/VERIFYING
                with contextlib.suppress(Exception):
                    self.store.log(task_id, Actor.BRIDGE, "error",
                                   {"error": redact(repr(e), 2000)})
                await self._rescue(task_id, e)
            finally:
                # A bridge error before the cost was known: fail closed (unknown cost). A run
                # that never reached the agent was already settled 0.0 by _run_inner.
                b._settle_open(usage_id, None, "unavailable")
                lock.release()
                # Tells a cancel_task in another process that this run let go of the account.
                with contextlib.suppress(Exception):
                    self.store.update(task_id, runner_pid=None, runner_started=None)
            if self.store.get(task_id)["state"] == State.REVIEW and not b._stopping:
                await self._review_stage(task_id)
        finally:
            b._runs.pop(task_id, None)

    async def _run_inner(self, task_id: str, n: int, used: int, prompt: str,
                         session: str | None, lock: AccountLock,
                         usage_id: int | None = None) -> None:
        b = self.bridge
        try:
            row = self.store.get(task_id)
            spec, max_att = row["spec"], b._max_attempts(row)
            agent = b.orch.agent(b._agent_of(row) or "")
            account = b._account(agent.account or "")
            adapter = b._adapter(agent.adapter)
            sandbox = (spec.get("sandbox") or b.cfg.sandbox) if agent.writes else "read-only"
            before = b._snapshot()
            self.store.set_baseline(task_id, n, {k: before[k] for k in ("head", "tree", "files")})
            log_path = b._attempt_log(task_id, n)
            log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(log_path.parent, 0o700)
            if b.progress:
                with contextlib.suppress(Exception):
                    b.progress(f"[{task_id} #{n}] log: {log_path}")
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.chmod(log_path, 0o600)
            log_file = os.fdopen(fd, "a", encoding="utf-8")
        except BaseException:
            b._settle_open(usage_id, 0.0, "actual")  # the agent never started
            raise

        def cancelled() -> bool:
            return b._stopping or self.store.get(task_id)["state"] == State.CANCELLED

        retries = 0
        with log_file as log:
            def on_line(stream: str, line: str) -> None:
                _write_attempt_log_line(log, stream, line)
                if stream == "stdout" and b.progress:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        return
                    if isinstance(ev, dict) and (summary := describe_event(ev)):
                        with contextlib.suppress(Exception):
                            b.progress(f"[{task_id} #{n}] {summary}")

            recorded = False  # the last attempt already has its own ledger row
            while True:
                res: RunResult = await adapter.run(
                    RunRequest(prompt, account, sandbox, b._timeout(agent, spec.get("timeout_s")),
                               session_id=session, model=agent.model, allow_bash=agent.allow_bash,
                               hook_settings=b.cfg.claude_hook_settings
                               if agent.adapter == "claude-cli" else None,
                               write_paths=spec["allowed_paths"],
                               deny_paths=[*spec.get("excluded_paths", []),
                                           *b.cfg.protected_paths],
                               read_deny=b._account_homes(),
                               keep_fds=() if (lock_fd := lock.fileno()) is None else (lock_fd,)),
                    on_start=lambda pid: self.store.update(task_id, codex_pid=pid,
                                                            codex_started=process_started(pid)),
                    on_session=lambda sid: self.store.update(task_id, session_id=sid),
                    should_cancel=cancelled, on_line=on_line,
                )
                if res.session_id:
                    session = res.session_id
                    self.store.update(task_id, session_id=session, codex_pid=None,
                                      codex_started=None)
                if res.outcome == "network" and retries < b.cfg.network_retries:
                    # A failed attempt still costs: charge it on its own row, then retry only
                    # while the account stays within its limits.
                    charged = b._record_account(account.name, res, None, task_id)
                    report = b._limit_report(account.name)
                    if report["status"] == "limited":
                        self.store.log(task_id, Actor.BRIDGE, "limit_blocked",
                                       {"account": account.name, "reason": report["reason"],
                                        "retry": True})
                        recorded = charged
                        break
                    retries += 1
                    self.store.log(task_id, Actor.BRIDGE, "retry", {"error": res.error,
                                                                     "n": retries})
                    await asyncio.sleep(b.cfg.retry_backoff_s)
                    continue
                break

        if recorded:
            # Charged above on its own row: release the reservation instead of charging twice.
            b._settle_open(usage_id, 0.0, "actual")
        else:
            b._record_account(account.name, res, usage_id, task_id)
        changes = diff(b.root, before, b.cfg.protected_paths)
        record = dict(exit_code=res.exit_code, outcome=res.outcome, session_id=res.session_id,
                      final=res.final or {"raw": res.final_text[-3000:]}, usage=res.usage,
                      stderr_tail=res.stderr_tail, changes=changes)

        # Verification-only ignored changes must gate when nobody reviews the evidence.
        enforce = "review" not in b.orch.workflow.stages
        if res.outcome != "ok":
            # An attempt that ends early is never verified, and whatever it changed becomes the
            # next attempt's baseline: changes out of scope must stop the task now.
            leftover = scope_violations(changes["files"], spec, b.cfg, changes["head_changed"],
                                        changes["ignored_files"], root=b.root)
            if leftover:
                record["verification"] = {"passed": False, "scope_violations": leftover,
                                          "checks": [], "interrupted": True,
                                          **protected_after(b.root, leftover)}
            self.store.finish_attempt(task_id, n, **record)
            if leftover and self.store.get(task_id)["state"] in ACTIVE:
                self.store.transition(task_id, State.BLOCKED, Actor.BRIDGE,
                                      {"outcome": res.outcome, "scope_violations": leftover},
                                      failure=leftover_failure(leftover))
                return
        if res.outcome == "cancelled":
            self._park_if_stopping(task_id, used)
            return
        if res.outcome != "ok":
            dst = run_failure_state(res.outcome, used, max_att)
            failure = f"{res.outcome.upper()}: {res.error}"[:1000]
            if dst == State.PAUSED:
                # The attempt did not consume work budget when the environment failed.
                self.store.transition(task_id, dst, Actor.BRIDGE, {"error": res.error},
                                      failure=failure, attempts=used - 1)
            else:  # timeout / crash
                self.store.transition(task_id, dst, Actor.BRIDGE, {"outcome": res.outcome},
                                      failure=failure)
            return

        self.store.transition(task_id, State.VERIFYING, Actor.BRIDGE, {"attempt": n})
        violations = scope_violations(changes["files"], spec, b.cfg, changes["head_changed"],
                                      changes["ignored_files"], root=b.root)
        agent_status = (res.final or {}).get("status", "unknown")
        # Checks cannot change the outcome of a run that gave up without touching anything, and
        # can take many minutes.
        skipped = ("agent reported blocked and changed nothing"
                   if agent_status == "blocked" and not changes["files"]
                   and not changes["head_changed"] else None)
        commands = ([] if skipped or "test" not in b.orch.workflow.stages
                    else spec.get("verification_commands", []))
        if b.progress and commands:
            with contextlib.suppress(Exception):
                b.progress(f"[{task_id} #{n}] verifying ({len(commands)} commands)")
        def check_started(pid: int | None) -> None:  # recover() kills it if this runner dies
            self.store.update(task_id, codex_pid=pid,
                              codex_started=process_started(pid) if pid else None)

        flow = b.orch.workflow
        checks = (await run_verification(commands, b.root, b.cfg, should_cancel=cancelled,
                                         on_start=check_started, confine=flow.verify_sandbox,
                                         writable=flow.verify_writable) if commands else [])
        stopped = cancelled()  # checks cut short (or never started) prove nothing
        touched, later = changes["files"], []
        if commands:  # the checks ran code in the worktree: judge what they changed too
            final = recheck(b.root, before, changes, b.cfg)
            touched, later = final["files"], final["changed_by_verification"]
            violations = scope_violations(touched, spec, b.cfg, final["head_changed"],
                                          final["ignored_files"], enforce_ignored=enforce, root=b.root,
                                          verification_ignored=[f for f in final["ignored_files"]
                                                                if f not in changes["ignored_files"]])
        passed, dst, failure = verdict(violations, checks, agent_status, stopped, used, max_att)
        record["verification"] = {"passed": passed, "scope_violations": violations,
                                  "checks": checks, "agent_status": agent_status,
                                  **({"changed_by_verification": later} if later else {}),
                                  **({"skipped": skipped} if skipped else {}),
                                  **({"interrupted": True} if stopped else {}),
                                  **protected_after(b.root, violations)}
        # What a later approval is bound to: the verified content of every file the attempt touched.
        changes["after_hashes"] = content_hashes(b.root, touched)
        changes["verified_head"] = current_head(b.root)
        self.store.finish_attempt(task_id, n, **record)
        needs_human = bool(row["needs_human"]) or touches_sensitive(touched, b.cfg)
        if self._park_if_stopping(task_id, used):
            return  # stopped while verifying: the checks were cut short, not failed
        if self.store.get(task_id)["state"] != State.VERIFYING:
            return  # cancelled while verifying
        self.store.transition(task_id, dst, Actor.BRIDGE, record["verification"],
                              failure=failure, needs_human=int(needs_human))

    async def _rescue(self, task_id: str, error: Exception) -> None:
        """Take a task out of RUNNING/VERIFYING after a bridge error. The database may be the
        thing failing (busy, locked): retry with backoff rather than leave the task active."""
        for delay in (0, 0.2, 0.5, 1, 2):
            await asyncio.sleep(delay)
            try:
                row = self.store.get(task_id)
                if row["state"] in ACTIVE:
                    self.store.transition(
                        task_id, State.FAILED if row["state"] == State.RUNNING else State.PAUSED,
                        Actor.BRIDGE, {"error": repr(error)},
                        failure=f"BRIDGE_ERROR: {redact(repr(error), 500)}")
                return
            except Exception:  # noqa: BLE001 - every failure here is retried, then given up
                continue

    def _park_without_evidence(self, task_id: str, n: int, used: int, error: EvidenceError) -> None:
        """Git could not show what the run changed. The attempt proves nothing either way, so the
        task pauses (an environment failure, attempt given back) instead of being judged."""
        message = redact(str(error), 500)
        self.store.log(task_id, Actor.BRIDGE, "error", {"error": message})
        with contextlib.suppress(Exception):
            self.store.finish_attempt(task_id, n, outcome="evidence_error")
        with contextlib.suppress(Exception):
            if self.store.get(task_id)["state"] in ACTIVE:
                self.store.transition(task_id, State.PAUSED, Actor.BRIDGE, {"error": message},
                                      failure=f"EVIDENCE_ERROR: {message}", attempts=used - 1)

    def _park_if_stopping(self, task_id: str, used: int) -> bool:
        """PAUSE a run this bridge stopped (shutdown or a stop signal). An interrupted run is an
        environment failure, so it gives its attempt back."""
        if not self.bridge._stopping or self.store.get(task_id)["state"] not in ACTIVE:
            return False
        self.store.transition(task_id, State.PAUSED, Actor.BRIDGE, {"reason": "bridge shutdown"},
                              failure="INTERRUPTED: bridge shut down during execution",
                              attempts=used - 1)
        return True

    async def _review_stage(self, task_id: str) -> None:
        b = self.bridge
        try:
            if "review" not in b.orch.workflow.stages:
                b._apply_review(task_id, "approve", "Deterministic gates passed; the workflow "
                                "has no review stage.", Actor.BRIDGE, "bridge")
                return
            try:
                name = b._pick("reviewer", "read")
            except ValueError as e:
                self.store.log(task_id, Actor.BRIDGE, "agent_review_skipped", {"reason": str(e)})
                return
            if b.orch.agents[name].adapter == "client":
                return  # the MCP client reviews through bridge_review_task
            row = self.store.get(task_id)
            prompt = build_review_prompt(task_id, row["spec"], self.store.attempts(task_id)[-1])
            res = await b._consult(
                name, prompt, REVIEW_SCHEMA, task_id,
                lambda: b._stopping or self.store.get(task_id)["state"] != State.REVIEW)
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
                b._apply_review(task_id, d.decision, d.notes, Actor.AGENT, name)
        except Exception as e:  # the task stays in REVIEW; the client can still decide
            self.store.log(task_id, Actor.BRIDGE, "error", {"error": redact(repr(e), 2000)})
