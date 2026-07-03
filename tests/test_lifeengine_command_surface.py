"""轴二-6: the tool registry is the single source of truth for the tool surface.

Pins TOOL_REGISTRY (what register() actually registers) against plugin.yaml's
provides_tools so they can't drift — the 2026-07-02 audit found plugin.yaml
missing 8 registered tools (五面漂移). Also checks each tool's JSON-schema name
matches its registered name, so a schema copy/alias can't silently diverge (e.g.
the life_venture / life_activity back-compat pair).
"""
from __future__ import annotations

import re
from pathlib import Path

from lifeengine import TOOL_REGISTRY

_PLUGIN_YAML = Path(__file__).resolve().parent.parent / "plugin.yaml"
_TOP_KEY = re.compile(r"^[a-z_]+:\s*$")


def _plugin_yaml_tools() -> list[str]:
    tools: list[str] = []
    in_section = False
    for line in _PLUGIN_YAML.read_text(encoding="utf-8").splitlines():
        if _TOP_KEY.match(line):
            in_section = line.strip() == "provides_tools:"
            continue
        if in_section:
            s = line.strip()
            if s.startswith("- "):
                tools.append(s[2:].strip())
    return tools


def test_tool_registry_matches_plugin_yaml():
    registered = {name for name, *_ in TOOL_REGISTRY}
    yaml_tools = set(_plugin_yaml_tools())
    assert registered == yaml_tools, (
        "registry vs plugin.yaml drift — "
        f"missing from plugin.yaml: {sorted(registered - yaml_tools)}; "
        f"stale in plugin.yaml: {sorted(yaml_tools - registered)}"
    )


def test_no_duplicate_registered_tool_names():
    names = [name for name, *_ in TOOL_REGISTRY]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate tool registrations: {dupes}"


def test_each_tool_wellformed_and_schema_name_matches():
    for name, schema, handler, desc, emoji in TOOL_REGISTRY:
        assert isinstance(schema, dict) and schema.get("name") == name, (
            f"{name}: schema name is {schema.get('name')!r}, expected {name!r}"
        )
        assert callable(handler), f"{name}: handler is not callable"
        assert desc, f"{name}: empty description"
        assert emoji, f"{name}: missing emoji"
