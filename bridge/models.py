"""Task contract and the task state machine (states, transitions, who may make them)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class State(StrEnum):
    BACKLOG = "BACKLOG"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    REWORK = "REWORK"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


class Actor(StrEnum):
    BRIDGE = "bridge"  # automatic, driven by execution/verification outcomes and workflows
    CLIENT = "client"  # whoever calls the MCP tools or the CLI (often an interactive agent)
    AGENT = "agent"  # a configured agent's structured decision (planner, reviewer)
    HUMAN = "human"  # interactive TTY confirmation only


S, A = State, Actor
TERMINAL = frozenset({S.DONE, S.CANCELLED, S.BLOCKED})
ACTIVE = frozenset({S.RUNNING, S.VERIFYING})

# (from, to) -> actors allowed to make the move. Anything absent is illegal.
TRANSITIONS: dict[tuple[State, State], frozenset[Actor]] = {
    (S.BACKLOG, S.READY): frozenset({A.BRIDGE}),
    # The bridge starts runs itself only inside a workflow it is driving.
    (S.READY, S.RUNNING): frozenset({A.CLIENT, A.BRIDGE}),
    (S.REWORK, S.RUNNING): frozenset({A.CLIENT, A.BRIDGE}),
    (S.FAILED, S.RUNNING): frozenset({A.CLIENT, A.BRIDGE}),
    (S.PAUSED, S.RUNNING): frozenset({A.CLIENT, A.BRIDGE}),
    (S.RUNNING, S.VERIFYING): frozenset({A.BRIDGE}),
    (S.RUNNING, S.FAILED): frozenset({A.BRIDGE}),
    (S.RUNNING, S.PAUSED): frozenset({A.BRIDGE}),
    (S.RUNNING, S.BLOCKED): frozenset({A.BRIDGE}),
    (S.VERIFYING, S.REVIEW): frozenset({A.BRIDGE}),
    (S.VERIFYING, S.REWORK): frozenset({A.BRIDGE}),
    (S.VERIFYING, S.BLOCKED): frozenset({A.BRIDGE}),
    (S.VERIFYING, S.PAUSED): frozenset({A.BRIDGE}),
    # BRIDGE approves only when the workflow has no review stage (deterministic gates).
    (S.REVIEW, S.APPROVED): frozenset({A.CLIENT, A.AGENT, A.BRIDGE}),
    (S.REVIEW, S.REWORK): frozenset({A.CLIENT, A.AGENT}),
    (S.REVIEW, S.BLOCKED): frozenset({A.CLIENT, A.AGENT}),
    (S.REWORK, S.BLOCKED): frozenset({A.CLIENT, A.BRIDGE}),
    (S.FAILED, S.BLOCKED): frozenset({A.CLIENT, A.BRIDGE}),
    (S.PAUSED, S.BLOCKED): frozenset({A.CLIENT, A.BRIDGE}),
    # Low/medium risk: the bridge closes the task once its quality gates hold.
    # High risk / sensitive change: only a human closes it.
    (S.APPROVED, S.DONE): frozenset({A.BRIDGE, A.HUMAN}),
}
for _s in State:
    if _s not in TERMINAL:
        TRANSITIONS[(_s, S.CANCELLED)] = frozenset({A.CLIENT, A.HUMAN})
    if _s in ACTIVE:
        # Interrupted runs (process died) are parked for a manual resume.
        TRANSITIONS.setdefault((_s, S.PAUSED), frozenset({A.BRIDGE}))


class TransitionError(ValueError):
    pass


def check_transition(src: State, dst: State, actor: Actor) -> None:
    allowed = TRANSITIONS.get((src, dst))
    if allowed is None:
        raise TransitionError(f"illegal transition {src} -> {dst}")
    if actor not in allowed:
        raise TransitionError(f"{actor} may not move a task {src} -> {dst}")


# Outcomes that mean "the environment, not the code": park the task, never auto-retry.
PAUSE_OUTCOMES = frozenset({"quota", "auth", "network", "missing_binary"})


def run_failure_state(outcome: str, used: int, max_attempts: int) -> State:
    """Where a run that ended neither "ok" nor "cancelled" goes. An environment failure pauses
    (the attempt is given back); a timeout or crash spends it, BLOCKED once the budget is gone."""
    if outcome in PAUSE_OUTCOMES:
        return S.PAUSED
    return S.BLOCKED if used >= max_attempts else S.FAILED


PROTECTED_VIOLATION = "protected file changed: "


def verdict(violations: list[str], checks: list[dict], agent_status: str, stopped: bool,
            used: int, max_attempts: int) -> tuple[bool, State, str | None]:
    """The bridge's gate on a verified attempt: (passed, next state, failure text).

    Only a clean scope, every check passed, checks not cut short and the agent reporting
    `completed` reach REVIEW. A changed protected file (config, secrets, .git) BLOCKS: the
    worktree may now hold tampered settings, so a human looks before anything runs again.
    Anything else is REWORK, BLOCKED once the budget is gone."""
    passed = (not violations and not stopped and all(c["passed"] for c in checks)
              and agent_status == "completed")
    if passed:
        return True, S.REVIEW, None
    failure = "; ".join(violations + [f"check failed: {c['command']}" for c in checks
                                      if not c["passed"]]
                        + ([f"agent status: {agent_status}"]
                           if agent_status != "completed" else []))[:1000]
    protected = [v.split(": ", 1)[1] for v in violations if v.startswith(PROTECTED_VIOLATION)]
    if protected:
        return False, S.BLOCKED, (f"PROTECTED: {', '.join(protected)}: inspect and restore "
                                  f"them, then create a new task; {failure}")[:1000]
    return False, S.REWORK if used < max_attempts else S.BLOCKED, failure


class TaskSpec(BaseModel):
    """What the planner hands to an executor agent. The bridge assigns the task id."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # agent pins the task to one configured agent; account is the legacy way to pin by account.
    agent: str | None = None
    account: str | None = None
    objective: str = Field(min_length=1, max_length=4000)
    context: str = Field("", max_length=20000)
    scope: str = Field("", max_length=4000)
    allowed_paths: list[str] = Field(min_length=1, max_length=50)
    excluded_paths: list[str] = Field(default_factory=list, max_length=50)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=30)
    verification_commands: list[str] = Field(default_factory=list, max_length=10)
    risk_level: Literal["low", "medium", "high"] = "low"
    timeout_s: int | None = Field(None, ge=30)
    max_attempts: int | None = Field(None, ge=1, le=5)
    sandbox: Literal["read-only", "workspace-write"] | None = None


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "rework", "block"]
    notes: str = Field(min_length=1, max_length=8000)
