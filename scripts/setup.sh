#!/usr/bin/env bash
set -euo pipefail
KIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python3 - "$KIT_DIR" "$@" <<'PY'
import base64
import datetime
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys

kit = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
if len(args) not in (1, 2) or (len(args) == 2 and args[1] != '--force'):
    sys.exit('Usage: setup.sh <target_dir> [--force]')
target = pathlib.Path(args[0]).resolve()
if target == kit or subprocess.run(['git', '-C', str(target), 'rev-parse', '--is-inside-work-tree'], capture_output=True).stdout.strip() != b'true':
    sys.exit('Target must be a git work tree other than the kit')

def path(name):
    p = target / name
    if not p.resolve().is_relative_to(target) or any(q.is_symlink() for q in [p, *p.parents] if q != target and q.is_relative_to(target)):
        sys.exit('Refusing symlink or external path: ' + name)
    return p

def read(name):
    p = path(name)
    return p.read_bytes() if p.exists() else None

def encode(data):
    return None if data is None else base64.b64encode(data).decode()

def dump(obj):
    return (json.dumps(obj, indent=2) + '\n').encode()

git_dir = pathlib.Path(subprocess.run(['git', '-C', str(target), 'rev-parse', '--absolute-git-dir'], check=True, capture_output=True, text=True).stdout.strip())
manifest_dir = git_dir / 'codex-bridge'
manifest_path = manifest_dir / 'install-manifest.json'
if manifest_dir.is_symlink() or manifest_path.is_symlink():
    sys.exit('Refusing symlinked install manifest')
old_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
manifest = json.loads(old_manifest) if old_manifest else {'files': {}, 'json': {}}
changes = {}

def plan(name, data):
    if read(name) != data:
        changes[name] = data

server = {'command': 'uv', 'args': ['run', '--project', str(kit), 'codex-bridge', 'serve'], 'env': {'BRIDGE_PROJECT_ROOT': '${CLAUDE_PROJECT_DIR:-.}'}}
for name in ['.mcp.json', '.claude/settings.json']:
    raw = read(name)
    try:
        obj = json.loads(raw) if raw is not None else {}
    except (ValueError, UnicodeError):
        sys.exit('Invalid JSON: ' + name)
    record = manifest['json'].setdefault(name, {'created': raw is None, 'original': encode(raw)})
    before = json.loads(json.dumps(obj))
    if name == '.mcp.json':
        servers = obj.setdefault('mcpServers', {})
        existing = servers.get('codex-bridge')
        if 'codex-bridge' in servers and existing != server and len(args) != 2:
            sys.exit('Conflicting codex-bridge server; use --force')
        if existing != server:
            record.setdefault('server_previous', {'present': 'codex-bridge' in servers, 'value': existing})
            record['server_installed'] = server
            record.setdefault('container_created', 'mcpServers' not in before)
            servers['codex-bridge'] = server
    else:
        kit_settings = json.loads((kit / name).read_bytes())
        for keys, values in [(('enabledMcpjsonServers',), ['codex-bridge']), (('permissions', 'deny'), kit_settings['permissions']['deny'])]:
            cursor = obj
            for key in keys[:-1]:
                cursor = cursor.setdefault(key, {})
            entries = cursor.setdefault(keys[-1], [])
            label = '.'.join(keys)
            addition = record.setdefault(label, {'added': [], 'key_created': keys[-1] not in (before if len(keys) == 1 else before.get('permissions', {}))})
            if len(keys) == 2:
                record.setdefault('permissions_created', 'permissions' not in before)
            for value in values:
                if value not in entries:
                    entries.append(value)
                    if value not in addition['added']:
                        addition['added'].append(value)
    if obj != before:
        plan(name, dump(obj))

copies = {f'.claude/rules/{n}.md': kit / f'.claude/rules/{n}.md' for n in ['orchestration', 'delegation', 'security']}
for source in (kit / '.claude/skills/agent-orchestration').rglob('*'):
    if source.is_file():
        copies[str(source.relative_to(kit))] = source
# Files earlier kit versions installed under codex-* names: remove them while they are unedited.
retired = [f'.claude/rules/codex-{n}.md' for n in ['orchestration', 'delegation', 'review', 'security']]
retired.append('.claude/skills/codex-delegation/SKILL.md')
for name in retired:
    record = manifest['files'].get(name)
    old = read(name)
    if record is None or old is None:
        continue
    if hashlib.sha256(old).hexdigest() != record['sha256']:
        print('Warning: keeping modified retired file: ' + name, file=sys.stderr)
        continue
    original = record['original']
    plan(name, None if original is None else base64.b64decode(original))
    del manifest['files'][name]
for name, source in copies.items():
    data = source.read_bytes()
    old = read(name)
    if old is not None and old != data and len(args) != 2:
        sys.exit('Conflicting file: ' + name + '; use --force')
    if old != data:
        record = manifest['files'].setdefault(name, {'original': encode(old)})
        record['sha256'] = hashlib.sha256(data).hexdigest()
        plan(name, data)

start = b'<!-- codex-bridge:start -->'
end = b'<!-- codex-bridge:end -->'
block = start + b'\nRoles map to agents in .ai/orchestrator.yaml (bridge_list_agents); without it Claude plans and reviews, Codex executes.\nDelegate implementation to the executor role first. Approvals, reassignment, and account actions are human-only.\nFollow .claude/rules/{orchestration,delegation,security}.md and the .claude/skills/agent-orchestration/SKILL.md skill.\n' + end
raw = read('CLAUDE.md')
content = raw or b''
if start in content or end in content:
    if content.count(start) != 1 or content.count(end) != 1 or content.index(end) < content.index(start):
        sys.exit('Malformed codex-bridge block')
    a, b = content.index(start), content.index(end) + len(end)
    updated = content[:a] + block + content[b:]
else:
    updated = content + (b'\n' if content and not content.endswith(b'\n') else b'') + block + b'\n'
if updated != raw:
    manifest.setdefault('claude', {'original': encode(raw)})
    manifest['claude']['installed'] = encode(updated)
    plan('CLAUDE.md', updated)
if read('.ai/.gitignore') is None:
    data = b'*\n!.gitignore\n!config.json\n'
    plan('.ai/.gitignore', data)
    manifest['files']['.ai/.gitignore'] = {'original': None, 'sha256': hashlib.sha256(data).hexdigest()}
manifest_data = dump(manifest)
manifest_changed = old_manifest != manifest_data
# Validate every destination before creating backups or changing the target.
for name in changes:
    path(name)
if changes or manifest_changed:
    backup = path('.ai/backup-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    for name in changes:
        old = read(name)
        if old is not None:
            dest = backup / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(old)
    if manifest_changed and old_manifest is not None:
        backup.mkdir(parents=True, exist_ok=True)
        (backup / 'manifest.previous').write_bytes(old_manifest)
    for name, data in changes.items():
        dest = path(name)
        if data is None:
            dest.unlink()
            parent = dest.parent
            while parent != target and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    if manifest_changed:
        manifest_dir.mkdir(mode=0o700, exist_ok=True)
        manifest_path.write_bytes(manifest_data)
print('Open claude in the target once to trust the folder.')
print('BRIDGE_PROJECT_ROOT=' + shlex.quote(str(target)) + ' uv run --project ' + shlex.quote(str(kit)) + ' codex-bridge health')
PY
