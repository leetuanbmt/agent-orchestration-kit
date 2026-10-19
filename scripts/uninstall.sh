#!/usr/bin/env bash
set -euo pipefail
python3 - "$@" <<'PY'
import base64
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

args = sys.argv[1:]
if len(args) not in (1, 2) or (len(args) == 2 and args[1] != '--purge'):
    sys.exit('Usage: uninstall.sh <target_dir> [--purge]')
target = pathlib.Path(args[0]).resolve()

def path(name):
    p = target / name
    if not p.resolve().is_relative_to(target) or any(q.is_symlink() for q in [p, *p.parents] if q != target and q.is_relative_to(target)):
        sys.exit('Refusing symlink or external path: ' + name)
    return p

def decode(value):
    return None if value is None else base64.b64decode(value)

def restore(p, data):
    if data is None:
        p.unlink(missing_ok=True)
    else:
        p.write_bytes(data)

git_dir = pathlib.Path(subprocess.run(['git', '-C', str(target), 'rev-parse', '--absolute-git-dir'], check=True, capture_output=True, text=True).stdout.strip())
mp = git_dir / 'codex-bridge/install-manifest.json'
if mp.parent.is_symlink() or mp.is_symlink():
    sys.exit('Refusing symlinked install manifest')
if not mp.is_file():
    sys.exit('No install manifest found')
m = json.loads(mp.read_bytes())
# Validate all manifest paths before removing anything.
for name in [*m['files'], *m['json'], 'CLAUDE.md', '.ai']:
    path(name)
for name, record in m['json'].items():
    p = path(name)
    if not p.exists():
        continue
    obj = json.loads(p.read_bytes())
    before = json.loads(json.dumps(obj))
    if name == '.mcp.json' and 'server_previous' in record:
        servers = obj.get('mcpServers', {})
        if servers.get('codex-bridge') == record['server_installed']:
            previous = record['server_previous']
            if previous['present']:
                servers['codex-bridge'] = previous['value']
            else:
                servers.pop('codex-bridge', None)
            if not servers and record['container_created']:
                obj.pop('mcpServers', None)
        else:
            print('Warning: keeping modified codex-bridge server', file=sys.stderr)
    elif name == '.claude/settings.json':
        for label, keys in [('enabledMcpjsonServers', ('enabledMcpjsonServers',)), ('permissions.deny', ('permissions', 'deny'))]:
            addition = record[label]
            cursor = obj if len(keys) == 1 else obj.get('permissions', {})
            if keys[-1] in cursor:
                cursor[keys[-1]] = [v for v in cursor[keys[-1]] if v not in addition['added']]
                if not cursor[keys[-1]] and addition['key_created']:
                    cursor.pop(keys[-1])
        if not obj.get('permissions') and record.get('permissions_created'):
            obj.pop('permissions', None)
    if not obj and record['created']:
        p.unlink()
    elif obj != before:
        original = decode(record['original'])
        p.write_bytes(original if original is not None and obj == json.loads(original) else (json.dumps(obj, indent=2) + '\n').encode())
if 'claude' in m:
    p = path('CLAUDE.md')
    record = m['claude']
    if p.exists():
        data = p.read_bytes()
        if data == decode(record['installed']):
            restore(p, decode(record['original']))
        else:
            start, end = b'<!-- codex-bridge:start -->', b'<!-- codex-bridge:end -->'
            if data.count(start) == data.count(end) == 1 and data.index(start) < data.index(end):
                a, b = data.index(start), data.index(end) + len(end)
                original = decode(record['original']) or b''
                previous = b''
                if start in original and end in original:
                    previous = original[original.index(start):original.index(end) + len(end)]
                p.write_bytes(data[:a] + previous + data[b:])
for name, record in m['files'].items():
    p = path(name)
    if not p.exists():
        continue
    if hashlib.sha256(p.read_bytes()).hexdigest() == record['sha256']:
        restore(p, decode(record['original']))
        parent = p.parent
        while parent != target and parent.name != '.ai':
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    else:
        print('Warning: keeping modified file: ' + name, file=sys.stderr)
mp.unlink()
if len(args) == 2:
    shutil.rmtree(path('.ai'))
PY
