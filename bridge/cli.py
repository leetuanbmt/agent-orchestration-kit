"""Command-line front for the same bridge service (`agent-bridge ...`).

Without a subcommand it opens the TUI for the project in the current directory. `approve` is
the human gate: it requires an interactive terminal and a typed confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import install, orchestrator
from .accounts import (account_env, codex_overrides, global_auth_fingerprint,
                       global_claude_identity, login_status)
from .config import find_root, load_config
from .executor import AGENT_ENV
from .service import Bridge, dumps, resolve_account


def _human_confirm(task_id: str) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("refused: human action needs an interactive terminal (human approval only)",
              file=sys.stderr)
        return False
    typed = input(f"Type the task id to confirm {task_id}: ").strip()
    return typed == task_id


def _server_entry() -> dict[str, Any]:
    """Call the installed tool by name when this process is it; otherwise go through uv."""
    on_path = shutil.which(install.NAME)
    if on_path and Path(on_path).resolve() == Path(sys.argv[0]).resolve():
        return install.tool_server()
    return install.uv_server()


async def _run(args: argparse.Namespace) -> Any:
    if args.cmd == "serve":
        from .server import _serve
        await _serve()
        return None
    if args.cmd == "init":
        changed = install.install(Path(args.dir), args.force, _server_entry())
        print("\n".join(f"updated {c}" for c in changed) or "already up to date")
        print("Open claude in the project once to trust the folder; run agent-bridge there "
              "for the TUI.")
        return None
    if args.cmd == "uninstall":
        install.uninstall(Path(args.dir), args.purge)
        return None
    if args.cmd == "config-check":
        root = find_root()
        orch = orchestrator.load(root, load_config(root))
        return {"ok": True, "source": orch.source, "stages": orch.workflow.stages,
                "accounts": sorted(orch.accounts), "agents": sorted(orch.agents),
                "roles": {r: orch.role(r).agents for r in orch.roles}}
    if args.cmd in {"account-list", "account-login"}:
        if args.cmd == "account-login" and not (sys.stdin.isatty() and sys.stdout.isatty()):
            print("refused: account-login needs an interactive terminal (human only)",
                  file=sys.stderr)
            raise SystemExit(2)
        root = find_root()
        cfg = load_config(root)
        if args.cmd == "account-list":
            return Bridge(root, cfg).list_accounts()
        acc = resolve_account(orchestrator.load(root, cfg), cfg, root, args.name)
        if acc.home is None:
            raise ValueError("account-login requires a configured named account")
        acc.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        if acc.provider == "openai":
            argv = [cfg.codex_bin, "login", *codex_overrides(acc),
                    *(["--device-auth"] if args.device_auth else [])]
            fingerprint = global_auth_fingerprint
        else:
            argv = [cfg.claude_bin, "auth", "login"]
            fingerprint = lambda: global_claude_identity(cfg)  # noqa: E731
        before = fingerprint()
        try:
            subprocess.run(argv, env=account_env(acc, os.environ), check=False)
        finally:
            if fingerprint() != before:
                print("ERROR: global auth was modified", file=sys.stderr)
                raise SystemExit(3)
        return login_status(cfg, acc)
    b = Bridge()
    if args.cmd not in {"approve", "reassign"}:
        b.recover()
    match args.cmd:
        case "account-clear":
            return b.clear_account(args.name)
        case "health":
            return await b.health()
        case "create":
            return await b.create_task(json.loads(Path(args.spec).read_text()))
        case "execute":
            return await b.execute_task(args.task_id, wait=True)
        case "resume":
            return await b.resume_task(args.task_id, args.feedback, wait=True)
        case "status":
            return await b.task_status(args.task_id, args.wait)
        case "result":
            return await b.task_result(args.task_id)
        case "list":
            return await b.list_tasks(args.state, args.limit)
        case "agents":
            return b.list_agents()
        case "plan":
            return await b.plan(args.objective, args.context, wait=True)
        case "run-workflow":
            return await b.run_workflow(args.workflow_id, wait=True)
        case "workflow-status":
            return await b.workflow_status(args.workflow_id)
        case "cancel":
            return await b.cancel_task(args.task_id, args.reason)
        case "review":
            return await b.review_task(args.task_id, args.decision, args.notes)
        case "reassign":
            if not _human_confirm(args.task_id):
                raise SystemExit(2)
            return b.reassign_account(args.task_id, args.agent)
        case "approve":
            if not _human_confirm(args.task_id):
                raise SystemExit(2)
            return b.human_approve(args.task_id)
    return None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="agent-bridge")
    sub = p.add_subparsers(dest="cmd")
    i = sub.add_parser("init", help="install the kit into a project (default: this directory)")
    i.add_argument("dir", nargs="?", default=".")
    i.add_argument("--force", action="store_true", help="overwrite conflicting kit files")
    u = sub.add_parser("uninstall", help="remove the kit from a project, keeping user edits")
    u.add_argument("dir", nargs="?", default=".")
    u.add_argument("--purge", action="store_true", help="also delete .ai/ (task history)")
    sub.add_parser("tui", help="interactive dashboard (the default without a subcommand)")
    sub.add_parser("serve", help="run the MCP stdio server")
    sub.add_parser("health")
    sub.add_parser("config-check", help="validate .ai/orchestrator.yaml (or the legacy config)")
    sub.add_parser("agents", help="list agents, roles and availability")
    sub.add_parser("account-list")
    sub.add_parser("account-clear").add_argument("name")
    login = sub.add_parser("account-login", help="HUMAN ONLY: log in an isolated account")
    login.add_argument("name")
    login.add_argument("--device-auth", action="store_true")
    c = sub.add_parser("create", help="create a task from a JSON spec file")
    c.add_argument("spec")
    for name in ("execute", "result"):
        sub.add_parser(name).add_argument("task_id")
    r = sub.add_parser("resume")
    r.add_argument("task_id")
    r.add_argument("--feedback", default="")
    s = sub.add_parser("status")
    s.add_argument("task_id")
    s.add_argument("--wait", type=float, default=0)
    ls = sub.add_parser("list")
    ls.add_argument("--state")
    ls.add_argument("--limit", type=int, default=50)
    x = sub.add_parser("cancel")
    x.add_argument("task_id")
    x.add_argument("--reason", required=True)
    v = sub.add_parser("review")
    v.add_argument("task_id")
    v.add_argument("--decision", choices=["approve", "rework", "block"], required=True)
    v.add_argument("--notes", required=True)
    pl = sub.add_parser("plan", help="run the planner agent on an objective")
    pl.add_argument("objective")
    pl.add_argument("--context", default="")
    for name in ("run-workflow", "workflow-status"):
        sub.add_parser(name).add_argument("workflow_id")
    sub.add_parser("approve", help="HUMAN ONLY: close an APPROVED task").add_argument("task_id")
    reass = sub.add_parser("reassign", help="HUMAN ONLY: move a task to another agent or account")
    reass.add_argument("task_id")
    reass.add_argument("agent")
    args = p.parse_args(argv)
    if os.environ.get(AGENT_ENV):
        print("refused: the bridge cannot run inside an agent it started", file=sys.stderr)
        raise SystemExit(2)
    if args.cmd in (None, "tui"):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            p.print_help()
            raise SystemExit(0 if args.cmd is None else 2)
        from .tui import run_tui
        run_tui()
        return
    try:
        out = asyncio.run(_run(args))
    except (ValueError, KeyError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    if out is not None:
        print(dumps(out))


if __name__ == "__main__":
    main()
