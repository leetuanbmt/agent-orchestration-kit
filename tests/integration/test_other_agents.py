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
from bridge.render import account_rows
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
# @pt.condition accounts added under ~/.agent-bridge for openai, google and opencode, plus invalid requests
# @pt.expected fresh private homes (openai with the file credential store), eval-able switch code, one opencode account on the global login, duplicate/shared/second-opencode/opencode-home/bad names refused, removal deletes only registry-owned homes
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
    oc = accounts.register("oc", "opencode")
    assert oc.home is None and accounts.load_registry()["oc"] == {"provider": "opencode",
                                                                  "home": None}
    for args, message in [(("work", "openai"), "already exists"),
                          (("other", "anthropic", str(outside)), "already the home"),
                          (("oc2", "opencode"), "already uses the global opencode login"),
                          (("oc3", "opencode", str(tmp_path / "x")), "no isolated home"),
                          (("Bad Name", "openai"), "invalid"),
                          (("default", "openai"), "reserved")]:
        with pytest.raises(ValueError, match=message):
            accounts.register(*args)
    assert accounts.unregister("work") is True and not work.home.exists()
    outside.mkdir()
    assert accounts.unregister("mine") is False and outside.exists()
    assert accounts.unregister("oc") is False
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


# @pt.id KIT-ACCT-REG-004-same-name
# @pt.item One account name used by several providers
# @pt.condition `tuanvm` is added for openai, then for google; config agents refer to `tuanvm`
# @pt.expected the second account gets the id tuanvm@google and its own home; a repeat for one provider is refused; each agent gets the account of its adapter's provider; typed names need --provider only when ambiguous; listings show the plain name
# @pt.target bridge.accounts.register, bridge.accounts.pick_account, bridge.orchestrator.parse, bridge.render.account_rows
# @pt.method unit
# @pt.legacy none
def test_same_name_accounts():
    first = accounts.register("tuanvm", "openai")
    second = accounts.register("tuanvm", "google")
    assert (first.name, second.name) == ("tuanvm", "tuanvm@google")
    assert second.home == accounts.registry_dir() / "homes" / "tuanvm@google"
    with pytest.raises(ValueError, match="already exists"):
        accounts.register("tuanvm", "google")
    registry = accounts.load_registry()
    known = {k: e["provider"] for k, e in registry.items()}
    assert accounts.pick_account("tuanvm", "google", known) == "tuanvm@google"
    assert accounts.pick_account("tuanvm", "openai", known) == "tuanvm"
    assert accounts.pick_account("tuanvm@google", None, known) == "tuanvm@google"
    with pytest.raises(ValueError, match="add --provider"):
        accounts.pick_account("tuanvm", None, known)
    with pytest.raises(ValueError, match="no anthropic account"):
        accounts.pick_account("tuanvm", "anthropic", known)
    orch = parse("""
agents:
  codex: {adapter: codex-cli, account: tuanvm}
  agy: {adapter: agy-cli, account: tuanvm}
roles: {executor: codex, reviewer: agy}
""", registry=registry)
    assert orch.agents["codex"].account == "tuanvm"
    assert orch.agents["agy"].account == "tuanvm@google"
    assert "tuanvm-google" not in legacy(BridgeConfig(), registry).agents  # google: no agent
    listing = {"accounts": [{"name": "tuanvm@google", "provider": "google", "source": "registry",
                             "logged_in": True, "state": "available"}],
               "codex_switch": {"homes": [], "profiles": []}}
    assert account_rows(listing)[0][1][:2] == ("tuanvm", "google")


# @pt.id KIT-ACCT-REG-005-list-outside
# @pt.item account-list outside a project
# @pt.condition no git project in the current directory; a registry account exists
# @pt.expected a one-line "not a git repository" error and exit 1, not a traceback
# @pt.target bridge.cli._run
# @pt.method unit
# @pt.legacy none
def test_list_outside_project(monkeypatch, capsys):
    accounts.register("work", "openai")
    _human(monkeypatch)
    with pytest.raises(SystemExit) as failed:
        cli.main(["account-list"])
    assert failed.value.code == 1
    assert "not a git repository" in capsys.readouterr().err


# @pt.id KIT-ACCT-012-agy-login-without-starting-agy
# @pt.item agy login checks never start agy
# @pt.condition google accounts whose HOME has no token, an empty token, a token; the global login; agy missing from PATH
# @pt.expected logged in only with a non-empty token file, unknown without the CLI, and agy is never started (starting it under an account HOME pops macOS "Keychain Not Found")
# @pt.target bridge.accounts.login_status
# @pt.method integration
# @pt.legacy login_status ran `agy models` under the account HOME
def test_agy_login_status_reads_token_file(fake_others, tmp_path, monkeypatch):
    from bridge.accounts import AGY_TOKEN, Account, login_status
    cfg = BridgeConfig()
    home = tmp_path / "gm"
    acc = Account("gm", home, "google")
    assert login_status(cfg, acc) is False
    (home / AGY_TOKEN).parent.mkdir(parents=True)
    (home / AGY_TOKEN).touch()
    assert login_status(cfg, acc) is False
    (home / AGY_TOKEN).write_text("token")
    assert login_status(cfg, acc) is True
    assert login_status(cfg, Account("global", None, "google")) is True  # fixture's $HOME token
    assert not (fake_others / "agy" / "models_called").exists()
    assert login_status(BridgeConfig(agy_bin="agy-not-installed"), acc) is None


# Cursor (cursor-agent): a read-only planner and reviewer on its global login.
CONFIG_CURSOR = """
version: 1
accounts:
  cx: {provider: openai, home: {home}/cx}
  cu: {provider: cursor}
agents:
  codex: {adapter: codex-cli, account: cx}
  cursor: {adapter: cursor-cli, account: cu, model: auto}
roles: {planner: cursor, executor: codex, reviewer: cursor}
workflow: {stages: [plan, execute, test, review]}
"""


def _cursor_request(**over):
    from bridge.executor import RunRequest
    base = {"prompt": "REVIEWER", "account": accounts.Account("cu", None, "cursor"),
            "sandbox": "read-only", "timeout_s": 60, "schema": {"properties": {"decision": {}}}}
    return RunRequest(**{**base, **over})


# @pt.id KIT-FLOW-F3-cursor-planner-reviewer
# @pt.item Config with Cursor as planner and reviewer
# @pt.condition fake cursor-agent; Codex executes
# @pt.expected the workflow reaches DONE with the review recorded as an agent review by cursor; every Cursor run is in ask mode with the sandbox enabled and trust, the prompt on stdin with the schema, and none of the write or approve flags
# @pt.target bridge.executor.CursorAdapter, Bridge.plan, Bridge._review_stage
# @pt.method integration
# @pt.legacy none
def test_cursor_planner_and_reviewer(make_orch, fake_codex, fake_others, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    b = make_orch(CONFIG_CURSOR)
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    wf = run(b.run_workflow(wid, wait=True))
    assert wf["state"] == "DONE", wf
    planner, reviewer = log(fake_others / "cursor")
    for call in (planner, reviewer):
        argv = call["argv"]
        assert argv[argv.index("--mode") + 1] == "ask"
        assert argv[argv.index("--sandbox") + 1] == "enabled" and "--trust" in argv
        assert argv[argv.index("--model") + 1] == "auto"
        assert not {"-f", "--yolo", "--force", "--approve-mcps"} & set(argv)
        assert call["agent_env"] == "1"
    assert '"tasks"' in planner["prompt"] and '"decision"' in reviewer["prompt"]
    assert "REVIEWER" in reviewer["prompt"]
    review, = events(b, wf["tasks"][0]["task_id"], "review")
    assert review["actor"] == Actor.AGENT and review["detail"]["reviewer"] == "cursor"


# @pt.id KIT-CFG-READONLY-cursor
# @pt.item cursor-cli cannot be configured to write
# @pt.condition allow_write, a write capability, the executor role, or a cursor account with a home
# @pt.expected each is rejected with a reason naming read-only, write or `cursor-agent login`; reading roles stay valid
# @pt.target bridge.orchestrator.AgentConfig, bridge.orchestrator.AccountProfile
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("account,agent,roles,message", [
    ("{provider: cursor}", "{adapter: cursor-cli, account: cu, allow_write: true}",
     "{executor: codex}", "read-only"),
    ("{provider: cursor}", "{adapter: cursor-cli, account: cu, capabilities: [write]}",
     "{executor: codex}", "read-only"),
    ("{provider: cursor}", "{adapter: cursor-cli, account: cu}", "{executor: cursor}", "write"),
    ("{provider: cursor, home: /tmp/cu}", "{adapter: cursor-cli, account: cu}",
     "{executor: codex}", "cursor-agent login"),
    ("{provider: opencode, home: /tmp/cu}", "{adapter: opencode-cli, account: cu}",
     "{executor: codex}", "opencode auth login"),
])
def test_cursor_read_only(account, agent, roles, message):
    def text(account, agent, roles):
        return f"""
accounts:
  cx: {{provider: openai}}
  cu: {account}
agents:
  cursor: {agent}
  codex: {{adapter: codex-cli, account: cx}}
roles: {roles}
"""
    parse(text("{provider: cursor}", "{adapter: cursor-cli, account: cu}",
               "{planner: cursor, executor: codex, reviewer: cursor}"))
    with pytest.raises(ValueError, match=message):
        parse(text(account, agent, roles))


# @pt.id KIT-ADAPTER-cursor-last-assistant
# @pt.item Cursor's final answer comes from the last assistant event
# @pt.condition the fake emits two assistant messages and a result that concatenates them; one run resumes a session
# @pt.expected final is the JSON of the last assistant message, usage is kept as reported with no cost, the session id is reported, and --resume carries the given id
# @pt.target bridge.executor.CursorAdapter.handle, bridge.executor.CursorAdapter.argv
# @pt.method integration
# @pt.legacy none
def test_cursor_last_assistant_event(repo, fake_others, monkeypatch):
    from bridge.executor import CursorAdapter
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "multi")
    seen = []
    adapter = CursorAdapter(BridgeConfig(), repo)
    res = run(adapter.run(_cursor_request(session_id="sess-1"), on_session=seen.append))
    assert res.outcome == "ok" and res.final == {"decision": "approve", "notes": "looks good"}
    assert res.final_text.startswith("{") and res.cost_usd is None
    assert res.usage == {"inputTokens": 1, "outputTokens": 1, "cacheReadTokens": 1,
                         "cacheWriteTokens": 0, "reasoningTokens": 1}
    assert seen == ["sess-1"] and res.session_id == "sess-1"
    call, = log(fake_others / "cursor")
    assert call["argv"][call["argv"].index("--resume") + 1] == "sess-1"
    assert call["argv"][call["argv"].index("--workspace") + 1] == str(repo)
    assert "--resume" not in adapter.argv(_cursor_request())


# @pt.id KIT-ADAPTER-cursor-error-result
# @pt.item A Cursor error result fails the turn
# @pt.condition the fake answers with a result of subtype error and is_error true
# @pt.expected the run is not ok and the error text carries the subtype and the result text
# @pt.target bridge.executor.CursorAdapter.handle, bridge.executor.CursorAdapter.finish
# @pt.method integration
# @pt.legacy none
def test_cursor_error_result(repo, fake_others, monkeypatch):
    from bridge.executor import CursorAdapter
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "error")
    res = run(CursorAdapter(BridgeConfig(), repo).run(_cursor_request()))
    assert res.outcome != "ok" and res.final is None
    assert "error: Usage limit reached" in (res.error or "")


# @pt.id KIT-ADAPTER-cursor-hooks-refused
# @pt.item Cursor is refused in a directory with project hooks
# @pt.condition the run directory contains .cursor/hooks.json, and a write run is requested elsewhere
# @pt.expected a ValueError naming the file and saying Cursor would execute it; the fake CLI is never started; a write run is refused as read-only
# @pt.target bridge.executor.CursorAdapter.argv, bridge.executor.ReadOnlyAdapter.build
# @pt.method integration
# @pt.legacy none
def test_cursor_refuses_project_hooks(repo, fake_others):
    from bridge.executor import CursorAdapter
    adapter = CursorAdapter(BridgeConfig(), repo)
    with pytest.raises(ValueError, match="read-only"):
        run(adapter.run(_cursor_request(sandbox="workspace-write")))
    (repo / ".cursor").mkdir()
    (repo / ".cursor" / "hooks.json").write_text("{}")
    with pytest.raises(ValueError, match=r"hooks\.json.*execute"):
        run(adapter.run(_cursor_request()))
    assert not (fake_others / "cursor" / "calls").exists()


# @pt.id KIT-ADAPTER-cursor-claude-hooks-refused
# @pt.item Cursor is refused when .claude settings define hooks or cannot be read
# @pt.condition .claude/settings.json or settings.local.json holds hooks, an empty hooks value, no hooks, invalid JSON or a JSON list
# @pt.expected hooks and unreadable files are refused with a message naming the file and saying Cursor would execute its hooks; the others are not; the fake CLI is never started for a refusal
# @pt.target bridge.executor.CursorAdapter.refusal, bridge.executor.CursorAdapter.argv
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
@pytest.mark.parametrize("content,refused", [
    ('{"hooks": {"PreToolUse": []}}', True), ('{"hooks": {"Stop": [{"hooks": []}]}}', True),
    ("{not json", True), ("[]", True),
    ('{"hooks": {}}', False), ('{"permissions": {"deny": []}}', False)])
def test_cursor_refuses_claude_settings_hooks(repo, fake_others, name, content, refused):
    from bridge.executor import CursorAdapter
    settings = repo / ".claude" / name
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(content)
    adapter = CursorAdapter(BridgeConfig(), repo)
    if refused:
        assert name in (adapter.refusal() or "") and adapter.refusal_file() == settings
        with pytest.raises(ValueError, match=rf"{name}.*Cursor would execute its hooks"):
            run(adapter.run(_cursor_request()))
        assert not (fake_others / "cursor" / "calls").exists()
    else:
        assert adapter.refusal() is None
        assert run(adapter.run(_cursor_request())).outcome == "ok"


CONFIG_CURSOR_REVIEW = CONFIG_CURSOR.replace(
    "roles: {planner: cursor, executor: codex, reviewer: cursor}",
    "roles: {planner: codex, executor: codex, reviewer: cursor}").replace(
    "[plan, execute, test, review]", "[execute, test, review]")


# @pt.id KIT-ADAPTER-cursor-refusal-surfaced
# @pt.item A refused Cursor reviewer is unavailable, skipped, and reported by health
# @pt.condition Cursor reviews, Codex executes, and .claude/settings.json holds hooks
# @pt.expected the agent is listed unavailable with the refusal reason, the task stays in REVIEW with an agent_review_skipped event, the fake CLI never runs, and health carries an adapter_refused warning whose fix names the file
# @pt.target bridge.service.Bridge._unavailable, bridge.service.Bridge.list_agents, bridge.service.Bridge.health, bridge.runner.TaskRunner._review_stage
# @pt.method integration
# @pt.legacy none
def test_cursor_refusal_is_surfaced(make_orch, fake_codex, fake_others, monkeypatch):
    from tests.conftest import spec
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    b = make_orch(CONFIG_CURSOR_REVIEW)
    settings = b.root / ".claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text('{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}')
    listed = {a["agent"]: a for a in b.list_agents(logins={"cx": True, "cu": True})["agents"]}
    assert listed["cursor"]["available"] is False
    assert "settings.json" in listed["cursor"]["unavailable_reason"]
    assert "Cursor would execute its hooks" in listed["cursor"]["unavailable_reason"]
    t = run(b.create_task(spec(objective="Set x to 2")))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == "REVIEW"
    skipped, = events(b, t, "agent_review_skipped")
    assert "settings.json" in skipped["detail"]["reason"] or "reviewer" in skipped["detail"]["reason"]
    assert not (fake_others / "cursor" / "calls").exists()
    health = run(b.health())
    warning, = [w for w in health["warnings"] if w["code"] == "adapter_refused"]
    assert str(settings) in warning["fix"] and "another reviewer/planner agent" in warning["fix"]
    assert "adapter_refused" not in [x["code"] for x in health["problems"]]


# @pt.id KIT-ADAPTER-cursor-result-text
# @pt.item The result text answers when the last assistant message holds no JSON
# @pt.condition the fake answers with the JSON first and a prose message last; the result text joins both
# @pt.expected the run is ok and final is the JSON object from the result text
# @pt.target bridge.executor.CursorAdapter.finish
# @pt.method integration
# @pt.legacy none
def test_cursor_result_text_fallback(repo, fake_others, monkeypatch):
    from bridge.executor import CursorAdapter
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "early")
    res = run(CursorAdapter(BridgeConfig(), repo).run(_cursor_request()))
    assert res.final_text == "Done."
    assert res.outcome == "ok" and res.final == {"decision": "approve", "notes": "looks good"}


# @pt.id KIT-HEALTH-cursor-unprobed
# @pt.item A Cursor CLI newer than the probed version is a health warning
# @pt.condition the cursor adapter reports 2026.11.11 (probed), then 2026.12.1
# @pt.expected only 2026.12.1 adds cli_unprobed
# @pt.target bridge.service.Bridge.health, bridge.service.Bridge.PROBED_VERSIONS
# @pt.method integration
# @pt.legacy none
def test_cursor_unprobed_version(make_orch, monkeypatch):
    from bridge.executor import CursorAdapter
    b = make_orch(CONFIG_CURSOR)
    for version, warned in (("2026.11.11", False), ("2026.12.1", True)):
        monkeypatch.setattr(CursorAdapter, "version", lambda self, v=version: v)
        warnings = run(b.health())["warnings"]
        assert any(w["code"] == "cli_unprobed" and "cursor-cli" in w["message"]
                   for w in warnings) is warned, version


# @pt.id KIT-ACCT-cursor-status-env
# @pt.item The Cursor login check does not see CURSOR_API_KEY
# @pt.condition CURSOR_API_KEY is set in the environment while the status command runs against the fake
# @pt.expected the fake logs a status call with no CURSOR_API_KEY
# @pt.target bridge.accounts.login_status
# @pt.method integration
# @pt.legacy none
def test_cursor_status_env_is_scrubbed(fake_others, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "key-for-test")
    assert accounts.login_status(BridgeConfig(), accounts.Account("cu", None, "cursor")) is True
    calls = [json.loads(line) for line in
             (fake_others / "cursor" / "status.jsonl").read_text().splitlines()]
    assert calls == [{"cursor_api_key": None}]


# @pt.id KIT-CFG-cursor-no-budget
# @pt.item A cursor account cannot carry a budget or pricing
# @pt.condition limits with budget_usd, with pricing, and with percentage and cooldown only; also through a registry login
# @pt.expected budget and pricing are rejected with a message saying Cursor reports no cost; percentage and cooldown limits load
# @pt.target bridge.orchestrator.AccountProfile
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("limits,ok", [
    ("{budget_usd: {day: 5}}", False),
    ("{pricing: {input_per_mtok: 1, output_per_mtok: 1}}", False),
    ("{five_hour_pct: 80, cooldown_s: 600}", True)])
@pytest.mark.parametrize("via_login", [False, True])
def test_cursor_cost_limits(limits, ok, via_login):
    account = "{login: cu, limits: %s}" % limits if via_login else (
        "{provider: cursor, limits: %s}" % limits)
    text = f"""
accounts:
  cx: {{provider: openai}}
  cu: {account}
agents:
  cursor: {{adapter: cursor-cli, account: cu}}
  codex: {{adapter: codex-cli, account: cx}}
roles: {{planner: cursor, executor: codex, reviewer: cursor}}
"""
    registry = {"cu": {"provider": "cursor", "home": None}} if via_login else None
    if ok:
        parse(text, registry=registry)
    else:
        with pytest.raises(ValueError, match="Cursor reports no cost"):
            parse(text, registry=registry)


# @pt.id KIT-ACCT-cursor-registry
# @pt.item Cursor accounts: add, second refused, login status
# @pt.condition account-add --provider cursor, a second cursor account, a home, fake status with and without a login, shell switch
# @pt.expected one home-less cursor account, second and home refused, logged in only when isAuthenticated is true, login runs `cursor-agent login`, shell switch keeps the global login
# @pt.target bridge.accounts.register, bridge.accounts.login_status, bridge.accounts.login_argv, bridge.cli._run
# @pt.method integration
# @pt.legacy none
def test_cursor_accounts(fake_others, monkeypatch):
    _human(monkeypatch)
    add = cli.argparse.Namespace(cmd="account-add", name="cu", provider="cursor", home=None,
                                 device_auth=False)
    monkeypatch.setattr(cli, "_login", lambda *a, **k: True)
    assert asyncio.run(cli._run(add))["account"] == "cu"
    assert accounts.load_registry()["cu"] == {"provider": "cursor", "home": None}
    for args, message in [(("cu2", "cursor"), "already uses the global cursor login"),
                          (("cu3", "cursor", "/tmp/x"), "no isolated home")]:
        with pytest.raises(ValueError, match=message):
            accounts.register(*args)
    acc = accounts.Account("cu", None, "cursor")
    cfg = BridgeConfig()
    assert accounts.login_status(cfg, acc) is True
    assert accounts.login_argv(cfg, acc) == ["cursor-agent", "login"]
    assert accounts.shell_use(acc) == "true  # cursor always uses its global login"
    monkeypatch.setenv("FAKE_CURSOR_LOGGED_OUT", "1")
    assert accounts.login_status(cfg, acc) is False
    assert accounts.login_status(BridgeConfig(cursor_bin="cursor-not-installed"), acc) is None
