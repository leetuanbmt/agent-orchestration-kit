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
    shipped = [".claude/settings.json", ".ai/config.example.json",
               *(str(p.relative_to(ROOT)) for p in (ROOT / "examples").glob("*.yaml")),
               *(f".claude/rules/{n}.md" for n in ("orchestration", "delegation", "security")),
               *(str(p.relative_to(ROOT)) for p in
                 (ROOT / ".claude/skills/agent-orchestration").rglob("*") if p.is_file())]
    for name in shipped:
        source = next((src for src in mapping if name == src or name.startswith(src + "/")),
                      None)
        assert source, f"{name} is not in the wheel"
        assert mapping[source] == f"bridge/kit/{source}"


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
