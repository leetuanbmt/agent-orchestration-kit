import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest


KIT = Path(__file__).resolve().parents[2]


def run(script, target, *flags, ok=True):
    result = subprocess.run(
        ['bash', str(KIT / 'scripts' / script), str(target), *flags],
        capture_output=True, text=True,
        env={**os.environ, 'GIT_CEILING_DIRECTORIES': str(target.parent)},
    )
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


@pytest.fixture
def repo(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    return tmp_path


def snapshot(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*')
        if p.is_file() and '.git' not in p.relative_to(root).parts
    }


def write(root, name, data):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)


def manifest_path(root):
    git_dir = subprocess.run(
        ['git', '-C', str(root), 'rev-parse', '--absolute-git-dir'],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return Path(git_dir) / 'agent-bridge/install-manifest.json'


# @pt.id KIT-SETUP-001-fresh
# @pt.item Fresh installation and idempotency
# @pt.condition Setup runs twice in an empty git work tree
# @pt.expected All workflow assets exist and the second run changes no file hashes
# @pt.target scripts/setup.sh
# @pt.method integration
# @pt.legacy none (new kit)
def test_fresh_install(repo):
    result = run('setup.sh', repo)
    server = json.loads((repo / '.mcp.json').read_text())['mcpServers']['agent-bridge']
    assert server['args'] == ['run', '--project', str(KIT), 'agent-bridge', 'serve']
    assert server['env'] == {'BRIDGE_PROJECT_ROOT': '${CLAUDE_PROJECT_DIR:-.}'}
    settings = json.loads((repo / '.claude/settings.json').read_text())
    assert settings == json.loads((KIT / '.claude/settings.json').read_text())
    for name in ['orchestration', 'delegation', 'security']:
        assert (repo / f'.claude/rules/{name}.md').read_bytes() == (KIT / f'.claude/rules/{name}.md').read_bytes()
    assert (repo / '.claude/skills/agent-orchestration/SKILL.md').read_bytes() == (KIT / '.claude/skills/agent-orchestration/SKILL.md').read_bytes()
    assert '<!-- agent-bridge:start -->' in (repo / 'CLAUDE.md').read_text()
    assert (repo / '.ai/.gitignore').read_bytes() == b'*\n!.gitignore\n!config.json\n'
    manifest = manifest_path(repo)
    assert manifest.is_file()
    assert manifest.parent.stat().st_mode & 0o777 == 0o700
    assert not list((repo / '.ai').rglob('install-manifest.json'))
    assert not list(p for p in repo.rglob('install-manifest.json') if '.git' not in p.relative_to(repo).parts)
    status = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=all'], check=True, capture_output=True, text=True).stdout
    assert [line[3:] for line in status.splitlines() if line[3:].startswith('.ai/')] == ['.ai/.gitignore']
    assert str(KIT) in result.stdout and str(repo) in result.stdout
    before = snapshot(repo)
    manifest_before = manifest.read_bytes(), manifest.stat().st_mtime_ns
    run('setup.sh', repo)
    assert snapshot(repo) == before
    assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == manifest_before
    run('uninstall.sh', repo)
    assert not manifest.exists()
    assert not (repo / '.mcp.json').exists()
    assert not (repo / '.claude/settings.json').exists()
    assert not (repo / 'CLAUDE.md').exists()
    assert not list((repo / '.claude').rglob('*.md'))
    assert (repo / '.ai').is_dir()


# @pt.id KIT-SETUP-002-preserve
# @pt.item Existing project content survives installation and removal
# @pt.condition Project has existing documentation, MCP servers, and settings
# @pt.expected Setup preserves existing values and uninstall restores original bytes
# @pt.target scripts/setup.sh scripts/uninstall.sh
# @pt.method integration
# @pt.legacy none (new kit)
def test_preserve_existing(repo):
    originals = {
        'CLAUDE.md': 'Project instructions\r\nDo not change this.',
        '.mcp.json': '{"mcpServers":{"other":{"command":"other"}},"extra":true}\n',
        '.claude/settings.json': '{"enabledMcpjsonServers":["other"],"permissions":{"deny":["existing"],"allow":["Read"]},"extra":42}\n',
    }
    for name, data in originals.items():
        write(repo, name, data)
    run('setup.sh', repo)
    assert (repo / 'CLAUDE.md').read_bytes().startswith(originals['CLAUDE.md'].encode())
    mcp = json.loads((repo / '.mcp.json').read_text())
    del mcp['mcpServers']['agent-bridge']
    assert mcp == json.loads(originals['.mcp.json'])
    settings = json.loads((repo / '.claude/settings.json').read_text())
    assert settings['extra'] == 42
    assert settings['permissions']['allow'] == ['Read']
    assert settings['permissions']['deny'][0] == 'existing'
    assert settings['enabledMcpjsonServers'] == ['other', 'agent-bridge']
    for name, data in originals.items():
        assert any(p.read_bytes() == data.encode() for p in (repo / '.ai').glob('backup-*/' + name))
    before = snapshot(repo)
    run('setup.sh', repo)
    assert snapshot(repo) == before
    run('uninstall.sh', repo)
    for name, data in originals.items():
        assert (repo / name).read_bytes() == data.encode()
    assert not list((repo / '.claude').rglob('*.md'))


# @pt.id KIT-SETUP-003-conflicts
# @pt.item Conflicting destinations are rejected without partial writes
# @pt.condition Server or copied rule differs from the kit
# @pt.expected Normal setup changes nothing and forced installation is reversible
# @pt.target scripts/setup.sh scripts/uninstall.sh
# @pt.method integration
# @pt.legacy none (new kit)
@pytest.mark.parametrize('name,data', [('.mcp.json', '{"mcpServers":{"agent-bridge":{"command":"custom"}}}'), ('.claude/rules/delegation.md', 'custom rule\n')])
def test_conflicts(repo, name, data):
    write(repo, name, data)
    before = snapshot(repo)
    run('setup.sh', repo, ok=False)
    assert snapshot(repo) == before
    run('setup.sh', repo, '--force')
    run('uninstall.sh', repo)
    assert (repo / name).read_text() == data


# @pt.id KIT-SETUP-004-modified-rule
# @pt.item User edits survive uninstall
# @pt.condition A copied rule is edited after installation
# @pt.expected Uninstall keeps the file with a warning and preserves later settings
# @pt.target scripts/uninstall.sh
# @pt.method integration
# @pt.legacy none (new kit)
def test_uninstall_modified(repo):
    run('setup.sh', repo)
    write(repo, '.claude/rules/security.md', 'user modification\n')
    before = snapshot(repo)
    run('setup.sh', repo, ok=False)
    assert snapshot(repo) == before
    settings_path = repo / '.claude/settings.json'
    settings = json.loads(settings_path.read_text())
    settings['permissions']['deny'].append('later rule')
    settings['later'] = True
    settings_path.write_text(json.dumps(settings))
    result = run('uninstall.sh', repo)
    assert 'Warning' in result.stderr and 'security.md' in result.stderr
    assert (repo / '.claude/rules/security.md').read_text() == 'user modification\n'
    assert json.loads(settings_path.read_text()) == {'permissions': {'deny': ['later rule']}, 'later': True}


# @pt.id KIT-SETUP-005-purge
# @pt.item Purge removes project-local task history
# @pt.condition Installation is removed with --purge
# @pt.expected The entire target .ai directory is removed
# @pt.target scripts/uninstall.sh
# @pt.method integration
# @pt.legacy none (new kit)
def test_purge(repo):
    run('setup.sh', repo)
    write(repo, '.ai/history.txt', 'history')
    run('uninstall.sh', repo, '--purge')
    assert not (repo / '.ai').exists()


# @pt.id KIT-SETUP-006-invalid-target
# @pt.item Unsafe setup targets are rejected
# @pt.condition Target is a non-git directory or the kit itself
# @pt.expected Setup fails without writing target files
# @pt.target scripts/setup.sh
# @pt.method integration
# @pt.legacy none (new kit)
def test_invalid_target(tmp_path):
    run('setup.sh', tmp_path, ok=False)
    assert not list(tmp_path.iterdir())
    run('setup.sh', KIT, ok=False)


# @pt.id KIT-SETUP-007-preexisting-assets
# @pt.item Preexisting identical assets and settings remain owned by the user
# @pt.condition A project already contains kit-compatible rules and settings
# @pt.expected Uninstall preserves assets it did not add and replaces only managed blocks
# @pt.target scripts/setup.sh scripts/uninstall.sh
# @pt.method integration
# @pt.legacy none (new kit)
def test_preexisting_assets(repo):
    rule = '.claude/rules/delegation.md'
    write(repo, rule, (KIT / '.claude/rules/delegation.md').read_text())
    write(repo, '.claude/settings.json', (KIT / '.claude/settings.json').read_text())
    original = 'before\n<!-- agent-bridge:start -->old<!-- agent-bridge:end -->\nafter\n'
    write(repo, 'CLAUDE.md', original)
    write(repo, '.ai/.gitignore', 'custom\n')
    run('setup.sh', repo)
    assert (repo / 'CLAUDE.md').read_text().startswith('before\n<!-- agent-bridge:start -->')
    assert (repo / 'CLAUDE.md').read_text().endswith('<!-- agent-bridge:end -->\nafter\n')
    run('uninstall.sh', repo)
    assert (repo / rule).read_bytes() == (KIT / '.claude/rules/delegation.md').read_bytes()
    assert (repo / '.claude/settings.json').read_bytes() == (KIT / '.claude/settings.json').read_bytes()
    assert (repo / 'CLAUDE.md').read_text() == original
    assert (repo / '.ai/.gitignore').read_text() == 'custom\n'


# @pt.id KIT-SETUP-008-invalid-json
# @pt.item Invalid project JSON is rejected before writing files
# @pt.condition An existing MCP or settings file contains invalid JSON
# @pt.expected Setup reports the file name and leaves all project files unchanged
# @pt.target scripts/setup.sh
# @pt.method integration
# @pt.legacy none (new kit)
@pytest.mark.parametrize('name', ['.mcp.json', '.claude/settings.json'])
@pytest.mark.parametrize('data', ['{invalid', ''])
def test_invalid_json(repo, name, data):
    write(repo, name, data)
    before = snapshot(repo)
    result = run('setup.sh', repo, ok=False)
    assert 'Invalid JSON: ' + name in result.stderr
    assert snapshot(repo) == before
    assert not manifest_path(repo).parent.exists()
    assert not (repo / '.ai').exists()


# @pt.id KIT-SETUP-009-retired-names
# @pt.item Upgrading an install that used the codex-* rule and skill names
# @pt.condition The manifest lists old kit files: one unedited, one edited by the user
# @pt.expected Setup removes the unedited one and its manifest entry, keeps the edited one with a warning
# @pt.target scripts/setup.sh
# @pt.method integration
# @pt.legacy codex-*.md rules and codex-delegation skill (0.1.x)
def test_retired_names(repo):
    run('setup.sh', repo)
    manifest = json.loads(manifest_path(repo).read_text())
    old = {'.claude/rules/codex-review.md': b'old review rule\n',
           '.claude/skills/codex-delegation/SKILL.md': b'old skill\n'}
    for name, data in old.items():
        write(repo, name, data.decode())
        manifest['files'][name] = {'original': None, 'sha256': hashlib.sha256(data).hexdigest()}
    manifest_path(repo).write_text(json.dumps(manifest))
    write(repo, '.claude/rules/codex-review.md', 'edited by the user\n')
    result = run('setup.sh', repo)
    assert 'keeping modified retired file: .claude/rules/codex-review.md' in result.stderr
    assert (repo / '.claude/rules/codex-review.md').read_text() == 'edited by the user\n'
    assert not (repo / '.claude/skills/codex-delegation').exists()
    files = json.loads(manifest_path(repo).read_text())['files']
    assert '.claude/skills/codex-delegation/SKILL.md' not in files
    run('uninstall.sh', repo)
    assert (repo / '.claude/rules/codex-review.md').exists()


# @pt.id KIT-SETUP-010-old-name
# @pt.item Upgrading an install made when the server was called codex-bridge
# @pt.condition .mcp.json, settings, CLAUDE.md block and the git-dir manifest use the old name
# @pt.expected Setup replaces every old entry with agent-bridge, moves the state directory, and uninstall still restores the original project
# @pt.target bridge.install.install
# @pt.method integration
# @pt.legacy codex-bridge server name (0.1.x)
def test_old_server_name(repo):
    import base64
    run('setup.sh', repo)
    old_dir = manifest_path(repo).parent.with_name('codex-bridge')
    manifest_path(repo).parent.rename(old_dir)
    for name in ['.mcp.json', '.claude/settings.json', 'CLAUDE.md']:
        write(repo, name, (repo / name).read_text().replace('agent-bridge', 'codex-bridge'))
    m = json.loads((old_dir / 'install-manifest.json').read_text().replace('agent-bridge', 'codex-bridge'))
    m['claude']['installed'] = base64.b64encode((repo / 'CLAUDE.md').read_bytes()).decode()
    (old_dir / 'install-manifest.json').write_text(json.dumps(m))
    run('setup.sh', repo)
    assert not old_dir.exists() and manifest_path(repo).is_file()
    for name in ['.mcp.json', '.claude/settings.json', 'CLAUDE.md']:
        assert 'codex-bridge' not in (repo / name).read_text(), name
    assert json.loads((repo / '.mcp.json').read_text())['mcpServers']['agent-bridge']['args'][-2:] == ['agent-bridge', 'serve']
    settings = json.loads((repo / '.claude/settings.json').read_text())
    kit_settings = json.loads((KIT / '.claude/settings.json').read_text())
    assert settings['enabledMcpjsonServers'] == ['agent-bridge']
    assert sorted(settings['permissions']['deny']) == sorted(kit_settings['permissions']['deny'])
    run('uninstall.sh', repo)
    assert not (repo / '.mcp.json').exists()
    assert not (repo / '.claude/settings.json').exists()
    assert not (repo / 'CLAUDE.md').exists()


# @pt.id KIT-SETUP-011-cli-init
# @pt.item `agent-bridge init` installs into the given folder and picks the server command
# @pt.condition init run through the installed agent-bridge executable on PATH, then through python -m bridge; bare agent-bridge without a terminal
# @pt.expected the tool install registers `agent-bridge serve` with no kit path; other launches use `uv run --project <kit>`; the bare command prints help instead of opening the TUI
# @pt.target bridge.cli._server_entry
# @pt.method integration
# @pt.legacy none (new kit)
def test_cli_init(repo, tmp_path_factory):
    import sys
    exe = str(Path(sys.executable).parent / 'agent-bridge')
    env = {**os.environ, 'GIT_CEILING_DIRECTORIES': str(repo.parent),
           'PATH': str(Path(exe).parent) + os.pathsep + os.environ['PATH']}
    out = subprocess.run([exe, 'init', str(repo)], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    server = json.loads((repo / '.mcp.json').read_text())['mcpServers']['agent-bridge']
    assert server['command'] == 'agent-bridge' and server['args'] == ['serve']
    other = tmp_path_factory.mktemp('other')
    subprocess.run(['git', 'init', str(other)], check=True, capture_output=True)
    out = subprocess.run([sys.executable, '-m', 'bridge', 'init', str(other)], cwd=KIT,
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    server = json.loads((other / '.mcp.json').read_text())['mcpServers']['agent-bridge']
    assert server['args'] == ['run', '--project', str(KIT), 'agent-bridge', 'serve']
    bare = subprocess.run([exe], cwd=repo, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, env=env)
    assert bare.returncode == 0 and 'init' in bare.stdout and 'serve' in bare.stdout


# @pt.id KIT-SETUP-012-config-init
# @pt.item `agent-bridge config-init` starts .ai/orchestrator.yaml from a shipped example
# @pt.condition the installed executable lists examples without a terminal, then writes one from a pseudo-terminal, then runs again
# @pt.expected the listing names every example; without a terminal writing is refused; with one the file is written, git-ignored and passes config-check; an existing file is never overwritten
# @pt.target bridge.cli._run
# @pt.method integration
# @pt.legacy none (new kit)
def test_config_init(repo, tmp_path):
    import pty
    import sys
    exe = str(Path(sys.executable).parent / 'agent-bridge')
    env = {**os.environ, 'GIT_CEILING_DIRECTORIES': str(repo.parent), 'HOME': str(tmp_path)}
    listing = subprocess.run([exe, 'config-init'], cwd=repo, capture_output=True, text=True,
                             env=env)
    assert listing.returncode == 0, listing.stderr
    assert {line.split()[0] for line in listing.stdout.splitlines()[:5]} == {
        p.stem for p in (KIT / 'examples').glob('*.yaml')}
    piped = subprocess.run([exe, 'config-init', 'single-agent'], cwd=repo, capture_output=True,
                           text=True, env=env, stdin=subprocess.DEVNULL)
    assert piped.returncode == 2 and 'refused' in piped.stderr

    def in_terminal():
        main, tty = pty.openpty()
        proc = subprocess.Popen([exe, 'config-init', 'single-agent'], cwd=repo, env=env,
                                stdin=tty, stdout=tty, stderr=tty)
        os.close(tty)
        out = b''
        try:  # read while the child runs: macOS drops pty output once the child is gone
            while chunk := os.read(main, 4096):
                out += chunk
        except OSError:
            pass
        os.close(main)
        return proc.wait(), out.decode()

    code, out = in_terminal()
    target = repo / '.ai' / 'orchestrator.yaml'
    assert code == 0, out
    assert target.read_text() == (KIT / 'examples' / 'single-agent.yaml').read_text()
    ignored = subprocess.run(['git', '-C', str(repo), 'check-ignore', '-q', str(target)])
    assert ignored.returncode == 0
    check = subprocess.run([exe, 'config-check'], cwd=repo, capture_output=True, text=True,
                           env=env)
    assert check.returncode == 0 and str(target) in check.stdout, check.stderr
    target.write_text('# mine\n')
    code, out = in_terminal()
    assert code == 1 and 'exists' in out
    assert target.read_text() == '# mine\n'
