"""Human views of the bridge's data, shared by the CLI (in a terminal) and the dashboard.

Each `*_view` turns the exact dict/list the MCP tools return into rich renderables and ends
with the commands for the next step, so the JSON stays the machine contract.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from .accounts import display_name
from .quota import remaining_pct as _remaining

# What a human may have to do, by state; drives the "needs you" filter and the row colours.
ATTENTION = {"APPROVED": "approve", "REVIEW": "review", "PAUSED": "resume or fix account",
             "BLOCKED": "decide", "FAILED": "retry or cancel"}
STYLE = {"DONE": "green", "APPROVED": "bold yellow", "REVIEW": "yellow", "PAUSED": "magenta",
         "BLOCKED": "bold red", "FAILED": "red", "CANCELLED": "dim", "RUNNING": "cyan",
         "VERIFYING": "cyan"}


def quota_text(quota: Any, short: bool = False) -> str:
    left = "" if short else " left"
    if not isinstance(quota, dict):
        return f"{quota:.0f}%{left}" if isinstance(quota, (int, float)) else str(quota)
    parts = []
    for key, label in (("primary", "5h"), ("secondary", "wk" if short else "week")):
        window = quota.get(key)
        if isinstance(window, dict) and window.get("used_percent") is not None:
            # Same rule as routing: a window whose reset time has passed is full again.
            pct = _remaining({key: window}, time.time())
            if pct is not None:
                parts.append(f"{label} {pct:.0f}%{left}")
    return " · ".join(parts) or "UNKNOWN"


def remaining_pct(quota: Any) -> float | None:
    """The tighter of the 5h / week windows, as % left; None when no CLI has reported it."""
    if isinstance(quota, (int, float)):
        return float(quota)
    return _remaining(quota, time.time()) if isinstance(quota, dict) else None


def ago(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "-"
    s = max(0, int((now or time.time()) - ts))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return f"{s}s ago"


def _steps(hints: list[str], title: str = "Next:") -> list[RenderableType]:
    if not hints:
        return []
    out = Text(f"\n{title}\n", style="bold")
    for hint in hints:
        out.append(f"  {hint}\n", style="")
    return [out]


def account_rows(listing: dict[str, Any],
                 profiles: bool = True) -> list[tuple[str, tuple[Any, ...]]]:
    """Every account the machine knows about; only rows keyed by a bare name are usable here.

    codex-switch homes can be added; switch profiles hold copied tokens and are never used,
    so `profiles=False` leaves them out."""
    rows: list[tuple[str, tuple[Any, ...]]] = []
    for acc in listing["accounts"]:
        # The stored state only changes after a failed run; a failed login probe outranks it.
        state = (Text("logged out", style="red") if acc.get("logged_in") is False
                 else acc.get("state", "-"))
        # Keyed by id; shown by name (the Provider column tells a reused name apart).
        rows.append((acc["name"], (display_name(acc["name"]), acc.get("provider", "openai"),
                                   acc["source"],
                                   "yes" if acc.get("logged_in") else "no", state,
                                   quota_text(acc.get("quota")),
                                   acc.get("home") or "(global login)")))
    for home in listing["codex_switch"]["homes"]:
        if not home["configured"]:
            rows.append((f"codex-switch:{home['name']}", (
                home["name"], "openai", "codex-switch home", "yes" if home["logged_in"] else "no",
                Text("not added", style="yellow"), "-", home["codex_home"])))
    for profile in listing["codex_switch"]["profiles"] if profiles else []:
        rows.append((f"codex-profile:{profile['name']}", (
            profile["name"], "openai", "codex-switch profile", "-",
            Text("not usable: copied tokens", style="dim"), "-", "-")))
    for profile in listing.get("claude_switch", {}).get("profiles", []) if profiles else []:
        rows.append((f"claude-switch:{profile['name']}", (
            profile["name"], "anthropic", "claude-switch profile", "-",
            Text("not usable: copied tokens", style="dim"), "-", profile.get("email") or "-")))
    for cli in listing.get("unconfigured", []):
        rows.append((f"installed:{cli['provider']}", (
            "-", cli["provider"], "installed", "-", Text("no account", style="yellow"),
            "-", cli["binary"])))
    return rows


# Below this many columns the table drops Source and Home and shortens the quota.
NARROW = 120


def accounts_view(listing: dict[str, Any], width: int = NARROW,
                  show_all: bool = False) -> Group:
    """Usable accounts only unless `show_all`; rows keyed by a bare name are the accounts."""
    rows = account_rows(listing)
    shown = [(k, c) for k, c in rows if show_all or ":" not in k]
    quotas = {a["name"]: a.get("quota") for a in listing["accounts"]}
    if width < NARROW:
        table = Table("Account", "Provider", "Login", "State", "Quota")
        for key, c in shown:
            table.add_row(c[0], c[1], c[3], c[4],
                          quota_text(quotas[key], short=True) if key in quotas else "-")
    else:
        table = Table("Account", "Provider", "Source", "Logged in", "State", "Quota", "Home")
        for _, c in shown:
            table.add_row(*c)
    hints = [f"agent-bridge account-add {h['name']} --provider openai --home {h['codex_home']}"
             for h in listing["codex_switch"]["homes"] if not h["configured"]]
    hints += [cli["hint"] for cli in listing.get("unconfigured", [])]
    hidden = len(rows) - len(shown)
    steps = _steps(hints, "To add an account:")
    gap = "" if steps else "\n"  # the hints already end with a newline
    note = Text(f"{gap}{hidden} more not usable here (switch profiles, homes not added, CLIs "
                "without an account): --all to list them." if hidden else
                f"{gap}switch profiles hold copied tokens: listed only, never used.",
                style="dim")
    note.append(" --json for the full data.")
    return Group(table, *steps, note)


def health_view(h: dict[str, Any]) -> Group:
    head = Text()
    head.append(" OK " if h["ok"] else " NOT OK ",
                style="bold black on green" if h["ok"] else "bold white on red")
    head.append(f"  {h['project_root']}\n", style="bold")
    orch = h["orchestrator"]
    head.append(f"config {orch['source']} · stages {' → '.join(orch['stages'])}"
                f" · sandbox {h['sandbox']}", style="dim")
    adapters = Table("Adapter", "Path", "Version", title="Adapters", title_justify="left")
    hints = []
    for name, info in h["adapters"].items():
        missing = not info["path"]
        adapters.add_row(name, Text("not installed", style="red") if missing else info["path"],
                         info["version"] or "-")
        if missing:
            hints.append(f"install the {name.removesuffix('-cli')} CLI (or set its *_bin in "
                         ".ai/config.json)")
    codex = h["codex"]
    if "codex-cli" in h["adapters"] and codex.get("path") and not codex.get("logged_in"):
        hints.append("codex is not logged in: run `codex login` yourself, or "
                     "`agent-bridge account-add <name> --provider openai`")
    quota = Table("Account", "Quota", title="Quota", title_justify="left")
    for name, value in h["quota"].items():
        quota.add_row(name, quota_text(value))
    counts = Text("Tasks  ")
    for state, n in sorted(h["task_counts"].items()):
        counts.append(f"{state} {n}", style=STYLE.get(state, ""))
        counts.append("  ")
    if not h["task_counts"]:
        counts.append("none", style="dim")
    if h["active_tasks"]:
        counts.append(f"\nActive  {', '.join(h['active_tasks'])}", style="cyan")
    return Group(head, Text(""), adapters, quota, counts, *_steps(hints, "To fix:"))


def agents_view(a: dict[str, Any]) -> Group:
    head = Text(f"config {a['source']} · stages {' → '.join(a['stages'])}\n", style="dim")
    for role, spec in a["roles"].items():
        head.append(f"{role:<9}", style="bold")
        head.append(f"{', '.join(spec['agents']) or '-'}\n")
    fallback = a["quota_fallback"]
    head.append(f"quota fallback {'on' if fallback['enabled'] else 'off'}", style="dim")
    table = Table("Agent", "Adapter", "Account", "Model", "Roles", "Can", "Status")
    for ag in a["agents"]:
        can = "/".join(c for c in ("read", "write") if ag[c]) + (" +bash" if ag["allow_bash"]
                                                                  else "")
        status = (Text("available", style="green") if ag["available"]
                  else Text(ag["unavailable_reason"] or "unavailable", style="red"))
        table.add_row(ag["agent"], ag["adapter"], ag["account"] or "-", ag["model"] or "-",
                      ", ".join(ag["roles"]) or "-", can or "-", status)
    hints = [f"`agent-bridge account-list` to see why {ag['agent']} is unavailable"
             for ag in a["agents"] if not ag["available"] and ag["account"]][:1]
    return Group(head, table, *_steps(hints))


def _next_for(task: dict[str, Any]) -> str | None:
    tid = task["task_id"]
    match task["state"]:
        case "APPROVED" if task["needs_human_approval"]:
            return f"agent-bridge approve {tid}"
        case "REVIEW" if not task["review_pending"]:
            return f"agent-bridge result {tid}   then: agent-bridge review {tid} --decision …"
        case "PAUSED":
            return f"agent-bridge resume {tid}   (after fixing: {task['failure'] or 'see result'})"
        case "FAILED" | "BLOCKED":
            return f"agent-bridge result {tid}   (resume or cancel)"
    return None


def tasks_view(tasks: list[dict[str, Any]], now: float | None = None,
               project: str | None = None) -> Group | Text:
    """`project` is the root the tasks belong to: each project (worktree) has its own tasks."""
    head = Text()
    if project:
        head.append(f"project {Path(project).name}", style="bold")
        head.append(f"  {project}", style="dim")
    if not tasks:
        head.append(("\n" if project else "") + "No tasks yet. Ask Claude Code for a change; it "
                    "creates them through the bridge.", style="dim")
        return head
    table = Table("", "Task", "State", "Agent", "Tries", "Updated", expand=True)
    table.add_column("Objective", ratio=1, no_wrap=True, overflow="ellipsis")
    for t in tasks:
        mark = Text("●", style="bold yellow") if t["state"] in ATTENTION else ""
        table.add_row(mark, t["task_id"], Text(t["state"], style=STYLE.get(t["state"], "")),
                      t["agent"] or "-", f"{t['attempts']}/{t['max_attempts']}",
                      ago(t["updated_at"], now), (t["objective"].splitlines() or [""])[0])
    hints = [hint for t in tasks if (hint := _next_for(t))]
    return Group(*([head] if project else []), table, *_steps(hints, "● needs you:"))
