"""Read account quota snapshots from Codex session rollouts."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

from .accounts import Account


def codex_home_for(account: Account) -> Path:
    if account.codex_home is not None:
        return account.codex_home
    return Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex"


def _numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def remaining_pct(rate_limits: dict, now: float) -> float | None:
    used = []
    for name in ("primary", "secondary"):
        window = rate_limits.get(name)
        if not isinstance(window, dict) or not _numeric(window.get("used_percent")):
            continue
        reset = window.get("resets_at")
        used.append(0.0 if _numeric(reset) and reset <= now else window["used_percent"])
    return max(0.0, 100.0 - max(used)) if used else None


def read_quota(home: Path, now: float | None = None) -> dict | None:
    sessions = home / "sessions"
    if sessions.is_symlink():
        return None
    candidates = []
    for directory, _, names in os.walk(sessions, followlinks=False):
        for name in names:
            if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                continue
            path = Path(directory) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                candidates.append((info.st_mtime, path))
    now = time.time() if now is None else now
    for mtime, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:20]:
        latest = None
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict) or event.get("type") != "event_msg":
                        continue
                    payload = event.get("payload")
                    if (isinstance(payload, dict) and payload.get("type") == "token_count"
                            and isinstance(payload.get("rate_limits"), dict)):
                        latest = payload["rate_limits"]
        except OSError:
            continue
        if latest is not None:
            result = {"remaining_pct": remaining_pct(latest, now), "observed_at": mtime}
            for name in ("primary", "secondary"):
                window = latest.get(name)
                result[name] = ({key: window.get(key) for key in
                                 ("used_percent", "window_minutes", "resets_at")}
                                if isinstance(window, dict) else None)
            plan = latest.get("plan_type")
            result["plan_type"] = plan if isinstance(plan, str) else None
            return result
    return None
