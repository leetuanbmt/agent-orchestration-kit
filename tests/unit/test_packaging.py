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
