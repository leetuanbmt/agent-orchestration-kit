"""Project configuration can extend the built-in safety lists."""

import pytest

from bridge.config import DEFAULT_ENV_DENY, DEFAULT_PROTECTED_PATHS, BridgeConfig


# @pt.id KIT-CFG-FLOOR-001-additive
# @pt.item Built-in protection and environment deny lists are a floor
# @pt.condition project lists are empty, extra-only, partial, repeated, or reordered
# @pt.expected defaults come first, followed by unique extras in project order
# @pt.target bridge.config.BridgeConfig
# @pt.method unit
# @pt.legacy CFG-01 and SEC-04: project lists could replace all built-in protections
@pytest.mark.parametrize("field,defaults,extra", [
    ("protected_paths", DEFAULT_PROTECTED_PATHS, "secrets/**"),
    ("env_deny", DEFAULT_ENV_DENY, "MY_VAR"),
])
def test_safety_lists_have_additive_floor(field, defaults, extra):
    assert getattr(BridgeConfig(), field) == defaults
    for entries, expected in (
        ([], defaults),
        ([extra], [*defaults, extra]),
        ([defaults[0]], defaults),
        ([*defaults, *defaults], defaults),
        (list(reversed(defaults)), defaults),
        ([extra, defaults[0], extra, "OTHER/**", "OTHER/**"],
         [*defaults, extra, "OTHER/**"]),
    ):
        cfg = BridgeConfig.model_validate({field: entries})
        assert getattr(cfg, field) == expected
