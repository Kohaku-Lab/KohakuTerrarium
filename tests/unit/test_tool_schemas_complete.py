"""Regression test: every registered builtin tool has an explicit
schema in ``_BUILTIN_SCHEMAS``.

Without this guard, new builtin tools fall back to a generic
``{content: string}`` schema and the LLM only ever passes a single
``content`` field — silently losing structured arguments. The
``show_card`` tool hit exactly this bug in v1, which is what this
test prevents.
"""

import pytest

from kohakuterrarium.builtins import tools as _builtins  # registers tools
from kohakuterrarium.builtins.tool_catalog import list_builtin_tools
from kohakuterrarium.llm.tools import _BUILTIN_SCHEMAS
from kohakuterrarium.mcp import tools as _mcp_tools  # registers mcp_*

_ = _builtins  # silence unused-import lint
_ = _mcp_tools


# Provider-native tools (image_gen, etc.) advertise their args via
# their provider's wire format — we still keep a basic schema entry
# so the discovery list is uniform, but tests should accept that they
# may have only minimal fields. No exclusions today; if any tool
# legitimately doesn't need a schema, list it here.
_OPTIONAL_SCHEMA_TOOLS: set[str] = set()


def test_every_registered_builtin_tool_has_a_schema():
    registered = set(list_builtin_tools())
    schemas = set(_BUILTIN_SCHEMAS.keys())
    missing = (registered - schemas) - _OPTIONAL_SCHEMA_TOOLS
    assert not missing, (
        f"Registered builtin tools without an entry in _BUILTIN_SCHEMAS: "
        f"{sorted(missing)}.\n"
        f"All tools: {sorted(registered)}\n"
        f"Without an explicit schema, the LLM gets a generic "
        f"{{content: string}} fallback and structured arguments are lost. "
        f"Add a schema to ``src/kohakuterrarium/llm/tools.py:_BUILTIN_SCHEMAS``."
    )


def test_no_stale_schemas_for_unregistered_tools():
    """Catch the inverse: schemas that no longer correspond to a
    registered tool (renamed / removed)."""
    registered = set(list_builtin_tools())
    schemas = set(_BUILTIN_SCHEMAS.keys())
    stale = schemas - registered
    assert not stale, (
        f"_BUILTIN_SCHEMAS contains entries for tools that aren't "
        f"registered: {sorted(stale)}. Either restore the tool or "
        f"remove the schema."
    )


@pytest.mark.parametrize("tool_name", sorted(_BUILTIN_SCHEMAS.keys()))
def test_schema_well_formed(tool_name):
    """Every schema is a JSON-schema object with proper structure."""
    schema = _BUILTIN_SCHEMAS[tool_name]
    assert isinstance(schema, dict), f"{tool_name}: schema is not a dict"
    assert (
        schema.get("type") == "object"
    ), f"{tool_name}: top-level type must be 'object'"
    properties = schema.get("properties")
    assert isinstance(properties, dict), f"{tool_name}: 'properties' must be a dict"
    # Required must reference declared properties when present.
    required = schema.get("required", [])
    assert isinstance(required, list), f"{tool_name}: 'required' must be a list"
    for r in required:
        assert (
            r in properties
        ), f"{tool_name}: 'required' references '{r}' but it isn't in properties"
    # Every property has a type and (ideally) a description.
    for prop_name, prop_schema in properties.items():
        assert isinstance(
            prop_schema, dict
        ), f"{tool_name}.{prop_name}: property schema is not a dict"
        assert (
            "type" in prop_schema or "enum" in prop_schema
        ), f"{tool_name}.{prop_name}: missing 'type' or 'enum'"
