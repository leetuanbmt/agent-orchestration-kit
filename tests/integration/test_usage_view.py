"""Per-account usage and limits as people and MCP clients see them (read-only)."""

import asyncio
import io
import json
import time

import pytest
from rich.console import Console

from bridge import cli, server
from bridge.render import accounts_view, usage_view
from bridge.service import Bridge, dumps
from tests.conftest import run

CONFIG = """
version: 1
accounts:
  lim: {provider: anthropic, home: {home}/lim, limits: {five_hour_pct: 80}}
  okk: {provider: anthropic, home: {home}/okk, limits: {budget_usd: {day: 5}}}
  plain: {provider: anthropic, home: {home}/plain}
  dis: {provider: anthropic, home: {home}/dis, limits: {enabled: false, five_hour_pct: 80}}
agents:
  a1: {adapter: claude-cli, account: lim, allow_write: true}
  a2: {adapter: claude-cli, account: okk, allow_write: true}
  a3: {adapter: claude-cli, account: plain, allow_write: true}
  a4: {adapter: claude-cli, account: dis, allow_write: true}
roles: {executor: {agents: [a1, a2, a3, a4]}}
workflow: {stages: [execute, test]}
"""


def seed(b: Bridge, name: str, used: float) -> None:
    b.store.save_quota(name, {"primary": {"used_percent": used, "window_minutes": 300,
                                          "resets_at": time.time() + 3600}}, key=b._key(name))


def spend(b: Bridge, name: str, cost: float) -> None:
    b.store.record_usage(name, key=b._usage_key(name), task_id=None, cost_usd=cost,
                         source="actual")


@pytest.fixture
def bridge(make_orch):
    b = make_orch(CONFIG)
    seed(b, "lim", 90)
    seed(b, "dis", 90)
    spend(b, "okk", 0.6)
    spend(b, "plain", 1.25)
    return b


def _render(renderable) -> str:
    out = io.StringIO()
    Console(file=out, width=200, force_terminal=False, color_system=None).print(renderable)
    return out.getvalue()


# @pt.id KIT-USAGEVIEW-001-report
# @pt.item usage_report covers every account with state, limits and quota
# @pt.condition four accounts: lim (five_hour 80, 90 % used), okk (day budget 5, 0.60 spent), plain (no limits, 1.25 spent), dis (disabled limits, 90 % used)
# @pt.expected limited / ok / none / disabled statuses with their checks, state and quota per account, and generated_at
# @pt.target bridge.service.Bridge.usage_report
# @pt.method integration
# @pt.legacy none
def test_usage_report_statuses(bridge):
    report = bridge.usage_report()
    assert isinstance(report["generated_at"], float)
    accounts = {a["name"]: a for a in report["accounts"]}
    assert list(accounts) == ["lim", "okk", "plain", "dis"]
    for acc in accounts.values():
        assert acc["provider"] == "anthropic"
        assert acc["state"]["state"] == "available"
        assert "quota" in acc
    lim = accounts["lim"]["limits"]
    assert lim["status"] == "limited" and lim["reason"]
    check, = lim["checks"]
    assert check["limit"] == "five_hour_pct" and check["breached"] and check["used"] == 90
    assert accounts["lim"]["quota"]["primary"]["used_percent"] == 90
    okk = accounts["okk"]["limits"]
    assert okk["status"] == "ok"
    budget, = okk["checks"]
    assert budget["unit"] == "usd" and budget["used"] == pytest.approx(0.6)
    plain = accounts["plain"]["limits"]
    assert plain["status"] == "none" and plain["checks"] == []
    assert plain["spend"]["day"]["spent"] == pytest.approx(1.25)
    assert accounts["plain"]["quota"] == "UNKNOWN"
    dis = accounts["dis"]["limits"]
    assert dis["status"] == "disabled" and not dis["checks"][0]["breached"]


# @pt.id KIT-USAGEVIEW-002-narrow
# @pt.item usage_report narrows to one account; an unknown name is refused
# @pt.condition usage_report("okk"), then usage_report("nope")
# @pt.expected only okk is returned; the unknown name raises ValueError naming the known accounts
# @pt.target bridge.service.Bridge.usage_report
# @pt.method integration
# @pt.legacy none
def test_usage_report_narrow_and_unknown(bridge):
    assert [a["name"] for a in bridge.usage_report("okk")["accounts"]] == ["okk"]
    with pytest.raises(ValueError) as error:
        bridge.usage_report("nope")
    message = str(error.value)
    assert "nope" in message and all(n in message for n in ("lim", "okk", "plain", "dis"))


# @pt.id KIT-USAGEVIEW-003-cli
# @pt.item agent-bridge usage returns the report as JSON-serialisable data
# @pt.condition cli._run with cmd usage, without and with an account; the Bridge is built read-only
# @pt.expected the report of usage_report (narrowed by the account) built with create=False; dumps of it parses as JSON
# @pt.target bridge.cli._run, bridge.cli.main
# @pt.method integration
# @pt.legacy none
def test_cli_usage(bridge, monkeypatch):
    made = []

    def fake(*args, **kwargs):
        made.append(kwargs)
        return bridge
    monkeypatch.setattr(cli, "Bridge", fake)
    out = asyncio.run(cli._run(cli.argparse.Namespace(cmd="usage", account=None, json=True)))
    assert made == [{"create": False}]
    assert [a["name"] for a in out["accounts"]] == ["lim", "okk", "plain", "dis"]
    parsed = json.loads(dumps(out))
    assert parsed["accounts"][0]["limits"]["status"] == "limited"
    one = asyncio.run(cli._run(cli.argparse.Namespace(cmd="usage", account="plain", json=False)))
    assert [a["name"] for a in one["accounts"]] == ["plain"]
    assert cli.VIEWS["usage"] == "usage_view"


# @pt.id KIT-USAGEVIEW-004-view
# @pt.item usage_view renders one row per check, the none row and the human-config note
# @pt.condition the four-account report rendered to a plain console
# @pt.expected the columns, an "over" breached row with 90.0% / 80.0%, a $0.60 budget row, a "(none)" row with today's spend, a disabled row and the note that limits are human-owned config
# @pt.target bridge.render.usage_view
# @pt.method integration
# @pt.legacy none
def test_usage_view(bridge):
    text = _render(usage_view(bridge.usage_report()))
    for column in ("Account", "Limit", "Used", "Threshold", "Remaining", "Resets", "Source",
                   "Status"):
        assert column in text
    lim = next(line for line in text.splitlines() if "lim " in line and "five_hour_pct" in line)
    assert "90.0%" in lim and "80.0%" in lim and "0.0%" in lim and "over" in lim
    assert "actual" in lim
    okk = next(line for line in text.splitlines() if "budget_usd.day" in line)
    assert "$0.60" in okk and "$5.00" in okk and "$4.40" in okk
    assert " ok " in okk and "over" not in okk
    plain = next(line for line in text.splitlines() if "plain" in line)
    assert "(none)" in plain and "$1.25 today" in plain
    dis = next(line for line in text.splitlines() if "dis " in line)
    assert "disabled" in dis and "over" not in dis
    assert ("Limits are set by a human in .ai/orchestrator.yaml (accounts.<name>.limits); "
            "--json for the full data.") in text


def _listing(**acc):
    base = {"name": "cl1", "provider": "anthropic", "source": "project", "logged_in": True,
            "state": "available", "quota": "UNKNOWN", "home": "/h/cl1"}
    return {"accounts": [{**base, **acc}], "codex_switch": {"homes": [], "profiles": []}}


# @pt.id KIT-USAGEVIEW-005-accounts-marker
# @pt.item accounts_view marks a limited account "over limit"; logged out takes priority
# @pt.condition an account whose limits report is limited, logged in and then logged out; then one with ok limits
# @pt.expected "over limit" in the State column when logged in; "logged out" (not "over limit") when logged out; ok limits keep the stored state
# @pt.target bridge.render.account_rows, bridge.render.accounts_view
# @pt.method integration
# @pt.legacy none
def test_accounts_over_limit(bridge):
    limited = _render(accounts_view(_listing(limits={"status": "limited"})))
    assert "over limit" in limited
    out = _render(accounts_view(_listing(limits={"status": "limited"}, logged_in=False)))
    assert "logged out" in out and "over limit" not in out
    ok = _render(accounts_view(_listing(limits={"status": "ok"})))
    assert "over limit" not in ok and "available" in ok
    real = {a["name"]: a for a in bridge.list_accounts()["accounts"]}
    assert real["lim"]["limits"]["status"] == "limited"
    assert "limits" not in real["plain"]


# @pt.id KIT-USAGEVIEW-006-mcp
# @pt.item bridge_account_usage MCP tool returns the usage report
# @pt.condition the server's bridge() is the test bridge; called without and with an account, then with an unknown one
# @pt.expected the same accounts as usage_report (generated_at aside); the unknown account becomes a ToolError
# @pt.target bridge.server.bridge_account_usage
# @pt.method integration
# @pt.legacy none
def test_mcp_account_usage(bridge, monkeypatch):
    monkeypatch.setattr(server, "bridge", lambda: bridge)
    out = run(server.bridge_account_usage())
    assert ([(a["name"], a["limits"]["status"]) for a in out["accounts"]]
            == [(a["name"], a["limits"]["status"]) for a in bridge.usage_report()["accounts"]])
    one = run(server.bridge_account_usage("lim"))
    assert [a["name"] for a in one["accounts"]] == ["lim"]
    with pytest.raises(server.ToolError, match="unknown account"):
        run(server.bridge_account_usage("nope"))
    assert "read-only" in (server.bridge_account_usage.__doc__ or "").lower()


# @pt.id KIT-USAGEVIEW-007-next-run-reserve
# @pt.item Every view agrees with the start gate about the next run's reserve
# @pt.condition rsv has budget_usd day 1.0 and reserve_usd 0.5 and has spent 0.7, so the next run (0.7 + 0.5 > 1.0) is refused
# @pt.expected usage_report, list_accounts and health report rsv limited with the reserve in the reason, as _unavailable does; remaining stays 0.30
# @pt.target Bridge.usage_report, Bridge.list_accounts, Bridge.health, AccountGate._next_run_reserve
# @pt.method integration
# @pt.legacy the views evaluated without the reserve and showed ok while routing refused the account
def test_views_include_next_run_reserve(make_orch):
    b = make_orch("""
version: 1
accounts:
  rsv: {provider: anthropic, home: {home}/rsv, limits: {budget_usd: {day: 1.0}, reserve_usd: 0.5}}
agents:
  a1: {adapter: claude-cli, account: rsv, allow_write: true}
roles: {executor: a1}
workflow: {stages: [execute, test]}
""")
    spend(b, "rsv", 0.7)
    assert "over limit" in b._unavailable("a1", "write")
    usage, = b.usage_report("rsv")["accounts"]
    assert usage["limits"]["status"] == "limited" and "$0.50 reserve" in usage["limits"]["reason"]
    assert abs(usage["limits"]["checks"][0]["remaining"] - 0.3) < 1e-9
    listed = {a["name"]: a for a in b.list_accounts()["accounts"]}["rsv"]
    assert listed["limits"]["status"] == "limited"
    assert run(b.health())["limits"]["rsv"]["status"] == "limited"
