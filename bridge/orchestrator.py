"""Orchestration config: accounts, agents, roles, routing and workflow stages.

Loaded from `.ai/orchestrator.yaml`. Without that file the legacy `.ai/config.json` accounts are
mapped to an equivalent setup (the MCP client plans and reviews, Codex executes), so existing
projects keep their behaviour.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .accounts import load_registry
from .registry import registry_dir
from .config import AI_DIR, BridgeConfig

ORCHESTRATOR_FILE = "orchestrator.yaml"
# Written only by the human-only `project-account-add/-remove` commands; merged under
# orchestrator.yaml, which wins on a name clash, so the user's own file is never rewritten.
MANAGED_FILE = "accounts.yaml"
MANAGED_KEYS = frozenset({"accounts", "agents", "executor"})

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


class UsdBudget(BaseModel):
    """Spend caps per UTC calendar period (day, ISO week from Monday, month)."""

    model_config = ConfigDict(extra="forbid")

    day: float | None = Field(None, gt=0)
    week: float | None = Field(None, gt=0)
    month: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _any(self) -> UsdBudget:
        if self.day is None and self.week is None and self.month is None:
            raise ValueError("budget_usd needs at least one of day, week, month")
        return self

    def periods(self) -> dict[str, float]:
        return {p: v for p in ("day", "week", "month")
                if (v := getattr(self, p)) is not None}


class TokenPricing(BaseModel):
    """USD per million tokens, supplied by the user; the kit ships no price table."""

    model_config = ConfigDict(extra="forbid")

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cached_input_per_mtok: float | None = Field(None, ge=0)


class AccountLimits(BaseModel):
    """Per-account usage limits; an account without them behaves as before."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Percent of a provider window used at which the account stops taking work.
    five_hour_pct: float | None = Field(None, gt=0, le=100)
    weekly_pct: float | None = Field(None, gt=0, le=100)
    any_window_pct: float | None = Field(None, gt=0, le=100)
    budget_usd: UsdBudget | None = None
    # Reserved before each run on a budgeted account; a run starts only if it fits.
    reserve_usd: float = Field(0.0, ge=0)
    pricing: TokenPricing | None = None
    # Lifts a QUOTA mark after this long when no reset time is readable.
    cooldown_s: int | None = Field(None, ge=60, le=604800)

    @model_validator(mode="after")
    def _reserve(self) -> AccountLimits:
        if self.reserve_usd > 0:
            if self.budget_usd is None:
                raise ValueError("reserve_usd needs budget_usd")
            smallest = min(self.budget_usd.periods().values())
            if self.reserve_usd > smallest:
                raise ValueError(f"reserve_usd {self.reserve_usd} exceeds the smallest "
                                 f"budget {smallest}")
        return self


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    # Isolated auth home (CODEX_HOME / CLAUDE_CONFIG_DIR). None = the inherited global login.
    home: str | None = None
    max_concurrency: int = Field(1, ge=1, le=8)
    limits: AccountLimits | None = None
    # The NAME of an environment variable holding a `claude setup-token` token, never the token:
    # this file sits in the worktree, where agents can read it. Its own namespace keeps a
    # tampered file from pointing at another secret (GITHUB_TOKEN); the _TOKEN suffix keeps it
    # under the built-in *_TOKEN scrub, so no agent or verification command inherits it.
    token_env: str | None = Field(None, pattern=r"^AGENT_BRIDGE_CLAUDE_[A-Z0-9_]{1,40}_TOKEN$")
    # "keychain": the token lives in the macOS Keychain (`agent-bridge account-set-token`).
    credential: Literal["keychain"] | None = None
    # What that secret is: a `claude setup-token` token or an Anthropic Console API key.
    credential_kind: Literal["oauth_token", "api_key"] = "oauth_token"
    # The name of a machine registry login this project account uses: provider and home come
    # from that entry (resolved in `parse`); limits and credentials stay the project's own.
    login: str | None = Field(None, exclude=True)

    @model_validator(mode="after")
    def _known_provider(self) -> AccountProfile:
        if self.provider not in PROVIDERS:
            raise ValueError(f"unsupported provider {self.provider!r}; supported: "
                             f"{sorted(PROVIDERS)}")
        if (self.token_env or self.credential) and self.provider != "anthropic":
            raise ValueError("token_env and credential are for anthropic accounts "
                             "(claude setup-token)")
        if self.token_env and self.credential:
            raise ValueError("use token_env or credential, not both")
        if self.credential_kind != "oauth_token" and not (self.token_env or self.credential):
            raise ValueError("credential_kind needs credential or token_env")
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
    # quota: among eligible agents, the one whose account has the most remaining quota; unknown
    # quota ranks after every known value; ties keep list order.
    strategy: Literal["ordered", "explicit", "quota"] = "ordered"
    requires: Capability | None = None
    # The agent an unpinned task tries first; the rest of `agents` are its fallbacks in order.
    default: str | None = None

    @model_validator(mode="after")
    def _default_first(self) -> RoleConfig:
        if self.default is not None:
            if self.strategy == "explicit":
                raise ValueError("default needs strategy ordered: explicit makes every task "
                                 "name its agent")
            if self.strategy == "quota":
                raise ValueError("default needs strategy ordered: quota picks the agent whose "
                                 "account has the most remaining quota")
            self.agents = [self.default, *(a for a in self.agents if a != self.default)]
        return self


class QuotaFallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Moving work to another account when one runs low on quota is opt-in: enabling it is the
    # user's authorisation. Every move is an audit event.
    enabled: bool = False
    min_remaining_pct: float = Field(10.0, ge=0, le=100)
    agents: list[str] | None = None  # candidates in order; None = the role's agents
    # Caps automatic moves per task, so two exhausted accounts cannot bounce a task forever.
    max_switches: int = Field(3, ge=1, le=20)
    # Refuse to move a task that already has an agent session (its context would be lost).
    keep_sessions: bool = False


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quota_fallback: QuotaFallback = QuotaFallback()
    # How long a workflow task waits, with backoff, for an account or worktree slot held by
    # another bridge process before the workflow ends WAITING; 0 ends it at once.
    slot_wait_s: float = Field(1800, ge=0, le=86400)


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[str] = ["execute", "test", "review"]
    max_rework: int = Field(2, ge=0, le=4)
    # Verification runs code the executor wrote. auto: confine its writes to the worktree and a
    # private temp dir where the kernel supports Landlock; strict: also deny TCP, and refuse to
    # run checks without Landlock; none: no confinement.
    verify_sandbox: Literal["auto", "strict", "none"] = "auto"
    verify_writable: list[str] = []  # extra directories checks may write (e.g. ~/.cache/uv)

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
        relative = [p for p in self.verify_writable if not Path(p).expanduser().is_absolute()]
        if relative:
            raise ValueError(f"verify_writable needs absolute paths (or ~/...): {relative}")
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
    # Registry accounts no agent of this project references: still usable by the machine-wide
    # account commands, never part of the project's accounts.
    catalog: dict[str, AccountProfile] = Field({}, exclude=True)
    # Provenance, for reporting.
    accounts_from_registry: list[str] = Field([], exclude=True)
    accounts_in_orchestrator: list[str] = Field([], exclude=True)
    agents_in_accounts_file: list[str] = Field([], exclude=True)

    @model_validator(mode="after")
    def _references(self) -> OrchestratorConfig:
        for kind, names in (("account", self.accounts), ("agent", self.agents),
                            ("role", self.roles)):
            for name in names:
                base, at, provider = name.partition("@")
                if not re.match(_NAME, base) or (at and (
                        kind != "account" or provider != self.accounts[name].provider)):
                    raise ValueError(f"invalid {kind} id {name!r}: use [a-z0-9_-], max 32 chars"
                                     " (an account may end in @<its provider>)")
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
            need = ADAPTER_PROVIDERS[agent.adapter]
            acc = self.accounts.get(agent.account)
            other = self.accounts.get(f"{agent.account}@{need}")
            if other is not None and (acc is None or acc.provider != need):
                # A name shared across providers: the adapter's provider picks the account.
                agent.account, acc = f"{agent.account}@{need}", other
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
    agents |= {name.replace("@", "-"): AgentConfig(adapter="codex-cli", account=name,
                                                   model=cfg.model)
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


def account_owner(orch: OrchestratorConfig, name: str) -> str:
    """Where a human changes this account's settings."""
    if name in orch.accounts_in_orchestrator:
        return f"accounts.{name} in .ai/orchestrator.yaml (move it to .ai/accounts.yaml)"
    if name in orch.accounts_from_registry:
        return (f"the machine registry; declare it for this project: `{name}: {{login: {name}}}` "
                "in .ai/accounts.yaml (or run `agent-bridge config-migrate`)")
    if orch.source.startswith("legacy"):
        return ".ai/config.json"
    return f"accounts.{name} in .ai/accounts.yaml (`agent-bridge project-account-limits {name}`)"


def deprecated(orch: OrchestratorConfig, cfg: BridgeConfig) -> list[str]:
    """Settings layouts that still load but should move; empty for a clean config."""
    out = [f"{n}: declared in orchestrator.yaml; move it to .ai/accounts.yaml"
           for n in orch.accounts_in_orchestrator]
    out += [f"{n}: registry login used without a project declaration; its limits come from "
            "the machine registry" for n in orch.accounts_from_registry]
    out += [f"{n}: agent or executor entry in .ai/accounts.yaml; move it to orchestrator.yaml"
            for n in orch.agents_in_accounts_file]
    if not orch.source.startswith("legacy") and "max_attempts" in cfg.model_fields_set:
        out.append("max_attempts in .ai/config.json is ignored; use workflow.max_rework")
    return out


def load_managed(path: Path) -> dict:
    """The CLI-managed `.ai/accounts.yaml`: {accounts, agents, executor}, all optional."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict) or set(data) - MANAGED_KEYS:
        raise ValueError(f"{path}: expected only {sorted(MANAGED_KEYS)}")
    return data


def _with_managed(data: dict, managed: dict, source: str = ORCHESTRATOR_FILE,
                  managed_source: str = MANAGED_FILE) -> tuple[set[str], set[str]]:
    """Merge `managed` into `data` in place; returns the account and agent names it added.

    A name declared in both files is an error: neither file silently overrides the other."""
    added = {}
    for key in ("accounts", "agents"):
        own = data.get(key) or {}
        theirs = managed.get(key) or {}
        for name in theirs:
            if name in own:
                raise ValueError(f"{key[:-1]} {name!r} is declared in both {source} and "
                                 f"{managed_source}; keep it in one file")
        added[key] = set(theirs)
        data[key] = {**theirs, **own}
    first = [a for a in managed.get("executor") or [] if a in data["agents"]]
    roles = data.get("roles")
    if first and isinstance(roles, dict) and "executor" in roles:
        role = roles["executor"]
        if isinstance(role, str):
            roles["executor"] = {"agents": list(dict.fromkeys([*first, role]))}
        elif isinstance(role, list):
            roles["executor"] = list(dict.fromkeys([*first, *role]))
        elif isinstance(role, dict) and isinstance(role.get("agents"), list):
            # A `default:` in orchestrator.yaml still goes first; these come next.
            role["agents"] = list(dict.fromkeys([*first, *role["agents"]]))
    return added["accounts"], added["agents"]


def _resolve_logins(accounts: dict, registry: dict, managed_accounts: set[str],
                    source: str, managed_source: str) -> set[str]:
    """Fill provider and home of `login:` entries from the registry; returns their names."""
    used = set()
    for name, entry in accounts.items():
        if not isinstance(entry, dict) or entry.get("login") is None:
            continue
        where = managed_source if name in managed_accounts else source
        login = entry["login"]
        target = registry.get(login) if isinstance(login, str) else None
        if target is None:
            raise ValueError(f"{where}: account {name}: unknown login {login!r}; "
                             f"registry logins: {sorted(registry)}")
        if entry.get("home") is not None:
            raise ValueError(f"{where}: account {name}: login {login!r} supplies the home; "
                             "remove `home`")
        if entry.get("provider") not in (None, target.get("provider")):
            raise ValueError(f"{where}: account {name}: provider {entry['provider']!r} does "
                             f"not match login {login!r} ({target.get('provider')})")
        entry["provider"] = target.get("provider")
        if target.get("home") is not None:
            entry["home"] = target["home"]
        used.add(name)
    return used


def parse(text: str, source: str = ORCHESTRATOR_FILE,
          registry: dict | None = None, managed: dict | None = None,
          managed_source: str = MANAGED_FILE) -> OrchestratorConfig:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{source}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected a mapping at the top level")
    # Never mutate the caller's arguments: login resolution fills provider and home in place.
    managed = copy.deepcopy(managed or {})
    registry = copy.deepcopy(registry or {})
    own_accounts = list(data.get("accounts") or {})
    managed_accounts, managed_agents = _with_managed(data, managed, source, managed_source)
    logins = (_resolve_logins(data["accounts"], registry, managed_accounts,
                              source, managed_source)
              if isinstance(data.get("accounts"), dict) else set())
    declared = set(data.get("accounts") or {})
    # A registry entry whose login a project account already uses is not merged a second time:
    # both would share one auth home.
    taken = {(data["accounts"][n].get("provider"), data["accounts"][n].get("home"))
             for n in logins}
    inherited = {n for n in registry if n not in declared
                 and (registry[n].get("provider"), registry[n].get("home")) not in taken}
    extra = {n: registry[n] for n in registry if n not in declared and n not in inherited}
    if inherited:
        # Registry accounts resolve by name; the project's own declarations win.
        data["accounts"] = {**{n: registry[n] for n in registry if n in inherited},
                            **(data.get("accounts") or {})}
    try:
        orch = OrchestratorConfig.model_validate({**data, "source": source})
    except ValidationError as e:
        problems: dict[str, list[str]] = {}
        for err in e.errors():
            loc = err["loc"]
            # An error in a machine-wide or CLI-managed entry belongs to that file.
            where = source
            if len(loc) > 1 and loc[0] == "accounts" and loc[1] in inherited:
                where = str(registry_dir() / "accounts.json")
            elif len(loc) > 1 and ((loc[0] == "accounts" and loc[1] in managed_accounts)
                                   or (loc[0] == "agents" and loc[1] in managed_agents)):
                where = managed_source
            problems.setdefault(where, []).append(
                f"{'.'.join(str(p) for p in loc) or '<root>'}: {err['msg']}")
        raise ValueError("; ".join(f"{where}: {'; '.join(found)}"
                                   for where, found in problems.items())) from e
    # Scope the registry to this project: only accounts an agent points to stay.
    referenced = {a.account for a in orch.agents.values() if a.account}
    catalog: dict[str, AccountProfile] = {}
    for name in sorted(extra):
        try:
            catalog[name] = AccountProfile(**extra[name])
        except ValidationError as e:
            raise ValueError(f"{registry_dir() / 'accounts.json'}: accounts.{name}: "
                             f"{e.errors()[0]['msg']}") from e
    kept = {}
    for name, acc in orch.accounts.items():
        if name in inherited and name not in referenced:
            catalog[name] = acc
        else:
            kept[name] = acc
    orch.accounts = kept
    orch.catalog = dict(sorted(catalog.items()))
    orch.accounts_from_registry = [n for n in kept if n in inherited]
    orch.accounts_in_orchestrator = own_accounts
    orch.agents_in_accounts_file = list(dict.fromkeys(
        [*managed_agents, *(managed.get("executor") or [])]))
    return orch


def migration_plan(root: Path, cfg: BridgeConfig) -> dict:
    """The one-owner layout for this project; writes nothing.

    Accounts end up only in accounts.yaml (registry logins as `login:` entries that carry the
    registry limits), agents and roles only in orchestrator.yaml. The effective configuration
    must be unchanged, else ValueError and the plan is never to be written."""
    path = root / AI_DIR / ORCHESTRATOR_FILE
    if not path.exists():
        raise ValueError("nothing to migrate: legacy setup")
    managed_path = root / AI_DIR / MANAGED_FILE
    registry = load_registry()
    current = load(root, cfg)
    managed = load_managed(managed_path)
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    data = copy.deepcopy(data)
    moves: list[str] = []

    accounts = copy.deepcopy(managed.get("accounts") or {})
    for name, entry in (data.pop("accounts", None) or {}).items():
        accounts[name] = copy.deepcopy(entry)
        moves.append(f"account {name}: {ORCHESTRATOR_FILE} -> {MANAGED_FILE}")
    for name in current.accounts_from_registry:
        reg = registry.get(name) or {}
        entry: dict = {"login": name}
        for key in ("max_concurrency", "limits"):
            if reg.get(key) is not None:
                entry[key] = copy.deepcopy(reg[key])
        accounts[name] = entry
        moves.append(f"account {name}: machine registry -> {MANAGED_FILE} as `login: {name}`"
                     " (registry limits copied)")

    extra = {k: copy.deepcopy(managed[k]) for k in ("agents", "executor") if managed.get(k)}
    for name in extra.get("agents") or {}:
        moves.append(f"agent {name}: {MANAGED_FILE} -> {ORCHESTRATOR_FILE}")
    for name in extra.get("executor") or []:
        moves.append(f"executor entry {name}: {MANAGED_FILE} -> roles.executor in "
                     f"{ORCHESTRATOR_FILE}")
    if extra:
        _with_managed(data, extra)
        data.pop("accounts", None)

    plan_accounts = {"accounts": accounts}
    text = yaml.safe_dump(data, sort_keys=False)
    planned = parse(text, str(path), registry, plan_accounts, str(managed_path))
    if planned.model_dump() != current.model_dump():
        raise ValueError("migration would change the effective configuration; nothing "
                         "written. Differences in: " + ", ".join(
                             k for k, v in current.model_dump().items()
                             if planned.model_dump().get(k) != v))
    if (planned.accounts_in_orchestrator or planned.accounts_from_registry
            or planned.agents_in_accounts_file):
        left = [*planned.accounts_in_orchestrator, *planned.accounts_from_registry,
                *planned.agents_in_accounts_file]
        raise ValueError(f"migration would leave settings in the old layout: {left}")
    return {"accounts_yaml": plan_accounts, "orchestrator_yaml": data, "moves": moves,
            "changed": bool(moves)}


def load(root: Path, cfg: BridgeConfig) -> OrchestratorConfig:
    path = root / AI_DIR / ORCHESTRATOR_FILE
    registry = load_registry()
    if not path.exists():
        return legacy(cfg, registry)
    if cfg.accounts or cfg.auto_switch.enabled:
        raise ValueError(f"{path}: accounts and auto_switch moved to orchestrator.yaml; "
                         "remove them from .ai/config.json")
    managed = root / AI_DIR / MANAGED_FILE
    return parse(path.read_text(), str(path), registry, load_managed(managed), str(managed))
