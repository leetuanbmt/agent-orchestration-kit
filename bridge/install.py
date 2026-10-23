"""Install the kit into a project (`agent-bridge init`) and remove it (`agent-bridge uninstall`).

Stdlib only: `scripts/setup.sh` runs it with the system python3, before any venv exists.
Every change is recorded in an install manifest inside the git directory so uninstall can
restore the original bytes and keep anything the user edited afterwards.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
# A checkout (editable install) keeps the kit files at its root; a wheel carries them in
# bridge/kit/ with the same layout (pyproject force-include) and has no checkout to run from.
CHECKOUT = (KIT / ".claude/rules/orchestration.md").is_file()
if not CHECKOUT:
    KIT = Path(__file__).resolve().parent / "kit"
NAME = "agent-bridge"
OLD_NAME = "codex-bridge"
START, END = b"<!-- agent-bridge:start -->", b"<!-- agent-bridge:end -->"
OLD_START, OLD_END = b"<!-- codex-bridge:start -->", b"<!-- codex-bridge:end -->"
BLOCK = START + (
    b"\nRoles map to agents in .ai/orchestrator.yaml (bridge_list_agents); without it Claude"
    b" plans and reviews, Codex executes.\nDelegate implementation to the executor role first."
    b" Approvals, reassignment, and account actions are human-only.\nFollow"
    b" .claude/rules/{orchestration,delegation,security}.md and the"
    b" .claude/skills/agent-orchestration/SKILL.md skill.\n") + END
# Files earlier kit versions installed under codex-* names: removed while they are unedited.
RETIRED = [*(f".claude/rules/codex-{n}.md" for n in ("orchestration", "delegation", "review",
                                                     "security")),
           ".claude/skills/codex-delegation/SKILL.md"]


def git_dir(root: Path) -> Path:
    return Path(subprocess.run(["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
                               check=True, capture_output=True, text=True).stdout.strip())


def bridge_dir(gdir: Path) -> Path:
    """`<git dir>/agent-bridge`, taking over the directory an older kit named codex-bridge."""
    new, old = gdir / NAME, gdir / OLD_NAME
    if not new.exists() and old.is_dir() and not old.is_symlink():
        os.replace(old, new)
    return new


def uv_server(kit: Path = KIT) -> dict:
    return {"command": "uv", "args": ["run", "--project", str(kit), NAME, "serve"],
            "env": {"BRIDGE_PROJECT_ROOT": "${CLAUDE_PROJECT_DIR:-.}"}}


def tool_server() -> dict:
    """The server entry for an `uv tool install`ed agent-bridge: no path to the kit checkout."""
    return {"command": NAME, "args": ["serve"],
            "env": {"BRIDGE_PROJECT_ROOT": "${CLAUDE_PROJECT_DIR:-.}"}}


class _Target:
    def __init__(self, target: Path):
        self.root = target

    def path(self, name: str) -> Path:
        """A directory symlink is followed while it stays in the work tree (a shared
        `.claude/skills -> ../.agents/skills`); the file itself must not be a link."""
        p = self.root / name
        real = p.resolve()
        if (not real.is_relative_to(self.root) or real.is_relative_to(self.root / ".git")
                or p.is_symlink()):
            sys.exit("Refusing symlink or external path: " + name)
        return p

    def read(self, name: str) -> bytes | None:
        p = self.path(name)
        return p.read_bytes() if p.exists() else None


def _encode(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode()


def _decode(value: str | None) -> bytes | None:
    return None if value is None else base64.b64decode(value)


def _dump(obj: object) -> bytes:
    return (json.dumps(obj, indent=2) + "\n").encode()


def _manifest_path(target: Path) -> Path:
    mdir = bridge_dir(git_dir(target))
    path = mdir / "install-manifest.json"
    if mdir.is_symlink() or path.is_symlink():
        sys.exit("Refusing symlinked install manifest")
    return path


def install(target: Path, force: bool = False, server: dict | None = None,
            kit: Path = KIT) -> list[str]:
    """Install rules, skill, settings, MCP server and CLAUDE.md block; return the changed paths."""
    target = target.resolve()
    if target == kit or subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
            capture_output=True).stdout.strip() != b"true":
        sys.exit("Target must be a git work tree other than the kit")
    if not (kit / ".claude/rules/orchestration.md").is_file():
        sys.exit(f"Kit files not found under {kit}; install the tool with: "
                 "uv tool install --editable <kit checkout>")
    t = _Target(target)
    server = server or uv_server(kit)
    manifest_path = _manifest_path(target)
    old_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    manifest = json.loads(old_manifest) if old_manifest else {"files": {}, "json": {}}
    changes: dict[str, bytes | None] = {}

    def plan(name: str, data: bytes | None) -> None:
        if t.read(name) != data:
            changes[name] = data

    for name in [".mcp.json", ".claude/settings.json"]:
        raw = t.read(name)
        try:
            obj = json.loads(raw) if raw is not None else {}
        except (ValueError, UnicodeError):
            sys.exit("Invalid JSON: " + name)
        record = manifest["json"].setdefault(name, {"created": raw is None,
                                                    "original": _encode(raw)})
        before = json.loads(json.dumps(obj))
        if name == ".mcp.json":
            servers = obj.setdefault("mcpServers", {})
            # An older install registered the server as codex-bridge: hand that entry back.
            if OLD_NAME in servers and servers[OLD_NAME] == record.get("server_installed"):
                previous = record.pop("server_previous")
                if previous["present"]:
                    servers[OLD_NAME] = previous["value"]
                else:
                    del servers[OLD_NAME]
            existing = servers.get(NAME)
            if NAME in servers and existing != server and not force:
                sys.exit(f"Conflicting {NAME} server; use --force")
            if existing != server:
                record.setdefault("server_previous", {"present": NAME in servers,
                                                      "value": existing})
                record["server_installed"] = server
                record.setdefault("container_created", "mcpServers" not in before)
                servers[NAME] = server
        else:
            kit_settings = json.loads((kit / name).read_bytes())
            for keys, values in [(("enabledMcpjsonServers",), [NAME]),
                                 (("permissions", "deny"), kit_settings["permissions"]["deny"])]:
                cursor = obj
                for key in keys[:-1]:
                    cursor = cursor.setdefault(key, {})
                entries = cursor.setdefault(keys[-1], [])
                addition = record.setdefault(".".join(keys), {
                    "added": [],
                    "key_created": keys[-1] not in (before if len(keys) == 1
                                                    else before.get("permissions", {}))})
                if len(keys) == 2:
                    record.setdefault("permissions_created", "permissions" not in before)
                # Entries an older kit added and this one no longer ships (old server name).
                for value in [v for v in addition["added"] if v not in values]:
                    if value in entries:
                        entries.remove(value)
                    addition["added"].remove(value)
                for value in values:
                    if value not in entries:
                        entries.append(value)
                        if value not in addition["added"]:
                            addition["added"].append(value)
        if obj != before:
            plan(name, _dump(obj))

    copies = {f".claude/rules/{n}.md": kit / f".claude/rules/{n}.md"
              for n in ["orchestration", "delegation", "security"]}
    for source in (kit / ".claude/skills/agent-orchestration").rglob("*"):
        if source.is_file():
            copies[str(source.relative_to(kit))] = source
    for name in RETIRED:
        record = manifest["files"].get(name)
        if record is None:
            continue
        old = t.read(name)
        if old is None:
            continue
        if hashlib.sha256(old).hexdigest() != record["sha256"]:
            print("Warning: keeping modified retired file: " + name, file=sys.stderr)
            continue
        plan(name, _decode(record["original"]))
        del manifest["files"][name]
    for name, source in copies.items():
        data = source.read_bytes()
        old = t.read(name)
        if old is not None and old != data and not force:
            sys.exit("Conflicting file: " + name + "; use --force")
        if old != data:
            record = manifest["files"].setdefault(name, {"original": _encode(old)})
            record["sha256"] = hashlib.sha256(data).hexdigest()
            plan(name, data)

    raw = t.read("CLAUDE.md")
    content = raw or b""
    for start, end in [(START, END), (OLD_START, OLD_END)]:
        if start in content or end in content:
            if (content.count(start) != 1 or content.count(end) != 1
                    or content.index(end) < content.index(start)):
                sys.exit(f"Malformed {start.decode()} block")
            a, b = content.index(start), content.index(end) + len(end)
            updated = content[:a] + BLOCK + content[b:]
            break
    else:
        updated = (content + (b"\n" if content and not content.endswith(b"\n") else b"")
                   + BLOCK + b"\n")
    if updated != raw:
        manifest.setdefault("claude", {"original": _encode(raw)})
        manifest["claude"]["installed"] = _encode(updated)
        plan("CLAUDE.md", updated)
    if t.read(".ai/.gitignore") is None:
        data = b"*\n!.gitignore\n!config.json\n"
        plan(".ai/.gitignore", data)
        manifest["files"][".ai/.gitignore"] = {"original": None,
                                               "sha256": hashlib.sha256(data).hexdigest()}
    manifest_data = _dump(manifest)
    manifest_changed = old_manifest != manifest_data
    # Validate every destination before creating backups or changing the target.
    for name in changes:
        t.path(name)
    if changes or manifest_changed:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = t.path(".ai/backup-" + stamp)
        for name in changes:
            old = t.read(name)
            if old is not None:
                dest = backup / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(old)
        if manifest_changed and old_manifest is not None:
            backup.mkdir(parents=True, exist_ok=True)
            (backup / "manifest.previous").write_bytes(old_manifest)
        for name, data in changes.items():
            dest = t.path(name)
            if data is None:
                dest.unlink()
                parent = dest.parent
                while parent != target and not parent.is_symlink() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if manifest_changed:
            manifest_path.parent.mkdir(mode=0o700, exist_ok=True)
            manifest_path.write_bytes(manifest_data)
    return sorted(changes)


def uninstall(target: Path, purge: bool = False) -> None:
    """Undo `install` from its manifest, keeping files and values the user changed since."""
    target = target.resolve()
    t = _Target(target)
    mp = _manifest_path(target)
    if not mp.is_file():
        sys.exit("No install manifest found")
    m = json.loads(mp.read_bytes())
    # Validate all manifest paths before removing anything.
    for name in [*m["files"], *m["json"], "CLAUDE.md", ".ai"]:
        t.path(name)
    for name, record in m["json"].items():
        p = t.path(name)
        if not p.exists():
            continue
        obj = json.loads(p.read_bytes())
        before = json.loads(json.dumps(obj))
        if name == ".mcp.json" and "server_previous" in record:
            servers = obj.get("mcpServers", {})
            if servers.get(NAME) == record["server_installed"]:
                previous = record["server_previous"]
                if previous["present"]:
                    servers[NAME] = previous["value"]
                else:
                    servers.pop(NAME, None)
                if not servers and record["container_created"]:
                    obj.pop("mcpServers", None)
            else:
                print(f"Warning: keeping modified {NAME} server", file=sys.stderr)
        elif name == ".claude/settings.json":
            for label, keys in [("enabledMcpjsonServers", ("enabledMcpjsonServers",)),
                                ("permissions.deny", ("permissions", "deny"))]:
                addition = record[label]
                cursor = obj if len(keys) == 1 else obj.get("permissions", {})
                if keys[-1] in cursor:
                    cursor[keys[-1]] = [v for v in cursor[keys[-1]] if v not in addition["added"]]
                    if not cursor[keys[-1]] and addition["key_created"]:
                        cursor.pop(keys[-1])
            if not obj.get("permissions") and record.get("permissions_created"):
                obj.pop("permissions", None)
        if not obj and record["created"]:
            p.unlink()
        elif obj != before:
            original = _decode(record["original"])
            p.write_bytes(original if original is not None and obj == json.loads(original)
                          else _dump(obj))
    if "claude" in m:
        p = t.path("CLAUDE.md")
        record = m["claude"]
        if p.exists():
            data = p.read_bytes()
            if data == _decode(record["installed"]):
                _restore(p, _decode(record["original"]))
            elif data.count(START) == data.count(END) == 1 and data.index(START) < data.index(END):
                a, b = data.index(START), data.index(END) + len(END)
                original = _decode(record["original"]) or b""
                previous = b""
                for start, end in [(START, END), (OLD_START, OLD_END)]:
                    if start in original and end in original:
                        previous = original[original.index(start):original.index(end) + len(end)]
                        break
                p.write_bytes(data[:a] + previous + data[b:])
    for name, record in m["files"].items():
        p = t.path(name)
        if not p.exists():
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() == record["sha256"]:
            _restore(p, _decode(record["original"]))
            parent = p.parent
            while parent != target and parent.name != ".ai" and not parent.is_symlink():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        else:
            print("Warning: keeping modified file: " + name, file=sys.stderr)
    mp.unlink()
    if purge:
        shutil.rmtree(t.path(".ai"))


def _restore(p: Path, data: bytes | None) -> None:
    if data is None:
        p.unlink(missing_ok=True)
    else:
        p.write_bytes(data)


def main(argv: list[str]) -> None:
    """Entry point of scripts/setup.sh and scripts/uninstall.sh (kept for existing users)."""
    cmd, args = argv[0], argv[1:]
    flag = "--force" if cmd == "setup" else "--purge"
    if len(args) not in (1, 2) or (len(args) == 2 and args[1] != flag):
        sys.exit(f"Usage: {cmd}.sh <target_dir> [{flag}]")
    target = Path(args[0]).resolve()
    if cmd == "uninstall":
        uninstall(target, purge=len(args) == 2)
        return
    install(target, force=len(args) == 2)
    print("Open claude in the target once to trust the folder.")
    print("BRIDGE_PROJECT_ROOT=" + shlex.quote(str(target)) + " uv run --project "
          + shlex.quote(str(KIT)) + f" {NAME} health")


if __name__ == "__main__":
    main(sys.argv[1:])
