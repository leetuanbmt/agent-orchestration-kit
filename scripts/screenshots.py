"""Regenerate the README screenshots from an end-to-end run on dummy data.

Run it with the Python of an installed agent-bridge, so the installed build is what gets shown:

    "$(uv tool dir)/agent-orchestration-kit/bin/python" scripts/screenshots.py docs/images

Everything happens under /tmp/agent-bridge-demo: a fresh HOME, the kit's fake agent CLIs as
the only agents on PATH and an invented project, accounts and tasks. Nothing from the machine
running it (real logins, switcher profiles, user paths) can appear in the images.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
DEMO = Path("/tmp/agent-bridge-demo")
HOME, BIN, ROOT = DEMO / "home", DEMO / "bin", DEMO / "acme-api"
SIZE = (140, 36)

CONFIG = f"""version: 1
accounts:
  work:     {{provider: openai, home: {HOME}/.agent-bridge/homes/work}}
  personal: {{provider: openai, home: {HOME}/.agent-bridge/homes/personal}}
agents:
  codex-work:     {{adapter: codex-cli, account: work}}
  codex-personal: {{adapter: codex-cli, account: personal}}
  agy-studio:     {{adapter: agy-cli, account: studio, model: gemini-flash}}
  claude:         {{adapter: client}}
roles:
  planner: agy-studio
  executor: {{agents: [codex-work, codex-personal]}}
  reviewer: claude
workflow: {{stages: [plan, execute, test, review]}}
"""
CHECK = ("python3 -c \"import pathlib,sys; "
         "sys.exit(0 if '{text}' in pathlib.Path('{path}').read_text() else 1)\"")
# (objective, file the fake executor writes, extra spec fields, fake scenario, what follows)
TASKS = [
    ("Add rate limiting to the /login endpoint", "src/auth/limits.py", {"risk_level": "high"},
     "ok", "approve"),
    ("Paginate GET /orders (page, per_page)", "src/orders/api.py", {}, "ok", None),
    ("Fix the timezone in invoice due dates", "src/billing/dates.py", {}, "ok", "approve"),
    ("Cache product lookups for 60 seconds", "src/catalog/cache.py", {}, "quota", None),
    ("Rename the legacy CSV exporter", "src/export/csv.py", {}, "ok", "cancel"),
]


AGENT_BRIDGE = str(Path(sys.executable).parent / "agent-bridge")  # the installed build's CLI


def sh(*argv: str, **env: str) -> str:
    if argv[0] == "agent-bridge":
        argv = (AGENT_BRIDGE, *argv[1:])
    out = subprocess.run(argv, cwd=ROOT, env={**os.environ, **env}, capture_output=True,
                         text=True, check=False)
    if out.returncode != 0:
        sys.exit(f"{' '.join(argv)} failed ({out.returncode}):\n{out.stderr}{out.stdout}")
    return out.stdout


def setup() -> None:
    shutil.rmtree(DEMO, ignore_errors=True)
    BIN.mkdir(parents=True)
    for name in ("codex", "claude", "agy", "opencode"):  # fakes copied: no kit path in images
        shutil.copy2(KIT / "tests" / "fakes" / name, BIN / name)
    os.environ.update(HOME=str(HOME), PATH=f"{BIN}:/usr/bin:/bin", FAKE_CODEX_DIR=str(DEMO / "fake"),
                      FAKE_AGY_DIR=str(DEMO / "fake-agy"), FAKE_OPENCODE_DIR=str(DEMO / "fake-oc"),
                      COLUMNS=str(SIZE[0]))
    for var in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "BRIDGE_PROJECT_ROOT"):
        os.environ.pop(var, None)
    homes = HOME / ".agent-bridge" / "homes"
    for name, used in (("work", (38, 12)), ("personal", (91, 64))):
        (homes / name / "sessions").mkdir(parents=True)
        (homes / name / "auth.json").write_text("{}")  # the fake codex only checks it exists
        limits = {"primary": {"used_percent": used[0], "window_minutes": 300,
                              "resets_at": time.time() + 3600},
                  "secondary": {"used_percent": used[1], "window_minutes": 10080,
                                "resets_at": time.time() + 86400}}
        (homes / name / "sessions" / "rollout-demo.jsonl").write_text(json.dumps(
            {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": limits}}))
    (homes / "studio").mkdir()
    (HOME / ".agent-bridge" / "accounts.json").write_text(json.dumps(
        {"studio": {"provider": "google", "home": str(homes / "studio")}}))
    for part in ("auth", "orders", "billing", "catalog", "export"):
        (ROOT / "src" / part).mkdir(parents=True)
        (ROOT / "src" / part / "__init__.py").write_text("")
    subprocess.run(["git", "init", "-q", str(ROOT)], check=True)
    sh("agent-bridge", "init", ".")
    (ROOT / ".ai" / "orchestrator.yaml").write_text(CONFIG)
    subprocess.run(["git", "-C", str(ROOT), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ROOT), "-c", "user.name=demo", "-c",
                    "user.email=demo@example.com", "commit", "-qm", "init"], check=True)


def run_tasks() -> None:
    for objective, path, extra, scenario, then in TASKS:
        spec = {"objective": objective, "allowed_paths": ["src/**"],
                "acceptance_criteria": [f"{path} defines the change"],
                "verification_commands": [CHECK.format(text="def ", path=path)], **extra}
        spec_file = DEMO / "spec.json"
        spec_file.write_text(json.dumps(spec))
        task = json.loads(sh("agent-bridge", "create", str(spec_file)))["task_id"]
        if then == "cancel":
            sh("agent-bridge", "cancel", task, "--reason", "superseded by the new exporter")
            continue
        sh("agent-bridge", "execute", task, FAKE_CODEX_SCENARIO=scenario,
           FAKE_CODEX_WRITE=f"{path}=def handler():\n    return 'ok'\n")
        if then == "approve":
            sh("agent-bridge", "review", task, "--decision", "approve", "--notes",
               "criteria met, checks pass")


async def shoot_dashboard(out: Path) -> None:
    from bridge.service import Bridge
    from bridge.tui import BridgeApp

    app = BridgeApp(Bridge(ROOT))
    async with app.run_test(size=SIZE) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause(0.5)
        needs = app.query_one("#needs")
        needs.focus()
        needs.move_cursor(row=1)  # the task waiting in REVIEW
        await app.workers.wait_for_complete()
        await pilot.pause(2)  # the quota bar animates to its value
        app.save_screenshot(str(out / "dashboard.svg"))
        await pilot.press("enter")
        await pilot.pause(0.5)
        app.save_screenshot(str(out / "dashboard-actions.svg"))
        await pilot.press("escape")
        accounts = app.query_one("#accounts")
        accounts.focus()
        accounts.move_cursor(row=accounts.get_row_index("work"))  # the account out of quota
        await pilot.pause(0.5)
        app.save_screenshot(str(out / "dashboard-account.svg"))


def shoot_cli(out: Path) -> None:
    from rich.console import Console

    from bridge import render
    for name, argv, view in (("cli-list", ["list"], render.tasks_view),
                             ("cli-accounts", ["account-list"], render.accounts_view)):
        console = Console(record=True, width=SIZE[0], file=open(os.devnull, "w"))
        console.print(f"$ agent-bridge {' '.join(argv)}", style="bold")
        # The installed CLI prints JSON through a pipe; the terminal view of that same JSON.
        console.print(view(json.loads(sh("agent-bridge", *argv, "--json"))))
        console.save_svg(str(out / f"{name}.svg"), title=f"agent-bridge {' '.join(argv)}")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/images").resolve()
    out.mkdir(parents=True, exist_ok=True)
    setup()
    run_tasks()
    asyncio.run(shoot_dashboard(out))
    shoot_cli(out)
    print("\n".join(sorted(p.name for p in out.glob("*.svg"))))


if __name__ == "__main__":
    main()
