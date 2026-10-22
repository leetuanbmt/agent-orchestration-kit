"""Terminal dashboard (`agent-bridge` without a subcommand) for the human side of the bridge.

One screen of panels: what needs you, every task, the selected task's detail, agents,
accounts and the executor's quota. Enter on a row opens the actions that fit it. Agents are
never started from here: a run belongs to the process that started it, and closing the
dashboard would interrupt it.
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
from textual.widgets import (Button, DataTable, Footer, Input, Label, OptionList, ProgressBar,
                             Select, Static)
from textual.widgets.option_list import Option

from . import install
from .config import AI_DIR, find_root
from .render import ATTENTION, STYLE, account_rows, quota_text, remaining_pct
from .service import Bridge

TERMINAL = {"DONE", "CANCELLED", "BLOCKED"}
CURSOR = "❯"


def account_detail(listing: dict[str, Any], agents: list[dict[str, Any]], key: str) -> Text:
    """The Detail panel for the account row `key` (an `account_rows` key)."""
    cells = dict(account_rows(listing)).get(key)
    out = Text()
    if cells is None:
        return out
    for label, value in zip(("Account", "Provider", "Source", "Logged in", "State", "Quota",
                             "Home"), cells, strict=True):
        out.append(f"{label:<12}", style="dim")
        out.append_text(value if isinstance(value, Text) else Text(str(value)))
        out.append("\n")
    acc = next((a for a in listing["accounts"] if a["name"] == key), None)
    if acc is None:  # not usable here; the row says why
        return out
    out.append(f"{'Slots':<12}", style="dim")
    out.append(f"{acc.get('max_concurrency', 1)}\n")
    users = [f"{a['agent']} ({', '.join(a['roles']) or 'no role'})" for a in agents
             if a["account"] == key]
    out.append(f"{'Agents':<12}", style="dim")
    out.append(", ".join(users) or "-")
    out.append("\n")
    if acc.get("last_error"):
        out.append(f"\nLast error: {acc['last_error']}\n", style="red")
    if not acc.get("logged_in"):
        out.append(f"\nNeeds you: log in  (Enter for actions, or agent-bridge "
                   f"account-login {key})\n", style="bold yellow")
    return out


def detail_text(result: dict[str, Any], tries: int) -> Text:
    """`result` is `task_result`, whose `attempts` is the attempt list; `tries` is the budget used."""
    out = Text()
    out.append(f"agent {result['agent'] or '-'} · account {result['account'] or '-'}"
               f" · tries {tries}/{result['max_attempts']}"
               f" · risk {result['spec'].get('risk_level', 'normal')}\n\n", style="dim")
    out.append(result["spec"]["objective"] + "\n")
    if need := ATTENTION.get(result["state"]):
        out.append(f"\nNeeds you: {need}  (Enter for actions)\n", style="bold yellow")
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


def task_actions(row: dict[str, Any]) -> list[tuple[str, str]]:
    """(action, label) a human can take on a task in this state, most likely first."""
    state = row["state"]
    actions = []
    if state == "APPROVED" and row["needs_human"]:
        actions.append(("approve", "Approve and close"))
    if state == "REVIEW":
        actions.append(("review", "Review: approve / rework / block"))
    actions.append(("patch", "Read the patch"))
    if state not in TERMINAL:
        actions += [("move", "Move to another agent"), ("cancel_task", "Cancel")]
    return actions


def account_actions(key: str) -> list[tuple[str, str]]:
    if ":" in key:  # a switcher home, a copied-token profile or a CLI without an account
        return [("add_account", "Add an account")]
    return [("login", "Log in"), ("use_account", "Open a shell as this account"),
            ("clear_account", "Mark available again"), ("remove_account", "Remove"),
            ("add_account", "Add another account")]


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


class ActionMenu(ModalScreen[str | None]):
    """Enter on a row: only the actions that fit it, so no key has to be remembered."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, title: str, actions: list[tuple[str, str]]):
        super().__init__()
        self.title_text, self.actions = title, actions

    def compose(self) -> ComposeResult:
        menu = OptionList(*[Option(label, id=action) for action, label in self.actions],
                          id="menu", classes="dialog")
        menu.border_title = self.title_text
        yield menu

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class PatchView(ModalScreen[None]):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "Close")]

    def __init__(self, task_id: str, patch: str):
        super().__init__()
        self.task_id, self.patch = task_id, patch

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="patch") as box:
            box.border_title = f"{self.task_id} · patch"
            yield Static(Syntax(self.patch or "(no changes)", "diff", word_wrap=True))
        yield Footer()


class SettingsView(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,s", "app.pop_screen", "Close"),
                Binding("e", "app.edit_config", "Edit config")]

    def __init__(self, body: Any):
        super().__init__()
        self.body = body

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="patch") as box:
            box.border_title = "Settings"
            yield Static(self.body)
        yield Footer()


class BridgeApp(App[None]):
    TITLE = "agent-bridge"
    CSS = """
    $hi: #e8916a;
    Screen { background: $background; }
    ModalScreen { background: $background 60%; }
    OptionList > .option-list--option-highlighted { background: $hi 40%; color: $text; }
    .panel { border: round $panel-lighten-2; border-title-color: $hi;
             border-title-style: bold; padding: 0 1; background: $background; }
    .panel:focus, .panel:focus-within { border: round $hi; }
    #left { width: 2fr; }
    #needs { height: auto; max-height: 40%; }
    #tasks { height: 1fr; }
    #detail { width: 3fr; }
    #side { width: 2fr; }
    #search { border: round $panel-lighten-2; margin-bottom: 0; }
    #search:focus { border: round $hi; }
    #agents { height: auto; max-height: 40%; }
    #accounts { height: 1fr; }
    #quota-row { height: 3; border: round $panel-lighten-2; padding: 0 1; }
    #quota { width: 1fr; }
    #quota Bar { width: 1fr; }
    #quota Bar > .bar--bar, #quota Bar > .bar--complete { color: $hi; }
    #quota-label { width: auto; padding-left: 2; }
    DataTable { background: $background; overflow-x: hidden; }
    DataTable > .datatable--cursor { background: $hi 30%; color: $text; text-style: bold; }
    .dialog { width: 70; height: auto; max-height: 80%; padding: 1 2; border: round $hi;
              border-title-color: $hi; background: $surface; }
    .buttons { height: auto; margin-top: 1; }
    .buttons Button { margin-right: 2; }
    Confirm, Ask, ActionMenu, PatchView, SettingsView { align: center middle; }
    .patch { width: 95%; height: 90%; border: round $hi; border-title-color: $hi;
             background: $surface; padding: 0 1; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Keys"),
        Binding("slash", "search", "Search"),
        Binding("s", "settings", "Settings"),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("a", "approve", "Approve"),
        Binding("v", "review", "Review"),
        Binding("p", "patch", "Patch"),
        Binding("m", "move", "Move"),
        Binding("c", "cancel_task", "Cancel"),
        Binding("l", "login", "Login"),
        Binding("u", "use_account", "Shell"),
        Binding("x", "clear_account", "Clear"),
        Binding("n", "add_account", "Add account"),
        Binding("d", "remove_account", "Remove"),
        Binding("e", "edit_config", "Edit config", show=False),
    ]
    TASK_ACTIONS = {"approve", "review", "patch", "move", "cancel_task"}
    ACCOUNT_ACTIONS = {"login", "use_account", "clear_account", "remove_account"}

    def __init__(self, bridge: Bridge):
        super().__init__()
        self.bridge = bridge
        self.selected: str | None = None
        self.query_text = ""
        # Latest listings, kept for the account Detail view without re-probing logins.
        self.accounts_listing: dict[str, Any] = {"accounts": [], "codex_switch": {
            "homes": [], "profiles": []}}
        self.agents_listing: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="side"):
                yield Input(placeholder="/ search tasks", id="search")
                yield self._panel(DataTable(id="agents", cursor_type="row", show_header=False),
                                  "Agents")
                yield self._panel(DataTable(id="accounts", cursor_type="row",
                                            show_header=False), "Accounts")
            with Vertical(id="left"):
                yield self._panel(DataTable(id="needs", cursor_type="row", show_header=False),
                                  "Needs you")
                yield self._panel(DataTable(id="tasks", cursor_type="row", show_header=False),
                                  "Tasks")
            with VerticalScroll(id="detail", classes="panel") as detail:
                detail.border_title = "Detail"
                yield Static("Select a task or an account", id="detail-text")
        # Not "bar": ProgressBar's own inner Bar widget already uses that id.
        with Horizontal(id="quota-row"):
            yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="quota")
            yield Static("quota UNKNOWN", id="quota-label")
        yield Footer()

    @staticmethod
    def _panel(widget: DataTable, title: str) -> DataTable:
        widget.add_class("panel")
        widget.border_title = title
        widget.show_cursor = False
        return widget

    def on_mount(self) -> None:
        for name in ("needs", "tasks"):
            table = self.query_one(f"#{name}", DataTable)
            table.add_column("", key="cur", width=1)
            table.add_columns("Task", "State", "Objective")
        self.query_one("#agents", DataTable).add_columns("", "Agent", "Roles")
        self.query_one("#accounts", DataTable).add_columns("", "Account", "Provider", "State")
        self.action_refresh()
        self.set_interval(3, self.refresh_tasks)

    # ---- data ------------------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_tasks()
        self.refresh_agents()

    def _matches(self, t: dict[str, Any]) -> bool:
        q = self.query_text.lower()
        return not q or any(q in str(t[k] or "").lower()
                            for k in ("task_id", "state", "agent", "objective"))

    @work(exclusive=True, group="tasks")
    async def refresh_tasks(self) -> None:
        tasks = [t for t in await self.bridge.list_tasks(limit=200) if self._matches(t)]
        needs = [t for t in tasks if t["state"] in ATTENTION]
        self._fill_tasks("needs", needs)
        self._fill_tasks("tasks", tasks)
        self.query_one("#needs").border_title = f"Needs you ({len(needs)})"
        self.query_one("#tasks").border_title = f"Tasks ({len(tasks)})"
        self.title = f"agent-bridge · {len(needs)} need you" if needs else "agent-bridge"
        if self.focused is None:
            self.query_one("#needs" if needs else "#tasks").focus()

    def _fill_tasks(self, name: str, tasks: list[dict[str, Any]]) -> None:
        table = self.query_one(f"#{name}", DataTable)
        table.clear()
        for t in tasks:
            state = Text(t["state"], style=STYLE.get(t["state"], ""))
            table.add_row(CURSOR if t["task_id"] == self.selected else "", t["task_id"], state,
                          (t["objective"].splitlines() or [""])[0][:80], key=t["task_id"])
        if self.selected in {t["task_id"] for t in tasks}:
            table.move_cursor(row=table.get_row_index(self.selected))

    def refresh_agents(self) -> None:
        self.refresh_accounts()

    @work(thread=True, exclusive=True, group="accounts")
    def refresh_accounts(self) -> None:
        # Login probes run each provider's CLI; keep them off the UI thread.
        listing = self.bridge.list_accounts()
        logins = {a["name"]: a.get("logged_in") for a in listing["accounts"]}
        self.call_from_thread(self._fill_agents, self.bridge.list_agents(logins))
        self.call_from_thread(self._fill_accounts, listing)

    def _fill_agents(self, listing: dict[str, Any]) -> None:
        self.agents_listing = listing["agents"]
        agents = self.query_one("#agents", DataTable)
        agents.clear()
        for a in listing["agents"]:
            dot = Text("●", style="green" if a["available"] else "red")
            agents.add_row(dot, a["agent"], ", ".join(a["roles"]) or "-", key=a["agent"])

    def _fill_accounts(self, listing: dict[str, Any]) -> None:
        self.accounts_listing = listing
        table = self.query_one("#accounts", DataTable)
        table.clear()
        for key, cells in account_rows(listing):
            name, provider, _, logged_in, state = cells[:5]
            usable = ":" not in key and logged_in == "yes" and state == "available"
            dot = Text("●" if ":" not in key else "○", style="green" if usable else "red"
                       if ":" not in key else "dim")
            table.add_row(dot, name, provider, state if ":" in key else
                          ("logged out" if logged_in != "yes" else state), key=key)
        executor = self.bridge.orch.role("executor").agents
        account = self.bridge.orch.agents[executor[0]].account if executor else None
        quota = next((a.get("quota") for a in listing["accounts"] if a["name"] == account), None)
        pct = remaining_pct(quota)
        self.query_one("#quota", ProgressBar).update(progress=pct or 0)
        self.query_one("#quota-label", Static).update(
            f"executor {account or '-'} · {quota_text(quota) if pct is not None else 'quota UNKNOWN'}")

    @on(DataTable.RowHighlighted, "#needs, #tasks")
    def task_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Refreshes re-highlight rows in both lists; only the list the user is in decides.
        if not event.data_table.has_focus or not event.row_key.value:
            return
        self.selected = event.row_key.value
        for table in self.query("#needs, #tasks").results(DataTable):
            for key in table.rows:
                table.update_cell(key, "cur", CURSOR if key.value == self.selected else "")
        self.show_detail(self.selected)
        self.refresh_bindings()

    @on(DataTable.RowHighlighted, "#accounts")
    def account_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.refresh_bindings()
        if event.data_table.has_focus and event.row_key.value:
            self.show_account(event.row_key.value)

    def show_account(self, key: str) -> None:
        self.query_one("#detail").border_title = key.partition(":")[2] or key
        self.query_one("#detail-text", Static).update(
            account_detail(self.accounts_listing, self.agents_listing, key))

    def on_descendant_focus(self) -> None:
        # Only the list you are in shows its cursor; the others keep a plain look.
        for table in self.screen.query(DataTable):
            table.show_cursor = table.has_focus
        self.refresh_bindings()

    @work(exclusive=True, group="detail")
    async def show_detail(self, task_id: str) -> None:
        result = await self.bridge.task_result(task_id)
        tries = self.bridge.store.get(task_id)["attempts"]
        detail = self.query_one("#detail")
        detail.border_title = f"{task_id} · {result['state']}"
        self.query_one("#detail-text", Static).update(detail_text(result, tries))

    # ---- keys that fit the focused row -----------------------------------------------------

    def _focus_id(self) -> str | None:
        return self.focused.id if self.focused else None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in self.TASK_ACTIONS:
            if self._focus_id() not in ("needs", "tasks") or not self.selected:
                return False
            return action in {a for a, _ in task_actions(self.bridge.store.get(self.selected))}
        if action in self.ACCOUNT_ACTIONS:
            key = self._account_key()
            return self._focus_id() == "accounts" and key is not None and ":" not in key
        if action == "add_account":
            return self._focus_id() == "accounts"
        return True

    @on(DataTable.RowSelected, "#needs, #tasks, #accounts")
    async def row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        if not key:
            return
        if event.data_table.id == "accounts":
            title, actions = key.partition(":")[-1] or key, account_actions(key)
        else:
            title, actions = key, task_actions(self.bridge.store.get(key))
        self._open_menu(title, actions)

    @work
    async def _open_menu(self, title: str, actions: list[tuple[str, str]]) -> None:
        action = await self.push_screen_wait(ActionMenu(title, actions))
        if action:
            await self.run_action(action)

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.query_text = event.value.strip()
        self.refresh_tasks()

    @on(Input.Submitted, "#search")
    def search_done(self) -> None:
        self.query_one("#tasks").focus()

    def action_help(self) -> None:
        if self.screen.query("HelpPanel"):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()

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
        names = [a["agent"] for a in self.bridge.list_agents(logins={})["agents"] if a["write"]]
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

    def _account_key(self) -> str | None:
        table = self.query_one("#accounts", DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def _account(self) -> str | None:
        key = self._account_key()
        return key if key and ":" not in key else None

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
        if name := self._account():
            self._human_cmd("account-login", name)

    @work
    async def action_add_account(self) -> None:
        kind, _, name = (self._account_key() or "").partition(":")
        if kind == "codex-switch":
            listing = await asyncio.to_thread(self.bridge.list_accounts)
            home = next(h["codex_home"] for h in listing["codex_switch"]["homes"]
                        if h["name"] == name)
            if await self.push_screen_wait(Confirm(f"Add codex-switch home {name} as a "
                                                   f"machine-wide account?\n\n{home}")):
                self._human_cmd("account-add", name, "--provider", "openai", "--home", home)
            return
        providers = ["openai", "anthropic", "google", "opencode"]
        if kind == "installed" and name in providers:
            providers.insert(0, providers.pop(providers.index(name)))
        answer = await self.push_screen_wait(Ask(
            "New machine-wide account with its own fresh login", "name", providers))
        if answer:
            self._human_cmd("account-add", answer["text"], "--provider", answer["choice"])

    def action_remove_account(self) -> None:
        if name := self._account():
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
        if name := self._account():
            with self._guard():
                self.bridge.clear_account(name)
                self.refresh_agents()
                self.notify(f"{name} marked available")

    def _settings_body(self) -> Any:
        b = self.bridge
        out = Text()
        out.append(f"Project  {b.root}\nSource   {b.orch.source}\n", style="bold")
        out.append(f"Stages   {' → '.join(b.orch.workflow.stages)}\n")
        for role in b.orch.roles:
            out.append(f"{role:<9}{', '.join(b.orch.role(role).agents) or '-'}\n")
        fallback = b.orch.routing.quota_fallback
        out.append(f"Quota fallback  {'on' if fallback.enabled else 'off'}\n\n")
        path = self._config_path()
        if path is None:
            out.append("No .ai/orchestrator.yaml or .ai/config.json: defaults apply.\n"
                       "Copy one of the kit's examples/*.yaml to .ai/orchestrator.yaml and "
                       "press e to edit it.", style="dim")
            return out
        out.append(f"{path.relative_to(b.root)}  (press e to edit)\n", style="bold")
        lexer = "yaml" if path.suffix in (".yaml", ".yml") else "json"
        return Group(out, Syntax(path.read_text(), lexer, line_numbers=True))

    def action_settings(self) -> None:
        self.push_screen(SettingsView(self._settings_body()))

    def _config_path(self) -> Path | None:
        for name in ("orchestrator.yaml", "config.json"):
            path = self.bridge.root / AI_DIR / name
            if path.exists():
                return path
        return None

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
        if isinstance(self.screen, SettingsView):
            self.pop_screen()
        self.action_refresh()


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
