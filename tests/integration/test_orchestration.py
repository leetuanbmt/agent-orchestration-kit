"""Configurable roles, routing and workflows with fake Codex and Claude CLIs (configs A-E)."""

import json
import sqlite3

import pytest

from bridge.executor import ClaudeAdapter, RunRequest
from bridge.models import Actor, State
from bridge.state import Store
from tests.conftest import run, spec
from tests.integration.test_accounts_routing import calls

CHECK = spec()["verification_commands"][0]
WRITE = "src/app.py=x = 2\n"

ACCOUNTS = """
version: 1
accounts:
  cx1: {provider: openai, home: {home}/cx1}
  cx2: {provider: openai, home: {home}/cx2}
  cl: {provider: anthropic, home: {home}/cl}
"""
# A: Claude planner -> Codex executor -> Claude reviewer.
CONFIG_A = ACCOUNTS + """
agents:
  claude: {adapter: claude-cli, account: cl}
  codex: {adapter: codex-cli, account: cx1}
roles: {planner: claude, executor: codex, reviewer: claude}
workflow: {stages: [plan, execute, test, review]}
"""
# B: Codex planner -> Claude executor -> Codex reviewer.
CONFIG_B = ACCOUNTS + """
agents:
  codex: {adapter: codex-cli, account: cx1}
  claude: {adapter: claude-cli, account: cl, allow_write: true}
roles: {planner: codex, executor: claude, reviewer: codex}
workflow: {stages: [plan, execute, test, review]}
"""
# C: Claude planner -> two Codex executors on separate accounts.
CONFIG_C = ACCOUNTS + """
agents:
  claude: {adapter: claude-cli, account: cl}
  codex1: {adapter: codex-cli, account: cx1}
  codex2: {adapter: codex-cli, account: cx2}
roles:
  planner: claude
  executor: {agents: [codex1, codex2]}
  reviewer: claude
workflow: {stages: [plan, execute, test, review]}
"""
# D: one agent plays every role.
CONFIG_D = ACCOUNTS + """
agents:
  codex: {adapter: codex-cli, account: cx1}
roles: {planner: codex, executor: codex, reviewer: codex}
workflow: {stages: [plan, execute, test, review]}
"""
# E: no LLM reviewer; deterministic gates only.
CONFIG_E = ACCOUNTS + """
agents:
  codex: {adapter: codex-cli, account: cx1}
roles: {executor: codex}
workflow: {stages: [execute, test]}
"""


def log(state):
    path = state / "log.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def events(bridge, task, kind):
    return [e for e in bridge.store.events(task, 200) if e["kind"] == kind]


def tools(call):
    return call["argv"][call["argv"].index("--allowedTools") + 1].split(",")


def plan_task(**over):
    task = {"objective": "Set x to 2 in src/app.py", "context": "", "scope": "",
            "allowed_paths": ["src/**"], "excluded_paths": [],
            "acceptance_criteria": ["src/app.py contains x = 2"],
            "verification_commands": [CHECK], "depends_on": [], "risk_level": "low"}
    return {**task, **over}


# @pt.id KIT-FLOW-A-claude-codex-claude
# @pt.item Config A: Claude planner, Codex executor, Claude reviewer
# @pt.condition A planned objective with two dependent tasks is driven to completion
# @pt.expected Claude plans read-only without MCP; Codex executes in order; Claude approves as AGENT
# @pt.target Bridge.plan, Bridge.run_workflow, Bridge._review_stage, ClaudeAdapter
# @pt.method integration
# @pt.legacy none
def test_config_a(make_orch, fake_codex, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(
        [plan_task(), plan_task(objective="Keep x at 2", depends_on=[0])]))
    b = make_orch(CONFIG_A)
    wf = run(b.plan("Set x to 2", wait=True))
    assert wf["state"] == "READY" and [t["state"] for t in wf["tasks"]] == ["READY", "BACKLOG"]
    first, second = (t["task_id"] for t in wf["tasks"])
    assert b.store.get(second)["spec"]["depends_on"] == [first]
    planner = log(fake_claude)[0]
    assert "PLANNER" in planner["prompt"] and planner["agent_env"] == "1"
    assert tools(planner) == ["Read", "Glob", "Grep"]
    assert "--strict-mcp-config" in planner["argv"]
    assert planner["config_dir"].endswith("/cl")
    wf = run(b.run_workflow(wf["workflow_id"], wait=True))
    assert wf["state"] == "DONE" and all(t["state"] == "DONE" for t in wf["tasks"])
    assert [t["agent"] for t in wf["tasks"]] == ["codex", "codex"]
    assert [c["codex_home"].rsplit("/", 1)[1] for c in calls(fake_codex)] == ["cx1", "cx1"]
    review, = events(b, first, "review")
    assert review["actor"] == Actor.AGENT and review["detail"]["reviewer"] == "claude"
    assert sum("REVIEWER" in c["prompt"] for c in log(fake_claude)) == 2
    assert b._account_quota("cl")["remaining_pct"] == 80.0


# @pt.id KIT-FLOW-B-codex-claude-codex
# @pt.item Config B: Codex planner, Claude executor, Codex reviewer
# @pt.condition Claude has allow_write; the plan carries a verification command
# @pt.expected Claude gets edit tools but no Bash; Codex plans and reviews read-only; task is DONE
# @pt.target Bridge.plan, Bridge._run_inner, ClaudeAdapter.build, CodexAdapter
# @pt.method integration
# @pt.legacy none
def test_config_b(make_orch, fake_codex, fake_claude, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CODEX_PLAN", json.dumps([plan_task()]))
    b = make_orch(CONFIG_B)
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    wf = run(b.run_workflow(wid, wait=True))
    assert wf["state"] == "DONE"
    task = wf["tasks"][0]["task_id"]
    result = run(b.task_result(task))
    assert result["agent"] == "claude" and result["account"] == "cl"
    assert result["attempts"][-1]["verification"]["checks"][0]["passed"]
    assert result["reviews"][-1]["reviewer"] == "codex"
    executor, = log(fake_claude)
    assert set(tools(executor)) == {"Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit"}
    assert executor["argv"][executor["argv"].index("--permission-mode") + 1] == "dontAsk"
    planner, reviewer = calls(fake_codex)
    for call in (planner, reviewer):
        assert call["argv"][call["argv"].index("-s") + 1] == "read-only"
    assert "PLANNER" in planner["prompt"] and "REVIEWER" in reviewer["prompt"]
    acc = b._account("cl")
    argv = ClaudeAdapter(b.cfg, b.root).build(
        RunRequest("p", acc, "workspace-write", 60, allow_bash=True), tmp_path)
    assert "Bash" in argv[argv.index("--allowedTools") + 1].split(",")


# @pt.id KIT-FLOW-C-two-executors
# @pt.item Config C: two Codex executor accounts
# @pt.condition The first executor's account is busy with another task
# @pt.expected The task routes to the second executor and its account home
# @pt.target Bridge._route, Bridge._pick, Store.transition
# @pt.method integration
# @pt.legacy none
def test_config_c_busy(make_orch, fake_codex, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    b = make_orch(CONFIG_C)
    busy = run(b.create_task(spec(agent="codex1", objective="Busy", sandbox="read-only")))
    b.store.transition(busy["task_id"], State.RUNNING, Actor.CLIENT, expect={State.READY})
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    wf = run(b.run_workflow(wid, wait=True))
    task = wf["tasks"][0]
    assert wf["state"] == "DONE" and task["agent"] == "codex2" and task["account"] == "cx2"
    routed, = events(b, task["task_id"], "agent_routed")
    assert routed["detail"] == {"role": "executor", "agent": "codex2", "account": "cx2"}
    assert calls(fake_codex)[-1]["codex_home"].endswith("/cx2")


# @pt.id KIT-FLOW-C-quota
# @pt.item Config C: quota fallback between executor accounts
# @pt.condition The first account hits its quota, with fallback enabled and disabled
# @pt.expected Enabled: the driver resumes on codex2 and finishes; disabled: the workflow waits
# @pt.target Bridge._quota_fallback, Bridge._drive, Bridge._rebind
# @pt.method integration
# @pt.legacy auto_switch (0.1.x)
@pytest.mark.parametrize("enabled", [True, False])
def test_config_c_quota(make_orch, fake_codex, fake_claude, monkeypatch, enabled):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "quota,ok")
    config = CONFIG_C + f"routing: {{quota_fallback: {{enabled: {str(enabled).lower()}}}}}\n"
    b = make_orch(config)
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    wf = run(b.run_workflow(wid, wait=True))
    task = wf["tasks"][0]
    assert b.store.account_state("cx1")["state"] == "quota"
    if not enabled:
        assert wf["state"] == "WAITING" and task["state"] == "PAUSED"
        assert len(calls(fake_codex)) == 1
        return
    assert wf["state"] == "DONE" and task["agent"] == "codex2"
    moved, = events(b, task["task_id"], "account_auto_switched")
    assert moved["actor"] == Actor.BRIDGE and moved["detail"] == {
        "from": "codex1", "to": "codex2", "from_account": "cx1", "to_account": "cx2",
        "reason": "quota", "remaining_pct": 0, "to_remaining_pct": None}


# @pt.id KIT-FLOW-D-single-agent
# @pt.item Config D: single-agent workflow
# @pt.condition One Codex agent is planner, executor and reviewer
# @pt.expected Three Codex runs (plan, execute, review); plan and review are read-only
# @pt.target Bridge.plan, Bridge.run_workflow
# @pt.method integration
# @pt.legacy none
def test_config_d(make_orch, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    b = make_orch(CONFIG_D)
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    assert run(b.run_workflow(wid, wait=True))["state"] == "DONE"
    sandboxes = [c["argv"][c["argv"].index("-s") + 1] for c in calls(fake_codex)]
    assert sandboxes == ["read-only", "workspace-write", "read-only"]


# @pt.id KIT-FLOW-E-gates-only
# @pt.item Config E: no LLM reviewer
# @pt.condition Passing checks, failing checks, and a high-risk task
# @pt.expected Bridge approves only verified work; failures rework; high risk waits for a human
# @pt.target Bridge._review_stage, Bridge._apply_review
# @pt.method integration
# @pt.legacy none
def test_config_e(make_orch, fake_codex, monkeypatch):
    b = make_orch(CONFIG_E)
    failing = run(b.create_task(spec(objective="Fails")))["task_id"]
    assert run(b.execute_task(failing, wait=True))["state"] == "REWORK"
    assert not events(b, failing, "review")
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    assert run(b.resume_task(failing, wait=True))["state"] == "DONE"
    review, = events(b, failing, "review")
    assert review["actor"] == Actor.BRIDGE and review["detail"]["reviewer"] == "bridge"
    risky = run(b.create_task(spec(risk_level="high")))["task_id"]
    out = run(b.execute_task(risky, wait=True))
    assert out["state"] == "APPROVED" and out["needs_human_approval"]
    with pytest.raises(ValueError, match="no plan stage"):
        run(b.plan("x"))


# @pt.id KIT-ROUTE-001-pinning
# @pt.item Explicit selection and the explicit strategy
# @pt.condition Agent, account, mismatched, unknown and read-only selections; strategy explicit
# @pt.expected Valid pins bind agent and account; invalid ones are refused with the reason
# @pt.target Bridge._bind
# @pt.method integration
# @pt.legacy none
def test_pinning(make_orch, fake_codex):
    b = make_orch(CONFIG_C)
    by_account = run(b.create_task(spec(account="cx2", objective="by account")))
    assert (by_account["agent"], by_account["account"]) == ("codex2", "cx2")
    by_agent = run(b.create_task(spec(agent="codex1", objective="by agent")))
    assert (by_agent["agent"], by_agent["account"]) == ("codex1", "cx1")
    unpinned = run(b.create_task(spec(objective="unpinned")))
    assert unpinned["agent"] is None and unpinned["account"] is None
    for over, message in (({"agent": "codex1", "account": "cx2"}, "uses account cx1"),
                          ({"agent": "ghost"}, "unknown agent"),
                          ({"agent": "claude"}, "cannot execute"),
                          ({"account": "nope"}, "unknown account"),
                          ({"account": "cl"}, "no executor agent uses account cl")):
        with pytest.raises(ValueError, match=message):
            run(b.create_task(spec(objective=str(over), **over)))
    explicit = make_orch(CONFIG_C.replace("{agents: [codex1, codex2]}",
                                          "{agents: [codex1, codex2], strategy: explicit}"))
    with pytest.raises(ValueError, match="strategy explicit"):
        run(explicit.create_task(spec(objective="needs agent")))


# @pt.id KIT-ROUTE-002-affinity
# @pt.item Routed tasks keep their agent and session
# @pt.condition An unpinned task is routed, reworked by the client, then resumed
# @pt.expected The resume continues the same session on the same account
# @pt.target Bridge._route, Bridge.resume_task
# @pt.method integration
# @pt.legacy none
def test_affinity(make_orch, fake_codex):
    b = make_orch(CONFIG_C.replace("reviewer: claude", "reviewer: me")
                  .replace("  claude: {adapter: claude-cli, account: cl}",
                           "  claude: {adapter: claude-cli, account: cl}\n"
                           "  me: {adapter: client}"))
    task = run(b.create_task(spec(verification_commands=[])))["task_id"]
    first = run(b.execute_task(task, wait=True))
    assert first["state"] == "REVIEW" and first["agent"] == "codex1"
    run(b.review_task(task, "rework", "Again"))
    second = run(b.resume_task(task, wait=True))
    assert second["agent"] == "codex1" and second["session_id"] == first["session_id"]
    assert calls(fake_codex)[-1]["argv"][:2] == ["exec", "resume"]
    assert len(events(b, task, "agent_routed")) == 1


# @pt.id KIT-ROUTE-003-no-eligible
# @pt.item Routing with every executor unavailable
# @pt.condition Both executor accounts are marked auth-failed
# @pt.expected Execution is refused with a per-agent reason; nothing runs
# @pt.target Bridge._pick
# @pt.method integration
# @pt.legacy none
def test_no_eligible(make_orch, fake_codex):
    b = make_orch(CONFIG_C)
    for name in ("cx1", "cx2"):
        b.store.set_account_state(name, "auth", "401", actor=Actor.BRIDGE)
    task = run(b.create_task(spec()))["task_id"]
    with pytest.raises(ValueError, match="codex1: account cx1 auth; codex2: account cx2 auth"):
        run(b.execute_task(task, wait=True))
    assert not calls(fake_codex)


# @pt.id KIT-REVIEW-001-bounded-rework
# @pt.item Reviewer agent keeps asking for rework
# @pt.condition max_rework 1 and a Claude reviewer that always answers rework
# @pt.expected Two executor runs, then BLOCKED; the workflow ends INCOMPLETE
# @pt.target Bridge._drive, Bridge._start
# @pt.method integration
# @pt.legacy none
def test_bounded_rework(make_orch, fake_codex, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_DECISION", "rework:src/app.py still wrong")
    b = make_orch(CONFIG_A.replace("[plan, execute, test, review]}",
                                   "[plan, execute, test, review], max_rework: 1}"))
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    wf = run(b.run_workflow(wid, wait=True))
    assert wf["state"] == "INCOMPLETE" and wf["tasks"][0]["state"] == "BLOCKED"
    assert len(calls(fake_codex)) == 2
    assert "src/app.py still wrong" in calls(fake_codex)[-1]["prompt"]


# @pt.id KIT-REVIEW-002-reviewer-failure
# @pt.item Reviewer agent crash
# @pt.condition The Claude reviewer run crashes after a verified execution
# @pt.expected The task stays in REVIEW, the workflow waits, the client override closes it
# @pt.target Bridge._review_stage, Bridge.review_task
# @pt.method integration
# @pt.legacy none
def test_reviewer_failure(make_orch, fake_codex, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "ok,crash")
    b = make_orch(CONFIG_A)
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    wf = run(b.run_workflow(wid, wait=True))
    task = wf["tasks"][0]["task_id"]
    assert wf["state"] == "WAITING" and wf["tasks"][0]["state"] == "REVIEW"
    assert events(b, task, "agent_review_failed")[0]["detail"]["agent"] == "claude"
    assert run(b.review_task(task, "approve", "Verified the diff myself"))["state"] == "DONE"


# @pt.id KIT-REVIEW-003-claude-quota
# @pt.item Claude executor hits its usage limit
# @pt.condition Config B executor run returns a 429 usage-limit result
# @pt.expected PAUSED with QUOTA, account marked quota, budget not consumed, limits stored
# @pt.target ClaudeAdapter.handle, Bridge._record_account
# @pt.method integration
# @pt.legacy none
def test_claude_quota(make_orch, fake_codex, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "quota")
    monkeypatch.setenv("FAKE_CLAUDE_UTIL", "1.0")
    b = make_orch(CONFIG_B)
    task = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(task, wait=True))
    assert out["state"] == "PAUSED" and out["failure"].startswith("QUOTA")
    assert out["attempts"] == 0 and b.store.account_state("cl")["state"] == "quota"
    assert b._account_quota("cl")["remaining_pct"] == 0.0


# @pt.id KIT-PLAN-001-errors
# @pt.item Planner refusals and invalid plans
# @pt.condition Client planner; plans with a forward dependency or a protected path
# @pt.expected Client planner refused; invalid plans end FAILED without creating tasks
# @pt.target Bridge.plan, Bridge._plan
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("bad,message", [
    (plan_task(depends_on=[0]), "depends_on"),
    (plan_task(allowed_paths=[".env"]), "protected"),
])
def test_plan_errors(make_orch, fake_claude, monkeypatch, bad, message):
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps([bad]))
    b = make_orch(CONFIG_A)
    wf = run(b.plan("x", wait=True))
    assert wf["state"] == "FAILED" and message in wf["detail"]["error"]
    assert wf["tasks"] == [] and b.store.list() == []


# @pt.id KIT-PLAN-002-client-planner
# @pt.item Legacy setup has no planner agent
# @pt.condition plan is called on a bridge without orchestrator.yaml
# @pt.expected Refused: the legacy workflow has no plan stage
# @pt.target Bridge.plan
# @pt.method integration
# @pt.legacy .ai/config.json (0.1.x)
def test_client_planner(make_bridge, make_orch):
    with pytest.raises(ValueError, match="no plan stage"):
        run(make_bridge().plan("x"))
    client = make_orch(CONFIG_E.replace("roles: {executor: codex}",
                                        "roles: {executor: codex, planner: me}")
                       .replace("  codex: {adapter: codex-cli, account: cx1}",
                                "  codex: {adapter: codex-cli, account: cx1}\n"
                                "  me: {adapter: client}")
                       .replace("[execute, test]", "[plan, execute, test]"))
    with pytest.raises(ValueError, match="planner is the MCP client"):
        run(client.plan("x"))


# @pt.id KIT-SURF-001-agents-health
# @pt.item Agent listing and health per adapter
# @pt.condition Config A with both fake CLIs installed
# @pt.expected Agents show roles and availability; health lists both adapters and is ok
# @pt.target Bridge.list_agents, Bridge.health
# @pt.method integration
# @pt.legacy none
def test_agents_health(make_orch, fake_claude):
    b = make_orch(CONFIG_A)
    agents = {a["agent"]: a for a in b.list_agents()["agents"]}
    assert agents["claude"]["roles"] == ["planner", "reviewer"] and not agents["claude"]["write"]
    assert agents["codex"]["write"] and agents["codex"]["available"]
    h = run(b.health())
    assert set(h["adapters"]) == {"claude-cli", "codex-cli"} and h["ok"]
    assert h["quota"] == {"cx1": "UNKNOWN", "cx2": "UNKNOWN", "cl": "UNKNOWN"}


# @pt.id KIT-STATE-010-schema-upgrade
# @pt.item Databases from 0.1.x gain the new columns
# @pt.condition A tasks table without agent and workflow_id columns is reopened
# @pt.expected The columns are added and existing rows keep their account
# @pt.target Store.__init__
# @pt.method integration
# @pt.legacy bridge.db schema (0.1.x)
def test_schema_upgrade(tmp_path):
    path = tmp_path / "bridge.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, spec TEXT NOT NULL, account TEXT, "
               "spec_hash TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL "
               "DEFAULT 0, session_id TEXT, runner_pid INTEGER, codex_pid INTEGER, failure TEXT,"
               " needs_human INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, "
               "updated_at REAL NOT NULL)")
    db.execute("INSERT INTO tasks(id, spec, account, spec_hash, state, created_at, updated_at) "
               "VALUES ('T0001', ?, 'a', 'h', 'REVIEW', 0, 0)", (json.dumps(spec()),))
    db.commit()
    db.close()
    row = Store(path).get("T0001")
    assert row["account"] == "a" and row["agent"] is None and row["workflow_id"] is None
