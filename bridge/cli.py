"""Command-line front for the same bridge service (`uv run codex-bridge ...`).

`approve` is the human gate: it requires an interactive terminal and a typed confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .accounts import (account_env, codex_overrides, global_auth_fingerprint,
                       login_status, resolve)
from .config import find_root, load_config
from .service import Bridge, dumps


def _human_confirm(task_id: str) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("refused: human action needs an interactive terminal (human approval only)",
              file=sys.stderr)
        return False
    typed = input(f"Type the task id to confirm {task_id}: ").strip()
    return typed == task_id


async def _run(args: argparse.Namespace) -> Any:
    if args.cmd == "serve":
        from .server import _serve
        await _serve()
        return None
    if args.cmd in {"account-list", "account-login"}:
        if args.cmd == "account-login" and not (sys.stdin.isatty() and sys.stdout.isatty()):
            print("refused: account-login needs an interactive terminal (human only)",
                  file=sys.stderr)
            raise SystemExit(2)
        root = find_root()
        cfg = load_config(root)
        if args.cmd == "account-list":
            return Bridge(root, cfg).list_accounts()
        acc = resolve(cfg, root, args.name)
        if acc.codex_home is None:
            raise ValueError("account-login requires a configured named account")
        acc.codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        before = global_auth_fingerprint()
        try:
            subprocess.run(
                [cfg.codex_bin, "login", *codex_overrides(acc),
                 *(["--device-auth"] if args.device_auth else [])],
                env=account_env(acc, os.environ), check=False,
            )
        finally:
            if global_auth_fingerprint() != before:
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
        case "cancel":
            return await b.cancel_task(args.task_id, args.reason)
        case "review":
            return await b.review_task(args.task_id, args.decision, args.notes)
        case "reassign":
            if not _human_confirm(args.task_id):
                raise SystemExit(2)
            return b.reassign_account(args.task_id, args.account)
        case "approve":
            if not _human_confirm(args.task_id):
                raise SystemExit(2)
            return b.human_approve(args.task_id)
    return None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="codex-bridge")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the MCP stdio server")
    sub.add_parser("health")
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
    sub.add_parser("approve", help="HUMAN ONLY: close an APPROVED task").add_argument("task_id")
    reass = sub.add_parser("reassign", help="HUMAN ONLY: change task account")
    reass.add_argument("task_id")
    reass.add_argument("account")
    args = p.parse_args(argv)
    try:
        out = asyncio.run(_run(args))
    except (ValueError, KeyError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    if out is not None:
        print(dumps(out))


if __name__ == "__main__":
    main()
