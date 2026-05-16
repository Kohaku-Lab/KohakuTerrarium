"""Unit tests for ``llm/tool_schemas.py`` — builtin tool parameter schemas.

Behavior-first: assert the invariants the docstring + dependent
``build_tool_schemas`` rely on — every entry is a valid JSON-Schema
object, every ``required`` name is a declared property, and every
``enum`` is non-empty. A malformed entry here silently strips
structured tool arguments at the provider boundary.
"""

from kohakuterrarium.llm.tool_schemas import _BUILTIN_SCHEMAS


def _walk_properties(schema):
    """Yield every (name, prop_schema) recursively, including nested items."""
    props = schema.get("properties", {})
    for name, prop in props.items():
        yield name, prop
        if isinstance(prop, dict):
            items = prop.get("items")
            if isinstance(items, dict):
                yield from _walk_properties(items)
            if "properties" in prop:
                yield from _walk_properties(prop)


class TestBuiltinSchemas:
    def test_every_entry_is_an_object_schema(self):
        for name, schema in _BUILTIN_SCHEMAS.items():
            assert schema.get("type") == "object", f"{name} is not type=object"
            assert isinstance(
                schema.get("properties"), dict
            ), f"{name} missing properties dict"

    def test_required_names_are_declared_properties(self):
        for name, schema in _BUILTIN_SCHEMAS.items():
            declared = set(schema.get("properties", {}))
            for req in schema.get("required", []):
                assert (
                    req in declared
                ), f"{name}: required '{req}' is not a declared property"

    def test_every_enum_is_a_nonempty_list(self):
        for name, schema in _BUILTIN_SCHEMAS.items():
            for prop_name, prop in _walk_properties(schema):
                if isinstance(prop, dict) and "enum" in prop:
                    assert (
                        isinstance(prop["enum"], list) and prop["enum"]
                    ), f"{name}.{prop_name} has an empty/invalid enum"

    def test_known_core_tools_present(self):
        # build_tool_schemas relies on these being accurate, not the
        # generic {content: string} fallback
        for tool in ("bash", "read", "write", "edit", "grep", "glob"):
            assert tool in _BUILTIN_SCHEMAS

    def test_bash_command_is_required_string(self):
        bash = _BUILTIN_SCHEMAS["bash"]
        assert bash["required"] == ["command"]
        assert bash["properties"]["command"]["type"] == "string"

    def test_property_types_are_valid_json_schema_types(self):
        valid = {"string", "integer", "number", "boolean", "object", "array"}
        for name, schema in _BUILTIN_SCHEMAS.items():
            for prop_name, prop in _walk_properties(schema):
                if isinstance(prop, dict) and "type" in prop:
                    assert (
                        prop["type"] in valid
                    ), f"{name}.{prop_name} has invalid type '{prop['type']}'"
