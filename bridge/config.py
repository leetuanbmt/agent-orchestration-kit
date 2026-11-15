"""Bridge configuration: defaults overridden by the project-local `.ai/config.json`."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import storage
from .registry import find_root as find_root

AI_DIR = ".ai"
CONFIG_FILE = "config.json"
DEFAULT_PROTECTED_PATHS = [
    ".env", ".env.*", "**/.env", "**/.env.*", "*.pem", "*.key", "**/*.pem", "**/*.key",
    ".git/**", ".ai/**", ".mcp.json", ".claude/settings.json", ".claude/settings.local.json",
]
DEFAULT_ENV_DENY = ["*_API_KEY", "*_TOKEN", "*_SECRET*", "*PASSWORD*", "AWS_*"]


class AccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codex_home: str


class AutoSwitchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    min_remaining_pct: float = Field(10.0, ge=0, le=100)
    fallback_accounts: list[str] = []


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: dict[str, AccountConfig] = {}
    default_account: str = "default"
    lock_dir: str | None = None
    auto_switch: AutoSwitchConfig = AutoSwitchConfig()

    codex_bin: str = "codex"
    claude_bin: str = "claude"
    # Owner-supplied hooks-only file: trusted code runs without a sandbox.
    claude_hook_settings: str | None = None
    opencode_bin: str = "opencode"
    cursor_bin: str = "cursor-agent"
    agy_bin: str = "agy"
    model: str | None = None
    # danger-full-access is deliberately not representable: no silent fallback to unrestricted.
    sandbox: Literal["read-only", "workspace-write"] = "workspace-write"
    # Skips ~/.codex/config.toml (user hooks, MCP servers, profiles); auth still comes from CODEX_HOME.
    ignore_user_config: bool = True
    default_timeout_s: int = Field(1800, ge=30)
    max_timeout_s: int = Field(7200, ge=30)
    # 1 initial run + 2 rework rounds.
    max_attempts: int = Field(3, ge=1, le=5)
    network_retries: int = Field(1, ge=0, le=3)
    retry_backoff_s: float = Field(10.0, ge=0)
    verify_timeout_s: int = Field(900, ge=5)
    # Owner location of the verification sandbox settings; orchestrator.yaml workflow.verify_* is
    # deprecated. None means "not set here".
    verify_sandbox: Literal["auto", "strict", "none"] | None = None
    verify_writable: list[str] | None = None
    # Executables a verification command may start (argv[0] basename). No shells.
    verify_allowlist: list[str] = [
        "pytest", "uv", "python", "python3", "ruff", "mypy", "make",
        "npm", "npx", "pnpm", "yarn", "node", "go", "cargo", "git", "true", "false",
    ]
    # Never writable by Codex, never allowed in a task's allowed_paths.
    protected_paths: list[str] = DEFAULT_PROTECTED_PATHS
    # A change touching these forces human approval before DONE.
    sensitive_paths: list[str] = [
        "**/migrations/**", "migrations/**", "**/auth/**", "auth/**", "deploy/**",
        ".github/workflows/**", "**/*secret*", "**/*credential*", "Dockerfile", "**/Dockerfile",
    ]
    # Env var names (fnmatch) stripped from the Codex/verification environment.
    env_deny: list[str] = DEFAULT_ENV_DENY
    env_allow: list[str] = []

    @model_validator(mode="after")
    def enforce_safety_floor(self) -> BridgeConfig:
        self.protected_paths = list(dict.fromkeys([*DEFAULT_PROTECTED_PATHS, *self.protected_paths]))
        self.env_deny = list(dict.fromkeys([*DEFAULT_ENV_DENY, *self.env_deny]))
        return self

    @model_validator(mode="after")
    def validate_accounts(self) -> BridgeConfig:
        for name in self.accounts:
            if name == "default" or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name) is None:
                raise ValueError(f"invalid or reserved account name: {name}")
        if self.default_account != "default" and self.default_account not in self.accounts:
            raise ValueError(f"unknown default_account: {self.default_account}")
        fallbacks = self.auto_switch.fallback_accounts
        if len(fallbacks) != len(set(fallbacks)):
            raise ValueError("duplicate fallback_accounts")
        for name in fallbacks:
            if name != "default" and name not in self.accounts:
                raise ValueError(f"unknown fallback account: {name}")
        return self


def load_config(root: Path) -> BridgeConfig:
    path = root / AI_DIR / CONFIG_FILE
    if not path.exists():
        return BridgeConfig()
    return BridgeConfig.model_validate(json.loads(path.read_text()))


def state_dir(root: Path) -> Path:
    """Create private mutable state for this worktree."""
    path = storage.locate(root, create=True).dir
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def ensure_ai_dir(root: Path) -> None:
    path = root / AI_DIR
    path.mkdir(parents=True, exist_ok=True)
    try:
        with (path / ".gitignore").open("x") as f:
            f.write("*\n!.gitignore\n!config.json\n")
    except FileExistsError:
        pass
