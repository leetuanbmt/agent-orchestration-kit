"""The wheel must carry every file `agent-bridge init` copies, or a non-editable install breaks."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


# @pt.id KIT-PKG-001-kit-files-in-wheel
# @pt.item Kit files shipped in the wheel
# @pt.condition the files init and config-init copy (rules, skill, settings, examples, config example) and pyproject's force-include map
# @pt.expected each file is mapped to bridge/kit/<same path>, where a wheel install looks for it
# @pt.target pyproject.toml, bridge.install.install
# @pt.method unit
# @pt.legacy none
def test_kit_files_in_wheel():
    mapping = tomllib.loads((ROOT / "pyproject.toml").read_text())[
        "tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    shipped = ["templates/claude-settings.json", ".ai/config.example.json",
               *(str(p.relative_to(ROOT)) for p in (ROOT / "examples").glob("*.yaml")),
               *(f".claude/rules/{n}.md" for n in ("orchestration", "delegation", "security")),
               *(str(p.relative_to(ROOT)) for p in
                 (ROOT / ".claude/skills/agent-orchestration").rglob("*") if p.is_file())]
    for name in shipped:
        source = next((src for src in mapping if name == src or name.startswith(src + "/")),
                      None)
        assert source, f"{name} is not in the wheel"
        assert mapping[source] == f"bridge/kit/{source}"


# @pt.id KIT-PKG-005-settings-template-denies
# @pt.item Repository deny list is covered by the shipped template
# @pt.condition the deny lists of .claude/settings.json and templates/claude-settings.json
# @pt.expected every deny entry of .claude/settings.json is also in the template, so a credential deny added only to the repo file fails and names the missing entries
# @pt.target .claude/settings.json, templates/claude-settings.json, bridge.executor._claude_credential_denies
# @pt.method unit
# @pt.legacy none
def test_settings_template_denies_human_only_commands():
    import json

    repo = json.loads((ROOT / ".claude/settings.json").read_text())["permissions"]["deny"]
    template = json.loads((ROOT / "templates/claude-settings.json").read_text())["permissions"]["deny"]
    missing = [entry for entry in repo if entry not in template]
    assert not missing, f"denies missing from templates/claude-settings.json: {missing}"


# @pt.id KIT-PKG-006-credential-denies-from-template
# @pt.item Claude adapter credential Read denies in a wheel-shaped kit
# @pt.condition a kit directory containing only templates/claude-settings.json
# @pt.expected _claude_credential_denies includes the fixed Read denies for ~/.codex/auth.json, ~/.codex-accounts/**, ~/.agent-bridge/homes/** and ~/.gemini/antigravity-cli/**, and every rule starts with Read(
# @pt.target bridge.executor._claude_credential_denies
# @pt.method unit
# @pt.legacy none
def test_credential_denies_read_settings_template(monkeypatch, tmp_path):
    import bridge.install
    from bridge.executor import _claude_credential_denies

    template = ROOT / "templates/claude-settings.json"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates/claude-settings.json").write_text(template.read_text())
    required = ["Read(~/.codex/auth.json)", "Read(~/.codex-accounts/**)",
                "Read(~/.agent-bridge/homes/**)", "Read(~/.gemini/antigravity-cli/**)"]
    monkeypatch.setattr(bridge.install, "KIT", tmp_path)
    _claude_credential_denies.cache_clear()
    try:
        denies = list(_claude_credential_denies())
        for rule in required:
            assert rule in denies, (rule, denies)
        assert all(rule.startswith("Read(") for rule in denies), denies
    finally:
        _claude_credential_denies.cache_clear()


# @pt.id KIT-PKG-002-dashboard-assets
# @pt.item Dashboard assets shipped in package
# @pt.condition wheel configuration and static asset files
# @pt.expected bridge package contains all three dashboard assets
# @pt.target bridge/static, pyproject.toml
# @pt.method unit
# @pt.legacy none
def test_dashboard_assets_ship_in_package():
    packages = tomllib.loads((ROOT / "pyproject.toml").read_text())[
        "tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "bridge" in packages
    for name in ("index.html", "app.css", "app.js"):
        assert (ROOT / "bridge/static" / name).is_file()


# @pt.id KIT-PKG-003-dashboard-safe-dom
# @pt.item Dashboard never parses HTML data
# @pt.condition dashboard JavaScript source
# @pt.expected no HTML parsing or dynamic code execution sinks
# @pt.target bridge/static/app.js
# @pt.method unit
# @pt.legacy none
def test_dashboard_js_never_parses_html():
    source = (ROOT / "bridge/static/app.js").read_text()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function"):
        assert forbidden not in source


# @pt.id KIT-PKG-004-dashboard-local-assets
# @pt.item Dashboard uses local assets only
# @pt.condition dashboard HTML CSS and JavaScript sources
# @pt.expected no external URLs
# @pt.target bridge/static
# @pt.method unit
# @pt.legacy none
def test_dashboard_has_no_external_urls():
    for name in ("index.html", "app.css", "app.js"):
        source = (ROOT / "bridge/static" / name).read_text()
        assert "http://" not in source
        assert "https://" not in source


# @pt.id T0039-launch-code
# @pt.item Launch URL carries a fresh secret
# @pt.condition Fake server and browser without binding sockets
# @pt.expected Printed and opened URL contains server launch code of at least 32 URL-safe characters
# @pt.target bridge.webui.serve
# @pt.method unit
# @pt.legacy none
def test_web_launch_code(monkeypatch, capsys):
    import re
    from bridge import webui

    launch_codes, opened = [], []
    class Server:
        server_address = ("127.0.0.1", 1234)
        closed = False
        def serve_forever(self):
            raise KeyboardInterrupt
        def server_close(self):
            self.closed = True
    servers = []
    def make_server(port, roots=None, launch_code=None, read_only=False):
        assert port == 0
        launch_codes.append(launch_code)
        server = Server()
        servers.append(server)
        return server
    monkeypatch.setattr(webui, "make_server", make_server)
    monkeypatch.setattr(webui.webbrowser, "open", opened.append)
    for _ in range(2):
        webui.serve(0, open_browser=True)
        lines = capsys.readouterr().out.splitlines()
        assert re.fullmatch(r"[A-Za-z0-9_-]{32,}", launch_codes[-1])
        assert lines[0] == f"agent-bridge web: http://127.0.0.1:1234/?code={launch_codes[-1]}"
        assert opened[-1] == lines[0].removeprefix("agent-bridge web: ")
        assert "works once" in lines[1] and "do not share" in lines[1]
        assert servers[-1].closed
    assert launch_codes[0] != launch_codes[1]


# @pt.id T0039-browser-session
# @pt.item Browser session authentication
# @pt.condition Dashboard JavaScript source
# @pt.expected No browser credential storage or token header; query removed and translated session guidance
# @pt.target bridge/static/app.js
# @pt.method unit
# @pt.legacy none
def test_dashboard_launch_code_handling():
    source = (ROOT / "bridge/static/app.js").read_text()
    for forbidden in ("sessionStorage", "agent-bridge-token", "document.cookie", "X-Bridge-Token"):
        assert forbidden not in source
    for expected in ("location.search", 'launchParams.has("code")', "history.replaceState",
                     "location.pathname + location.hash", "credentials: 'same-origin'",
                     "response.status === 401", "restart it if the link was used",
                     "khởi động lại nếu đường dẫn đã được dùng"):
        assert expected in source
