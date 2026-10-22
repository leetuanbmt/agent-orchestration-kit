"""opencode and Antigravity (agy) as read-only planner/reviewer agents, with fake CLIs."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bridge import accounts, cli
from bridge.config import BridgeConfig
from bridge.models import Actor
from bridge.orchestrator import legacy, parse
from tests.conftest import run
from tests.integration.test_orchestration import WRITE, events, log

# F: agy plans, Codex executes, opencode reviews.
CONFIG_F = """
version: 1
accounts:
  cx: {provider: openai, home: {home}/cx}
  gm: {provider: google, home: {home}/gm}
  oc: {provider: opencode}
agents:
  agy: {adapter: agy-cli, account: gm, model: gemini-3.8-flash-low}
  codex: {adapter: codex-cli, account: cx}
  opencode: {adapter: opencode-cli, account: oc, model: ollama-cloud/gpt-oss:120b}
roles: {planner: agy, executor: codex, reviewer: opencode}
workflow: {stages: [plan, execute, test, review]}
"""


# @pt.id KIT-FLOW-F-agy-codex-opencode
# @pt.item Config F: agy planner, Codex executor, opencode reviewer
# @pt.condition fake agy and opencode; opencode wraps its JSON reply in a code fence
# @pt.expected agy plans read-only (plan mode, sandbox, prompt on stdin, its own HOME); opencode reviews with writes, shell and web denied and the schema in its prompt; the task is DONE with the reviewer recorded as actor agent
# @pt.target bridge.executor.AgyAdapter, bridge.executor.OpenCodeAdapter, Bridge.plan, Bridge._review_stage
# @pt.method integration
# @pt.legacy none
def test_config_f(make_orch, fake_codex, fake_others, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    monkeypatch.setenv("FAKE_OPENCODE_SCENARIO", "fenced")
    b = make_orch(CONFIG_F)
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    wf = run(b.run_workflow(wid, wait=True))
    assert wf["state"] == "DONE", wf

    planner, = log(fake_others / "agy")
    argv = planner["argv"]
    assert argv[argv.index("--mode") + 1] == "plan" and "--sandbox" in argv and "-p=" in argv
    assert "PLANNER" not in " ".join(argv)  # the prompt is not on the command line
    message = json.loads(planner["stdin"])
    assert message["event"] == "user" and "PLANNER" in message["message"]["content"]
    assert planner["home"].endswith("/gm") and planner["agent_env"] == "1"

    reviewer, = log(fake_others / "opencode")
    assert json.loads(reviewer["config"]) == {"permission": {
        "edit": "deny", "bash": "deny", "webfetch": "deny", "external_directory": "deny"}}
    assert reviewer["project_config_disabled"] == "1"
    assert "REVIEWER" in reviewer["prompt"] and '"decision"' in reviewer["prompt"]
    assert reviewer["argv"][reviewer["argv"].index("-m") + 1] == "ollama-cloud/gpt-oss:120b"
    review, = events(b, wf["tasks"][0]["task_id"], "review")
    assert review["actor"] == Actor.AGENT and review["detail"]["reviewer"] == "opencode"


# @pt.id KIT-FLOW-F2-reviewer-quota
# @pt.item An opencode reviewer that runs out of credit
# @pt.condition opencode answers with error provider.quota (402)
# @pt.expected the review stage fails as agent_review_failed, the task stays in REVIEW for the client, and the opencode account is marked quota
# @pt.target bridge.executor.OpenCodeAdapter.handle, Bridge._review_stage
# @pt.method integration
# @pt.legacy none
def test_opencode_quota(make_orch, fake_codex, fake_others, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    monkeypatch.setenv("FAKE_OPENCODE_SCENARIO", "quota")
    b = make_orch(CONFIG_F.replace("[plan, execute, test, review]", "[execute, test, review]")
                  .replace("planner: agy, ", ""))
    task = run(b.create_task({"objective": "Set x to 2", "allowed_paths": ["src/**"],
                              "acceptance_criteria": ["x is 2"]}))["task_id"]
    run(b.execute_task(task, wait=True))
    assert b.store.get(task)["state"] == "REVIEW"
    failed, = events(b, task, "agent_review_failed")
    assert "quota" in json.dumps(failed["detail"])
    assert b.store.account_state("oc")["state"] == "quota"


# @pt.id KIT-CFG-READONLY-adapters
# @pt.item opencode and agy cannot be configured to write
# @pt.condition allow_write, a write capability, the executor role, or an opencode account with a home
# @pt.expected each is rejected with a reason; reading roles stay valid
# @pt.target bridge.orchestrator.AgentConfig, bridge.orchestrator.AccountProfile
# @pt.method unit
# @pt.legacy none
READ_ONLY_BASE = """
accounts:
  cx: {provider: openai}
  gm: {provider: google}
  oc: {OC}
agents:
  agy: {AGY}
  opencode: {adapter: opencode-cli, account: oc}
  codex: {adapter: codex-cli, account: cx}
roles: {ROLES}
"""


@pytest.mark.parametrize("oc,agy,roles,message", [
    ("{provider: opencode}", "{adapter: agy-cli, account: gm, allow_write: true}",
     "{executor: codex}", "read-only"),
    ("{provider: opencode}", "{adapter: agy-cli, account: gm, capabilities: [write]}",
     "{executor: codex}", "read-only"),
    ("{provider: opencode}", "{adapter: agy-cli, account: gm}",
     "{executor: opencode}", "write"),
    ("{provider: opencode, home: /tmp/oc}", "{adapter: agy-cli, account: gm}",
     "{executor: codex}", "global login"),
])
def test_read_only_adapters(oc, agy, roles, message):
    def text(oc, agy, roles):
        return READ_ONLY_BASE.replace("{OC}", oc).replace("{AGY}", agy).replace("{ROLES}", roles)
    parse(text("{provider: opencode}", "{adapter: agy-cli, account: gm}",
               "{planner: agy, executor: codex, reviewer: opencode}"))
    with pytest.raises(ValueError, match=message):
        parse(text(oc, agy, roles))


def _human(monkeypatch):
    """Pretend a human runs the CLI in a terminal, outside any git project."""
    def nowhere():
        raise RuntimeError("not a git repository")
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: True))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)  # keep capsys capturing
    monkeypatch.setattr(cli, "find_root", nowhere)


# @pt.id KIT-ACCT-REG-001-register
# @pt.item Machine-wide registry: register, shell switch, unregister
# @pt.condition accounts added under ~/.agent-bridge for openai and google, plus invalid requests
# @pt.expected fresh private homes (openai with the file credential store), eval-able switch code, duplicate/shared/opencode/bad names refused, removal deletes only registry-owned homes
# @pt.target bridge.accounts.register, bridge.accounts.unregister, bridge.accounts.shell_use
# @pt.method unit
# @pt.legacy none
def test_registry(tmp_path):
    work = accounts.register("work", "openai")
    assert work.home == accounts.registry_dir() / "homes" / "work"
    assert work.home.stat().st_mode & 0o777 == 0o700
    assert "file" in (work.home / "config.toml").read_text()
    assert accounts.shell_use(work) == f"export CODEX_HOME={work.home}"
    gm = accounts.register("gm", "google")
    assert accounts.shell_use(gm) == f'agy() {{ HOME={gm.home} command agy "$@"; }}'
    outside = tmp_path / "mine"
    accounts.register("mine", "anthropic", str(outside))
    assert accounts.load_registry()["mine"] == {"provider": "anthropic",
                                                "home": str(outside.resolve())}
    for args, message in [(("work", "openai"), "already exists"),
                          (("other", "anthropic", str(outside)), "already the home"),
                          (("oc", "opencode"), "global login"),
                          (("Bad Name", "openai"), "invalid"),
                          (("default", "openai"), "reserved")]:
        with pytest.raises(ValueError, match=message):
            accounts.register(*args)
    assert accounts.unregister("work") is True and not work.home.exists()
    outside.mkdir()
    assert accounts.unregister("mine") is False and outside.exists()
    assert set(accounts.load_registry()) == {"gm"}


# @pt.id KIT-ACCT-REG-002-cli
# @pt.item account-add / account-use / account-remove through the CLI, outside a project
# @pt.condition fake agy: a fresh google account is not signed in until its interactive login runs
# @pt.expected account-add registers and logs in inside the new HOME without touching the global login; account-use prints the switch code; account-remove needs the typed name; non-tty use is refused
# @pt.target bridge.cli._run
# @pt.method integration
# @pt.legacy none
def test_registry_cli(fake_others, monkeypatch, capsys):
    _human(monkeypatch)
    global_token = Path.home() / ".gemini/antigravity-cli/antigravity-oauth-token"
    before = global_token.stat().st_mtime_ns
    add = cli.argparse.Namespace(cmd="account-add", name="gm", provider="google", home=None,
                                 device_auth=False)
    assert asyncio.run(cli._run(add)) == {"account": "gm", "provider": "google",
                                          "logged_in": True}
    home = Path.home() / ".agent-bridge/homes/gm"
    assert (home / ".gemini/antigravity-cli/antigravity-oauth-token").exists()
    assert global_token.stat().st_mtime_ns == before

    capsys.readouterr()
    asyncio.run(cli._run(cli.argparse.Namespace(cmd="account-use", name="gm", shell=False)))
    assert capsys.readouterr().out.strip() == f'agy() {{ HOME={home} command agy "$@"; }}'
    with pytest.raises(ValueError, match="eval"):
        asyncio.run(cli._run(cli.argparse.Namespace(cmd="account-use", name="gm", shell=True)))

    remove = cli.argparse.Namespace(cmd="account-remove", name="gm")
    monkeypatch.setattr("builtins.input", lambda prompt: "nope")
    with pytest.raises(SystemExit):
        asyncio.run(cli._run(remove))
    monkeypatch.setattr("builtins.input", lambda prompt: "gm")
    assert asyncio.run(cli._run(remove)) == {"account": "gm", "home_deleted": True}
    assert not home.exists()

    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: False))
    with pytest.raises(SystemExit) as refused:
        asyncio.run(cli._run(add))
    assert refused.value.code == 2


# @pt.id KIT-ACCT-REG-003-merge
# @pt.item Registry accounts in a project's configuration
# @pt.condition a registry Codex account, with and without orchestrator.yaml; the project redeclares one name
# @pt.expected legacy setups gain a Codex agent for it without changing the executor order; orchestrator.yaml can use it by name and its own declaration wins
# @pt.target bridge.orchestrator.legacy, bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_registry_merge():
    registry = {"spare": {"provider": "openai", "home": "/srv/spare"},
                "gm": {"provider": "google", "home": "/srv/gm"}}
    orch = legacy(BridgeConfig(), registry)
    assert orch.agents["spare"].adapter == "codex-cli" and "gm" not in orch.agents
    assert orch.role("executor").agents == ["default"]
    orch = parse("""
accounts:
  gm: {provider: google}
agents:
  codex: {adapter: codex-cli, account: spare}
  agy: {adapter: agy-cli, account: gm}
roles: {executor: codex, reviewer: agy}
""", registry=registry)
    assert str(orch.accounts["spare"].home) == "/srv/spare"
    assert orch.accounts["gm"].home is None
