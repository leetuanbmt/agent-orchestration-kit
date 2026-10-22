"""Orchestration config: accounts, agents, roles, routing and workflow stages.

Loaded from `.ai/orchestrator.yaml`. Without that file the legacy `.ai/config.json` accounts are
mapped to an equivalent setup (the MCP client plans and reviews, Codex executes), so existing
projects keep their behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .accounts import load_registry
from .config import AI_DIR, BridgeConfig

ORCHESTRATOR_FILE = "orchestrator.yaml"

# Adapter -> provider it speaks for. `client` is the MCP client driving the bridge (an
# interactive Claude Code session, for example): it plans and reviews through the MCP tools and
# is never started by the bridge.
ADAPTER_PROVIDERS: dict[str, str | None] = {
    "codex-cli": "openai",
    "claude-cli": "anthropic",
    "opencode-cli": "opencode",
    "agy-cli": "google",
    "client": None,
}
PROVIDERS = frozenset(p for p in ADAPTER_PROVIDERS.values() if p)
# Adapters with no verified way to confine writes to a task's paths: planner/reviewer only.
# Probed on opencode 2.1.2 and agy 1.4.5: their read-only modes refused every write.
READ_ONLY_ADAPTERS = frozenset({"opencode-cli", "agy-cli"})
# opencode keeps credentials behind one background service per user, so it has no isolated
# home: its only account is the global `opencode auth login`.
GLOBAL_ONLY_PROVIDERS = frozenset({"opencode"})
STAGES = ("plan", "execute", "test", "review")
BUILTIN_ROLES = ("advisor", "planner", "executor", "reviewer", "tester")
Capability = Literal["read", "write"]
_NAME = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    # Isolated auth home (CODEX_HOME / CLAUDE_CONFIG_DIR). None = the inherited global login.
    home: str | None = None
    max_concurrency: int = Field(1, ge=1, le=8)

    @model_validator(mode="after")
    def _known_provider(self) -> AccountProfile:
        if self.provider not in PROVIDERS:
            raise ValueError(f"unsupported provider {self.provider!r}; supported: "
                             f"{sorted(PROVIDERS)}")
        if self.home and self.provider in GLOBAL_ONLY_PROVIDERS:
            raise ValueError(f"{self.provider} accounts use the global login (no home); "
                             "log in with: opencode auth login")
        return self


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    provider: str | None = None
    account: str | None = None
    model: str | None = None
    # Restricts what the agent is used for; None = everything its adapter allows.
    capabilities: list[Capability] | None = None
    # claude-cli has no OS sandbox, so writing is opt-in; codex-cli writes inside its sandbox.
    allow_write: bool | None = None
    allow_bash: bool = False
    timeout_s: int | None = Field(None, ge=30)
    enabled: bool = True

    @model_validator(mode="after")
    def _adapter(self) -> AgentConfig:
        if self.adapter not in ADAPTER_PROVIDERS:
            raise ValueError(f"unknown adapter {self.adapter!r}; available: "
                             f"{sorted(ADAPTER_PROVIDERS)}")
        expected = ADAPTER_PROVIDERS[self.adapter]
        if self.provider is not None and self.provider != expected:
            raise ValueError(f"adapter {self.adapter} speaks for provider {expected}, "
                             f"not {self.provider}")
        if self.adapter == "client":
            if self.account or self.model or self.allow_write or self.allow_bash:
                raise ValueError("a client agent is the MCP caller: it takes no account, "
                                 "model, allow_write or allow_bash")
        elif self.account is None:
            raise ValueError(f"adapter {self.adapter} needs an account")
        if self.adapter in READ_ONLY_ADAPTERS and (self.allow_write
                                                   or "write" in (self.capabilities or [])):
            raise ValueError(f"{self.adapter} is read-only (planner/reviewer): no way to "
                             "confine its writes to a task's paths has been verified")
        if self.allow_bash and self.adapter != "claude-cli":
            raise ValueError("allow_bash only applies to claude-cli (codex-cli is sandboxed)")
        return self

    def can(self, capability: str) -> bool:
        if self.adapter == "client":
            return capability == "read"
        allowed = {"read"} | ({"write"} if self.writes and self.adapter not in
                              READ_ONLY_ADAPTERS else set())
        return capability in allowed and (self.capabilities is None
                                          or capability in self.capabilities)

    @property
    def writes(self) -> bool:
        if self.allow_write is not None:
            return self.allow_write
        return self.adapter == "codex-cli"


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: list[str] = Field(min_length=1)
    # ordered: first eligible agent in list order (fallback when one is busy or unavailable).
    # explicit: every task must name one of `agents`.
    strategy: Literal["ordered", "explicit"] = "ordered"
    requires: Capability | None = None


class QuotaFallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Moving work to another account when one runs low on quota is opt-in: enabling it is the
    # user's authorisation. Every move is an audit event.
    enabled: bool = False
    min_remaining_pct: float = Field(10.0, ge=0, le=100)
    agents: list[str] | None = None  # candidates in order; None = the role's agents


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quota_fallback: QuotaFallback = QuotaFallback()


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[str] = ["execute", "test", "review"]
    max_rework: int = Field(2, ge=0, le=4)

    @model_validator(mode="after")
    def _stages(self) -> WorkflowConfig:
        unknown = [s for s in self.stages if s not in STAGES]
        if unknown:
            raise ValueError(f"unknown stages {unknown}; available: {list(STAGES)}")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("duplicate stages")
        if "execute" not in self.stages:
            raise ValueError("a workflow needs the execute stage")
        if not {"test", "review"} & set(self.stages):
            raise ValueError("a workflow needs a gate: test, review or both")
        if self.stages != [s for s in STAGES if s in self.stages]:
            raise ValueError(f"stages must keep the order {list(STAGES)}")
        return self


class SecurityConfig(BaseModel):
    """Both flags are enforced by code and cannot be turned off; they exist to be explicit."""

    model_config = ConfigDict(extra="forbid")

    project_isolation: Literal[True] = True
    human_approval_for_sensitive_actions: Literal[True] = True


class OrchestratorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    accounts: dict[str, AccountProfile] = {}
    agents: dict[str, AgentConfig]
    roles: dict[str, str | RoleConfig]
    routing: RoutingConfig = RoutingConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    security: SecurityConfig = SecurityConfig()
    source: str = Field("orchestrator.yaml", exclude=True)

    @model_validator(mode="after")
    def _references(self) -> OrchestratorConfig:
        for kind, names in (("account", self.accounts), ("agent", self.agents),
                            ("role", self.roles)):
            for name in names:
                if not re.match(_NAME, name):
                    raise ValueError(f"invalid {kind} id {name!r}: use [a-z0-9_-], max 32 chars")
        homes: dict[tuple[str, str], str] = {}
        for name, acc in self.accounts.items():
            key = (acc.provider, str(Path(acc.home).expanduser()) if acc.home else "")
            if key in homes:
                raise ValueError(f"accounts {homes[key]} and {name} share one auth home; "
                                 "each account needs its own")
            homes[key] = name
        for name, agent in self.agents.items():
            if agent.account is None:
                continue
            acc = self.accounts.get(agent.account)
            if acc is None:
                raise ValueError(f"agent {name}: unknown account {agent.account!r}")
            if acc.provider != ADAPTER_PROVIDERS[agent.adapter]:
                raise ValueError(f"agent {name}: account {agent.account} is {acc.provider}, "
                                 f"adapter {agent.adapter} needs "
                                 f"{ADAPTER_PROVIDERS[agent.adapter]}")
        for role in self.roles:
            cfg = self.role(role)
            for name in cfg.agents:
                if name not in self.agents:
                    raise ValueError(f"role {role}: unknown agent {name!r}")
            need = self.requirement(role)
            capable = [a for a in cfg.agents if self.agents[a].can(need)]
            if not capable:
                raise ValueError(f"role {role} needs {need!r}; none of {cfg.agents} can")
            if role == "executor" and any(self.agents[a].adapter == "client" for a in cfg.agents):
                raise ValueError("the executor role cannot use a client agent: the bridge "
                                 "only runs agents it can start")
        if "executor" not in self.roles:
            raise ValueError("roles.executor is required")
        if "review" in self.workflow.stages and "reviewer" not in self.roles:
            raise ValueError("the review stage needs roles.reviewer")
        if "plan" in self.workflow.stages and "planner" not in self.roles:
            raise ValueError("the plan stage needs roles.planner")
        for name in self.routing.quota_fallback.agents or []:
            if name not in self.agents:
                raise ValueError(f"routing.quota_fallback: unknown agent {name!r}")
        return self

    def role(self, name: str) -> RoleConfig:
        value = self.roles.get(name)
        if value is None:
            raise ValueError(f"no role {name!r} configured; roles: {sorted(self.roles)}")
        return RoleConfig(agents=[value]) if isinstance(value, str) else value

    def requirement(self, role: str) -> str:
        cfg = self.roles.get(role)
        if isinstance(cfg, RoleConfig) and cfg.requires:
            return cfg.requires
        return "write" if role == "executor" else "read"

    def agent(self, name: str) -> AgentConfig:
        agent = self.agents.get(name)
        if agent is None:
            raise ValueError(f"unknown agent {name!r}; agents: {sorted(self.agents)}")
        return agent

    @property
    def max_attempts(self) -> int:
        return self.workflow.max_rework + 1


def legacy(cfg: BridgeConfig, registry: dict | None = None) -> OrchestratorConfig:
    """The setup implied by `.ai/config.json`: agents are named after the Codex accounts.

    Machine-wide registry accounts are added too; Codex ones become agents tasks can be
    pinned or reassigned to, but the executor role keeps its `config.json` order."""
    accounts = {"default": AccountProfile(provider="openai")}
    accounts |= {name: AccountProfile(provider="openai", home=a.codex_home)
                 for name, a in sorted(cfg.accounts.items())}
    for name, entry in sorted((registry or {}).items()):
        accounts.setdefault(name, AccountProfile(**entry))
    agents = {"client": AgentConfig(adapter="client")}
    agents |= {name: AgentConfig(adapter="codex-cli", account=name, model=cfg.model)
               for name, acc in accounts.items() if acc.provider == "openai"}
    fallback = cfg.auto_switch
    executors = list(dict.fromkeys([cfg.default_account, *fallback.fallback_accounts]))
    return OrchestratorConfig(
        accounts=accounts, agents=agents,
        roles={"advisor": "client", "planner": "client", "reviewer": "client",
               "executor": RoleConfig(agents=executors)},
        routing=RoutingConfig(quota_fallback=QuotaFallback(
            enabled=fallback.enabled, min_remaining_pct=fallback.min_remaining_pct,
            agents=fallback.fallback_accounts)),
        workflow=WorkflowConfig(max_rework=cfg.max_attempts - 1),
        source="legacy .ai/config.json",
    )


def parse(text: str, source: str = ORCHESTRATOR_FILE,
          registry: dict | None = None) -> OrchestratorConfig:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{source}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected a mapping at the top level")
    if registry:
        # Registry accounts are usable by name; the project's own declarations win.
        data["accounts"] = {**registry, **(data.get("accounts") or {})}
    try:
        return OrchestratorConfig.model_validate({**data, "source": source})
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in e.errors())
        raise ValueError(f"{source}: {problems}") from e


def load(root: Path, cfg: BridgeConfig) -> OrchestratorConfig:
    path = root / AI_DIR / ORCHESTRATOR_FILE
    registry = load_registry()
    if not path.exists():
        return legacy(cfg, registry)
    if cfg.accounts or cfg.auto_switch.enabled:
        raise ValueError(f"{path}: accounts and auto_switch moved to orchestrator.yaml; "
                         "remove them from .ai/config.json")
    return parse(path.read_text(), str(path), registry)
