"""Unit tests: task contract, state machine, config, path rules, redaction, adapter argv/parsing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from bridge.accounts import Account
from bridge.config import BridgeConfig
from bridge.executor import CodexAdapter, build_prompt, classify
from bridge.models import TERMINAL, Actor, State, TaskSpec, TransitionError, check_transition
from bridge.review import parse_command
from bridge.security import PathError, clean_env, matches, redact, validate_pattern

PROTECTED = BridgeConfig().protected_paths


# @pt.id KIT-UNIT-001-spec-validation
# @pt.item Task contract validation
# @pt.condition spec missing allowed_paths / with unknown field / with danger sandbox
# @pt.expected pydantic rejects each spec
# @pt.target bridge.models.TaskSpec
# @pt.method unit
# @pt.legacy none (new kit)
def test_task_spec_rejects_invalid() -> None:
    ok = {"objective": "o", "allowed_paths": ["src"], "acceptance_criteria": ["a"]}
    TaskSpec.model_validate(ok)
    for bad in ({**ok, "allowed_paths": []}, {**ok, "surprise": 1},
                {**ok, "sandbox": "danger-full-access"}, {**ok, "max_attempts": 9},
                {k: v for k, v in ok.items() if k != "acceptance_criteria"}):
        with pytest.raises(ValidationError):
            TaskSpec.model_validate(bad)


# @pt.id KIT-UNIT-002-transition-ownership
# @pt.item State machine legality and ownership
# @pt.condition Codex/bridge tries to approve; claude approves; illegal jump; terminal exit
# @pt.expected only listed (from,to,actor) triples pass; terminal states have no exits
# @pt.target bridge.models.check_transition
# @pt.method unit
# @pt.legacy none (new kit)
def test_transitions_enforce_actor_and_legality() -> None:
    check_transition(State.REVIEW, State.APPROVED, Actor.CLAUDE)
    with pytest.raises(TransitionError):
        check_transition(State.REVIEW, State.APPROVED, Actor.BRIDGE)  # executor side can't approve
    with pytest.raises(TransitionError):
        check_transition(State.READY, State.DONE, Actor.CLAUDE)
    with pytest.raises(TransitionError):
        check_transition(State.APPROVED, State.DONE, Actor.CLAUDE)  # closes via gates/human only
    for t in TERMINAL:
        for dst in State:
            for actor in Actor:
                with pytest.raises(TransitionError):
                    check_transition(t, dst, actor)


# @pt.id KIT-UNIT-003-config-parsing
# @pt.item Project config parsing
# @pt.condition unknown key, unrestricted sandbox
# @pt.expected both rejected; defaults keep workspace-write and 3 attempts
# @pt.target bridge.config.BridgeConfig
# @pt.method unit
# @pt.legacy none (new kit)
def test_config_rejects_unknown_and_unsafe() -> None:
    assert BridgeConfig().sandbox == "workspace-write" and BridgeConfig().max_attempts == 3
    with pytest.raises(ValidationError):
        BridgeConfig.model_validate({"typo_key": 1})
    with pytest.raises(ValidationError):
        BridgeConfig.model_validate({"sandbox": "danger-full-access"})


# @pt.id KIT-UNIT-004-path-restrictions
# @pt.item Path canonicalization, traversal, symlink escape, protected paths
# @pt.condition absolute, .., ~, symlinked dir pointing outside, .env, .git, over-broad glob
# @pt.expected every unsafe pattern raises PathError; normal relative paths are accepted
# @pt.target bridge.security.validate_pattern
# @pt.method unit
# @pt.legacy none (new kit)
def test_path_validation(tmp_path: Path) -> None:
    root = (tmp_path / "r").resolve()
    (root / "src").mkdir(parents=True)
    os.symlink(tmp_path, root / "escape")
    assert validate_pattern("src/**/*.py", root, PROTECTED) == "src/**/*.py"
    assert validate_pattern("./src/", root, PROTECTED) == "src"
    for bad in ("/etc/passwd", "../x", "src/../../x", "~/x", "escape/x", ".env", ".git",
                ".git/config", "**", "*", ".ai/bridge.db", "", "a\\b", "deep/key.pem"):
        with pytest.raises(PathError):
            validate_pattern(bad, root, PROTECTED)


# @pt.id KIT-UNIT-005-glob-semantics
# @pt.item Allowed-path matching
# @pt.condition `*` vs `**`, bare directory, nested protected file
# @pt.expected * stays in one dir, ** crosses dirs, bare dir covers its subtree
# @pt.target bridge.security.matches
# @pt.method unit
# @pt.legacy none (new kit)
def test_glob_semantics() -> None:
    assert matches("src/a.py", ["src/*.py"]) and not matches("src/x/a.py", ["src/*.py"])
    assert matches("src/x/a.py", ["src/**"]) and matches("src/x/a.py", ["src"])
    assert not matches("srcx/a.py", ["src"])
    assert matches("pkg/.env", PROTECTED) and matches(".env.local", PROTECTED)


# @pt.id KIT-UNIT-006-redaction
# @pt.item Secret redaction in logs and evidence
# @pt.condition OpenAI key, GitHub token, bearer header, key=value password, private key block
# @pt.expected none of the secret values survive
# @pt.target bridge.security.redact
# @pt.method unit
# @pt.legacy none (new kit)
def test_redact_masks_credentials() -> None:
    raw = ("key sk-abcdefghijklmnopqrstuvwx and ghp_abcdefghijklmnopqrstuvwxyz12 "
           "Authorization: Bearer abc.def.ghijklmnop password=hunter2hunter "
           "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----")
    out = redact(raw)
    for secret in ("sk-abcdefghijkl", "ghp_abcdefghij", "abc.def.ghijklmnop", "hunter2hunter",
                   "MIIE"):
        assert secret not in out
    assert redact("x" * 10, limit=4) == "…xxxx"


# @pt.id KIT-UNIT-007-env-scrub
# @pt.item Environment passed to Codex and verification commands
# @pt.condition env holds API keys/tokens plus PATH/HOME
# @pt.expected credential-shaped vars dropped unless explicitly allowed
# @pt.target bridge.security.clean_env
# @pt.method unit
# @pt.legacy none (new kit)
def test_clean_env() -> None:
    env = {"PATH": "/bin", "HOME": "/h", "OPENAI_API_KEY": "k", "GH_TOKEN": "t", "DB_PASSWORD": "p"}
    cfg = BridgeConfig()
    assert clean_env(env, cfg.env_deny, []) == {"PATH": "/bin", "HOME": "/h"}
    assert "GH_TOKEN" in clean_env(env, cfg.env_deny, ["GH_TOKEN"])


# @pt.id KIT-UNIT-008-argv-fresh-and-resume
# @pt.item Codex argv construction
# @pt.condition fresh exec vs resume; unrestricted sandbox requested
# @pt.expected fresh uses -s/-C, resume uses -c sandbox_mode + session id; /tmp excluded;
#              prompt on stdin; named account overrides precede session/stdin, default has none; danger-full-access refused
# @pt.target bridge.executor.CodexAdapter.argv
# @pt.method unit
# @pt.legacy codex-cli 0.163.0 `exec --help` / `exec resume --help`
def test_codex_argv(tmp_path: Path) -> None:
    ad = CodexAdapter(BridgeConfig(model="gpt-x"), tmp_path)
    default = Account("default", None)
    named = Account("work", tmp_path / "account")
    fresh = ad.argv(account=default, sandbox="workspace-write", last_msg=tmp_path / "m")
    assert fresh[:3] == ["codex", "exec", "--json"] and fresh[-1] == "-"
    assert fresh[fresh.index("-s") + 1] == "workspace-write" and "-C" in fresh
    assert "--ignore-user-config" in fresh and "--dangerously-bypass-approvals-and-sandbox" \
        not in fresh
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in fresh
    res = ad.argv(account=default, sandbox="read-only", last_msg=tmp_path / "m", session_id="abc")
    assert res[:3] == ["codex", "exec", "resume"] and res[-2:] == ["abc", "-"]
    assert 'sandbox_mode="read-only"' in res and "-s" not in res and "-C" not in res
    override = 'cli_auth_credentials_store="file"'
    assert override not in fresh and override not in res
    for session_id in (None, "abc"):
        argv = ad.argv(account=named, sandbox="read-only", last_msg=tmp_path / "m",
                       session_id=session_id)
        index = argv.index(override)
        assert argv[index - 1] == "-c"
        assert index < argv.index("-m")
        assert argv[-1] == "-"
        if session_id:
            assert argv[-2] == session_id
    with pytest.raises(ValueError):
        ad.argv(account=default, sandbox="danger-full-access", last_msg=tmp_path / "m")


# @pt.id KIT-UNIT-009-error-classification
# @pt.item Failure classification from Codex error text
# @pt.condition usage-limit text, 401 text (observed on codex 0.163), stream error, unknown
# @pt.expected quota / auth / network / crash; quota wins over generic words
# @pt.target bridge.executor.classify
# @pt.method unit
# @pt.legacy probe output of `CODEX_HOME=<empty> codex exec`
def test_classify() -> None:
    assert classify("You've hit your usage limit. try again at 5 PM") == "quota"
    assert classify("unexpected status 429 Too Many Requests") == "quota"
    assert classify("unexpected status 401 Unauthorized: Missing bearer") == "auth"
    assert classify("stream disconnected before completion") == "network"
    assert classify("thread 'main' panicked") == "crash"


# @pt.id KIT-UNIT-010-verify-command-parse
# @pt.item Verification command parsing
# @pt.condition allowlisted command with quotes; shell; non-allowlisted binary; empty
# @pt.expected argv split without a shell; sh/curl/empty rejected
# @pt.target bridge.review.parse_command
# @pt.method unit
# @pt.legacy none (new kit)
def test_parse_command() -> None:
    cfg = BridgeConfig()
    assert parse_command("uv run pytest -k 'a and b'", cfg) == ["uv", "run", "pytest", "-k",
                                                                "a and b"]
    for bad in ("sh -c 'rm -rf /'", "curl http://x", "", "bash x.sh"):
        with pytest.raises(ValueError):
            parse_command(bad, cfg)


# @pt.id KIT-UNIT-011-prompt-contract
# @pt.item Executor prompt
# @pt.condition spec with paths, criteria, commands
# @pt.expected prompt lists allowed + protected paths, criteria, no-commit rule, JSON output
# @pt.target bridge.executor.build_prompt
# @pt.method unit
# @pt.legacy none (new kit)
def test_build_prompt() -> None:
    s = TaskSpec(objective="obj", allowed_paths=["src"], acceptance_criteria=["crit"],
                 verification_commands=["pytest"]).model_dump()
    p = build_prompt("T0001", s, [".env"])
    for frag in ("T0001", "obj", "- src", "- .env", "crit", "`pytest`", "Do not create commits",
                 "output schema"):
        assert frag in p
