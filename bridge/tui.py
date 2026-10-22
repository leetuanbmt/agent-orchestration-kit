"""Terminal dashboard (`agent-bridge` without a subcommand) for the human side of the bridge.

It reads the same state as the MCP server and offers the human-only actions (approve,
reassign, account login) next to the task list. Agents are never started from here: a run
belongs to the process that started it, and closing the dashboard would interrupt it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label, Select, Static,
                             TabbedContent, TabPane)

from . import install
from .config import AI_DIR, find_root
from .render import ATTENTION, STYLE, account_rows
from .service import Bridge

def detail_text(result: dict[str, Any], tries: int) -> Text:
    """`result` is `task_result`, whose `attempts` is the attempt list; `tries` is the budget used."""
    out = Text()
    out.append(f"{result['task_id']}  ", style="bold")
    out.append(result["state"], style=STYLE.get(result["state"], ""))
    out.append(f"\nagent {result['agent'] or '-'} · account {result['account'] or '-'}"
               f" · tries {tries}/{result['max_attempts']}"
               f" · risk {result['spec'].get('risk_level', 'normal')}\n\n")
    out.append(result["spec"]["objective"] + "\n")
    if need := ATTENTION.get(result["state"]):
        out.append(f"\nNeeds you: {need}\n", style="bold yellow")
    if result.get("failure"):
        out.append(f"\n{result['failure']}\n", style="red")
    last = result["attempts"][-1] if result["attempts"] else None
    if last:
        verification = last.get("verification") or {}
        out.append(f"\nAttempt {last['n']} ({last['kind']}): {last['outcome'] or 'running'}\n",
                   style="bold")
        for check in verification.get("checks", []):
            ok = bool(check.get("passed"))
            out.append(("  PASS " if ok else "  FAIL "), style="green" if ok else "red")
            out.append(str(check.get("command", ""))[:200] + "\n")
        for violation in verification.get("scope_violations", []):
            out.append(f"  scope: {violation}\n", style="red")
        stat = (last.get("changes") or {}).get("stat")
        if stat:
            out.append("\n" + stat.rstrip() + "\n", style="dim")
    for review in result.get("reviews", [])[-2:]:
        out.append(f"\nReview {review.get('decision')}: {review.get('notes', '')}\n",
                   style="italic")
    return out


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Close")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.question)
            with Horizontal(classes="buttons"):
                yield Button("Yes", variant="warning", id="yes")
                yield Button("No", id="no")

    @on(Button.Pressed)
    def done(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class Ask(ModalScreen[dict[str, str] | None]):
    """A small form: an optional choice and an optional required text field."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, title: str, field: str | None, choices: list[str] | None = None):
        super().__init__()
        self.title_text, self.field, self.choices = title, field, choices

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text)
            if self.choices:
                yield Select([(c, c) for c in self.choices], value=self.choices[0],
                             allow_blank=False, id="choice")
            if self.field:
                yield Input(placeholder=self.field, id="text")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def _submit(self) -> None:
        text = self.query_one("#text", Input).value.strip() if self.field else ""
        if self.field and not text:
            self.notify(f"{self.field} is required", severity="error")
            return
        choice = str(self.query_one("#choice", Select).value) if self.choices else ""
        self.dismiss({"choice": choice, "text": text})

    @on(Input.Submitted)
    def submitted(self) -> None:
        self._submit()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)


class PatchView(ModalScreen[None]):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "Close")]

    def __init__(self, task_id: str, patch: str):
        super().__init__()
        self.task_id, self.patch = task_id, patch

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="patch"):
            yield Static(Syntax(self.patch or "(no changes)", "diff", word_wrap=True))
        yield Footer()


class BridgeApp(App[None]):
    TITLE = "agent-bridge"
    CSS = """
    #task-table { width: 3fr; }
    #detail { width: 2fr; border-left: solid $primary; padding: 0 1; }
    #agent-table, #account-table { height: auto; max-height: 50%; margin-bottom: 1; }
    #settings { padding: 0 1; }
    .dialog { width: 70; height: auto; padding: 1 2; border: thick $primary; background: $surface; }
    .buttons { height: auto; margin-top: 1; }
    .buttons Button { margin-right: 2; }
    Confirm, Ask, PatchView { align: center middle; }
    .patch { width: 95%; height: 90%; border: thick $primary; background: $surface; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("f", "toggle_filter", "Needs you / all"),
        Binding("a", "approve", "Approve"),
        Binding("v", "review", "Review"),
        Binding("c", "cancel_task", "Cancel"),
        Binding("m", "move", "Reassign"),
        Binding("p", "patch", "Patch"),
        Binding("l", "login", "Login account"),
        Binding("n", "add_account", "Add account"),
        Binding("d", "remove_account", "Remove account"),
        Binding("u", "use_account", "Shell as account"),
        Binding("x", "clear_account", "Clear account"),
        Binding("e", "edit_config", "Edit config"),
    ]

    def __init__(self, bridge: Bridge):
        super().__init__()
        self.bridge = bridge
        self.only_attention = False
        self.selected: str | None = None
        self.sub_title = str(bridge.root)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="tasks"):
            with TabPane("Tasks", id="tasks"), Horizontal():
                yield DataTable(id="task-table", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="detail"):
                    yield Static("Select a task", id="detail-text")
            with TabPane("Agents & accounts", id="agents"), Vertical():
                yield DataTable(id="agent-table", cursor_type="row")
                yield DataTable(id="account-table", cursor_type="row")
            with TabPane("Settings", id="settings-tab"), VerticalScroll(id="settings"):
                yield Static(id="settings-text")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#task-table", DataTable).add_columns(
            "Task", "State", "Agent", "Tries", "Objective")
        self.query_one("#agent-table", DataTable).add_columns(
            "Agent", "Adapter", "Account", "Model", "Roles", "Status")
        self.query_one("#account-table", DataTable).add_columns(
            "Account", "Provider", "Source", "Logged in", "State", "Quota", "Home")
        self.action_refresh()
        self.set_interval(3, self.refresh_tasks)

    # ---- data ------------------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_tasks()
        self.refresh_agents()
        self.refresh_settings()

    @work(exclusive=True, group="tasks")
    async def refresh_tasks(self) -> None:
        tasks = await self.bridge.list_tasks(limit=200)
        if self.only_attention:
            tasks = [t for t in tasks if t["state"] in ATTENTION]
        table = self.query_one("#task-table", DataTable)
        table.clear()
        for t in tasks:
            table.add_row(t["task_id"], Text(t["state"], style=STYLE.get(t["state"], "")),
                          t["agent"] or "-", f"{t['attempts']}/{t['max_attempts']}",
                          (t["objective"].splitlines() or [""])[0][:80], key=t["task_id"])
        if self.selected in {t["task_id"] for t in tasks}:
            table.move_cursor(row=table.get_row_index(self.selected))
        waiting = sum(t["state"] in ATTENTION for t in tasks)
        self.title = f"agent-bridge · {waiting} need you" if waiting else "agent-bridge"

    def refresh_agents(self) -> None:
        agents = self.query_one("#agent-table", DataTable)
        agents.clear()
        for a in self.bridge.list_agents()["agents"]:
            status = Text("available", style="green") if a["available"] else Text(
                a["unavailable_reason"] or "unavailable", style="red")
            agents.add_row(a["agent"], a["adapter"], a["account"] or "-", a["model"] or "-",
                           ", ".join(a["roles"]) or "-", status, key=a["agent"])
        self.refresh_accounts()

    @work(thread=True, exclusive=True, group="accounts")
    def refresh_accounts(self) -> None:
        # Login probes run each provider's CLI; keep them off the UI thread.
        rows = account_rows(self.bridge.list_accounts())
        self.call_from_thread(self._fill_accounts, rows)

    def _fill_accounts(self, rows: list[tuple[str, tuple[Any, ...]]]) -> None:
        table = self.query_one("#account-table", DataTable)
        table.clear()
        for key, cells in rows:
            table.add_row(*cells, key=key)

    def refresh_settings(self) -> None:
        b = self.bridge
        out = Text()
        out.append(f"Project  {b.root}\nSource   {b.orch.source}\n", style="bold")
        out.append(f"Stages   {' → '.join(b.orch.workflow.stages)}\n")
        for role in b.orch.roles:
            out.append(f"{role:<9}{', '.join(b.orch.role(role).agents) or '-'}\n")
        fallback = b.orch.routing.quota_fallback
        out.append(f"Quota fallback  {'on' if fallback.enabled else 'off'}\n\n")
        widget = self.query_one("#settings-text", Static)
        path = self._config_path()
        if path is None:
            out.append("No .ai/orchestrator.yaml or .ai/config.json: defaults apply.\n"
                       "Copy one of the kit's examples/*.yaml to .ai/orchestrator.yaml and "
                       "press e to edit it.", style="dim")
            widget.update(out)
            return
        out.append(f"{path.relative_to(b.root)}  (press e to edit)\n", style="bold")
        lexer = "yaml" if path.suffix in (".yaml", ".yml") else "json"
        widget.update(Group(out, Syntax(path.read_text(), lexer, line_numbers=True)))

    def _config_path(self) -> Path | None:
        for name in ("orchestrator.yaml", "config.json"):
            path = self.bridge.root / AI_DIR / name
            if path.exists():
                return path
        return None

    @on(DataTable.RowHighlighted, "#task-table")
    def task_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value:
            self.selected = event.row_key.value
            self.show_detail(self.selected)

    @work(exclusive=True, group="detail")
    async def show_detail(self, task_id: str) -> None:
        result = await self.bridge.task_result(task_id)
        tries = self.bridge.store.get(task_id)["attempts"]
        self.query_one("#detail-text", Static).update(detail_text(result, tries))

    # ---- task actions ----------------------------------------------------------------------

    def _selected_task(self, *states: str) -> dict[str, Any] | None:
        if not self.selected:
            self.notify("Select a task first", severity="warning")
            return None
        row = self.bridge.store.get(self.selected)
        if states and row["state"] not in states:
            self.notify(f"{self.selected} is {row['state']}; this needs {' or '.join(states)}",
                        severity="warning")
            return None
        return row

    @contextlib.contextmanager
    def _guard(self) -> Iterator[None]:
        """Show bridge refusals (wrong state, unknown agent, bad config) instead of exiting."""
        try:
            yield
        except (ValueError, KeyError, RuntimeError) as e:
            self.notify(str(e), severity="error", timeout=10)

    def _done(self, message: str) -> None:
        self.notify(message)
        self.refresh_tasks()
        if self.selected:
            self.show_detail(self.selected)

    @work
    async def action_approve(self) -> None:
        row = self._selected_task("APPROVED")
        if row and await self.push_screen_wait(Confirm(
                f"Approve and close {row['id']}?\n\n{row['spec']['objective'][:300]}")):
            with self._guard():
                self.bridge.human_approve(row["id"])
                self._done(f"{row['id']} approved")

    @work
    async def action_review(self) -> None:
        row = self._selected_task("REVIEW")
        if not row:
            return
        answer = await self.push_screen_wait(Ask(
            f"Review {row['id']} (press p first to read the patch)", "notes",
            ["approve", "rework", "block"]))
        if answer:
            with self._guard():
                await self.bridge.review_task(row["id"], answer["choice"], answer["text"])
                self._done(f"{row['id']}: {answer['choice']}")

    @work
    async def action_cancel_task(self) -> None:
        row = self._selected_task()
        if row:
            answer = await self.push_screen_wait(Ask(f"Cancel {row['id']}", "reason"))
            if answer:
                with self._guard():
                    await self.bridge.cancel_task(row["id"], answer["text"])
                    self._done(f"{row['id']} cancelled")

    @work
    async def action_move(self) -> None:
        row = self._selected_task()
        if not row:
            return
        names = [a["agent"] for a in self.bridge.list_agents()["agents"] if a["write"]]
        answer = await self.push_screen_wait(Ask(
            f"Move {row['id']} to another agent (starts a new session)", None, names))
        if answer:
            with self._guard():
                self.bridge.reassign_account(row["id"], answer["choice"])
                self._done(f"{row['id']} moved to {answer['choice']}")

    @work
    async def action_patch(self) -> None:
        row = self._selected_task()
        if row:
            attempts = (await self.bridge.task_result(row["id"]))["attempts"]
            patch = ((attempts[-1].get("changes") or {}).get("patch") if attempts else "") or ""
            self.push_screen(PatchView(row["id"], patch))

    # ---- account and settings actions ------------------------------------------------------

    def _row(self) -> str | None:
        table = self.query_one("#account-table", DataTable)
        if self.query_one(TabbedContent).active != "agents" or not table.row_count:
            self.notify("Open the Agents & accounts tab and select an account",
                        severity="warning")
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def _account(self) -> str | None:
        key = self._row()
        if key and ":" in key:
            self.notify("That row is not an account yet (press n to add one)",
                        severity="warning")
            return None
        return key

    def _human_cmd(self, *args: str) -> None:
        """Run a human-only CLI command in the real terminal (it prompts and logs in there)."""
        with self.suspend():
            subprocess.run([sys.executable, "-m", "bridge", *args], cwd=self.bridge.root,
                           check=False)
            input("\nPress Enter to return to agent-bridge...")
        with self._guard():
            self.bridge = Bridge(self.bridge.root)  # registry accounts join the config
        self.refresh_agents()

    def action_login(self) -> None:
        name = self._account()
        if name:
            self._human_cmd("account-login", name)

    @work
    async def action_add_account(self) -> None:
        key = self._row() or ""
        kind, _, name = key.partition(":")
        if kind == "codex-switch":
            listing = await asyncio.to_thread(self.bridge.list_accounts)
            home = next(h["codex_home"] for h in listing["codex_switch"]["homes"]
                        if h["name"] == name)
            if await self.push_screen_wait(Confirm(f"Add codex-switch home {name} as a "
                                                   f"machine-wide account?\n\n{home}")):
                self._human_cmd("account-add", name, "--provider", "openai", "--home", home)
            return
        providers = ["openai", "anthropic", "google"]
        if kind == "installed" and name in providers:
            providers.insert(0, providers.pop(providers.index(name)))
        answer = await self.push_screen_wait(Ask(
            "New machine-wide account with its own fresh login", "name", providers))
        if answer:
            self._human_cmd("account-add", answer["text"], "--provider", answer["choice"])

    def action_remove_account(self) -> None:
        name = self._account()
        if name:
            self._human_cmd("account-remove", name)

    def action_use_account(self) -> None:
        name = self._account()
        if not name:
            return
        if self.bridge.orch.accounts[name].provider == "google":
            # agy's account home is a whole HOME; a subshell inside it would lose the user's.
            self.notify(f'In your shell run: eval "$(agent-bridge account-use {name})"',
                        timeout=15)
            return
        with self.suspend():
            subprocess.run([sys.executable, "-m", "bridge", "account-use", name, "--shell"],
                           cwd=self.bridge.root, check=False)

    def action_clear_account(self) -> None:
        name = self._account()
        if name:
            with self._guard():
                self.bridge.clear_account(name)
                self.refresh_agents()
                self.notify(f"{name} marked available")

    def action_edit_config(self) -> None:
        path = self._config_path()
        if path is None:
            self.notify("No config yet: copy an example to .ai/orchestrator.yaml first",
                        severity="warning")
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        with self.suspend():
            subprocess.run([*editor.split(), str(path)], check=False)
        with self._guard():
            self.bridge = Bridge(self.bridge.root)
        self.action_refresh()

    def action_toggle_filter(self) -> None:
        self.only_attention = not self.only_attention
        self.refresh_tasks()


def run_tui() -> None:
    try:
        root = find_root()
    except RuntimeError as e:
        sys.exit(f"{e}\nRun agent-bridge inside a git project, or agent-bridge init <dir>.")
    if not (root / AI_DIR).exists():
        answer = input(f"agent-bridge is not set up in {root}. Install it here? [y/N] ")
        if answer.strip().lower() != "y":
            return
        from .cli import _server_entry
        for name in install.install(root, server=_server_entry()):
            print("updated", name)
    bridge = Bridge(root)
    bridge.recover()
    BridgeApp(bridge).run()
