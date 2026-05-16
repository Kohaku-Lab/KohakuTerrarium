"""Unit tests for :mod:`kohakuterrarium.studio.editors.codegen_plugin`."""

import json

import pytest

from kohakuterrarium.studio.editors import codegen_plugin as cg
from kohakuterrarium.studio.editors.codegen_common import RoundTripError

# ── render_new ───────────────────────────────────────────────


class TestRenderNew:
    def test_basic(self):
        out = cg.render_new(
            {
                "name": "demo",
                "priority": 10,
                "description": "x",
                "enabled_hooks": [{"name": "on_load", "body": "return 'loaded'"}],
            }
        )
        # Round-trip: rendered module parses back to the same form.
        back = cg.parse_back(out)
        assert back["mode"] == "simple"
        assert back["form"]["name"] == "demo"
        assert back["form"]["priority"] == 10
        assert back["form"]["class_name"] == "DemoPlugin"
        hooks = {h["name"]: h["body"] for h in back["form"]["enabled_hooks"]}
        assert "on_load" in hooks
        assert "return 'loaded'" in hooks["on_load"]

    def test_default_class_name(self):
        out = cg.render_new({"name": "demo"})
        back = cg.parse_back(out)
        assert back["form"]["class_name"] == "DemoPlugin"

    def test_no_hooks(self):
        out = cg.render_new({"name": "demo"})
        back = cg.parse_back(out)
        # No hooks requested → no hook methods emitted.
        assert back["form"]["class_name"] == "DemoPlugin"
        assert back["form"]["enabled_hooks"] == []


# ── sidecar_files ───────────────────────────────────────────


class TestSidecarFiles:
    def test_empty_schema_returns_empty(self):
        assert cg.sidecar_files({}) == {}
        assert cg.sidecar_files({"options_schema": []}) == {}

    def test_with_schema(self):
        out = cg.sidecar_files(
            {
                "options_schema": [
                    {"name": "k", "type_hint": "int", "default": 0},
                ]
            }
        )
        assert list(out.keys()) == [".schema.json"]
        parsed = json.loads(out[".schema.json"])
        # Each entry is normalized to the canonical 5-field shape.
        assert parsed == [
            {
                "name": "k",
                "type_hint": "int",
                "default": 0,
                "required": False,
                "description": "",
            }
        ]

    def test_invalid_entries_filtered(self):
        out = cg.sidecar_files({"options_schema": ["not-a-dict"]})
        # All entries filtered out → empty.
        assert out == {}

    def test_non_list_returns_empty(self):
        assert cg.sidecar_files({"options_schema": "not-a-list"}) == {}


# ── _normalize_schema_param ──────────────────────────────────


class TestNormalizeSchemaParam:
    def test_basic(self):
        out = cg._normalize_schema_param(
            {"name": "x", "type_hint": "int", "required": "yes"}
        )
        assert out["name"] == "x"
        assert out["type_hint"] == "int"
        assert out["required"] is True

    def test_missing_fields(self):
        out = cg._normalize_schema_param({})
        assert out["name"] == ""
        assert out["type_hint"] == ""


# ── update_existing ─────────────────────────────────────────


class TestUpdateExisting:
    def test_unknown_class_raises(self):
        with pytest.raises(RoundTripError):
            cg.update_existing("x = 1\n", {"class_name": "Foo"}, "")

    def test_preserves_existing_body(self):
        src = (
            "class P(BasePlugin):\n"
            "    name = 'demo'\n"
            "    priority = 50\n"
            "    async def on_load(self, agent):\n"
            "        return 'preserved'\n"
        )
        out = cg.update_existing(
            src,
            {
                "name": "demo",
                "priority": 50,
                "enabled_hooks": [{"name": "on_load"}],
            },
            "",
        )
        # Caller supplied no body for on_load → original body kept verbatim.
        back = cg.parse_back(out)
        hooks = {h["name"]: h["body"] for h in back["form"]["enabled_hooks"]}
        assert "return 'preserved'" in hooks["on_load"]

    def test_first_class_fallback(self):
        src = (
            "class P(BasePlugin):\n"
            "    async def on_load(self, agent):\n"
            "        return None\n"
        )
        # No class_name — fall back to first_class.
        out = cg.update_existing(
            src,
            {"name": "demo", "enabled_hooks": [{"name": "on_load"}]},
            "",
        )
        back = cg.parse_back(out)
        assert back["form"]["class_name"] == "P"
        assert [h["name"] for h in back["form"]["enabled_hooks"]] == ["on_load"]

    def test_caller_supplied_body(self):
        src = (
            "class P(BasePlugin):\n"
            "    async def on_load(self, agent):\n"
            "        return 'old'\n"
        )
        out = cg.update_existing(
            src,
            {
                "name": "demo",
                "enabled_hooks": [
                    {"name": "on_load", "body": "return 'new'"},
                ],
            },
            "",
        )
        # Caller-supplied body replaces the old one.
        back = cg.parse_back(out)
        hooks = {h["name"]: h["body"] for h in back["form"]["enabled_hooks"]}
        assert "return 'new'" in hooks["on_load"]
        assert "old" not in hooks["on_load"]


# ── parse_back ──────────────────────────────────────────────


class TestParseBack:
    def test_parse_failure_returns_raw(self):
        out = cg.parse_back("def broken(:\n")
        assert out["mode"] == "raw"

    def test_no_class_returns_raw(self):
        out = cg.parse_back("x = 1\n")
        assert out["mode"] == "raw"

    def test_basic_plugin(self):
        src = (
            "class P(BasePlugin):\n"
            "    @property\n"
            "    def name(self) -> str:\n"
            "        return 'demo'\n"
            "    priority = 25\n"
            "    async def on_load(self, agent):\n"
            "        return None\n"
        )
        out = cg.parse_back(src)
        assert out["mode"] == "simple"
        assert out["form"]["name"] == "demo"
        assert out["form"]["priority"] == 25
        assert any(h["name"] == "on_load" for h in out["form"]["enabled_hooks"])

    def test_with_sidecar_schema(self):
        src = (
            "class P(BasePlugin):\n"
            "    async def on_load(self, agent):\n"
            "        return None\n"
        )
        sidecar = [{"name": "rate_limit", "type_hint": "int", "default": 60}]
        out = cg.parse_back(src, sidecar_schema=sidecar)
        # Sidecar entries are normalized into options_schema.
        assert out["form"]["options_schema"] == [
            {
                "name": "rate_limit",
                "type_hint": "int",
                "default": 60,
                "required": False,
                "description": "",
            }
        ]

    def test_non_list_sidecar_ignored(self):
        src = "class P(BasePlugin):\n    pass\n"
        out = cg.parse_back(src, sidecar_schema="not-a-list")
        assert out["form"]["options_schema"] == []


# ── _hook_context ───────────────────────────────────────────


class TestHookContext:
    def test_known_hook(self):
        out = cg._hook_context({"name": "on_load", "body": "return None"})
        assert out["name"] == "on_load"
        assert "args_signature" in out

    def test_unknown_hook(self):
        out = cg._hook_context({"name": "ghost", "body": "x"})
        assert out["name"] == "ghost"
        assert out["args_signature"] == ""

    def test_empty_body_defaults_to_return_none(self):
        out = cg._hook_context({"name": "on_load", "body": ""})
        assert out["body"] == "return None"


# ── _pick_plugin_class ──────────────────────────────────────


class TestPickPluginClass:
    def test_attribute_base(self):
        import libcst as cst

        tree = cst.parse_module("class P(mod.BasePlugin): pass\n")
        cls = cg._pick_plugin_class(tree)
        assert cls.name.value == "P"

    def test_falls_back_to_first(self):
        import libcst as cst

        tree = cst.parse_module("class A: pass\n")
        cls = cg._pick_plugin_class(tree)
        assert cls.name.value == "A"

    def test_no_classes(self):
        import libcst as cst

        tree = cst.parse_module("x = 1\n")
        assert cg._pick_plugin_class(tree) is None


# ── _read_int_attr ──────────────────────────────────────────


class TestReadIntAttr:
    def test_simple_assign(self):
        import libcst as cst

        cls = cst.parse_module("class P:\n    priority = 25\n").body[0]
        assert cg._read_int_attr(cls, "priority", default=50) == 25

    def test_annotated_assign(self):
        import libcst as cst

        cls = cst.parse_module("class P:\n    priority: int = 7\n").body[0]
        assert cg._read_int_attr(cls, "priority", default=50) == 7

    def test_missing_returns_default(self):
        import libcst as cst

        cls = cst.parse_module("class P:\n    pass\n").body[0]
        assert cg._read_int_attr(cls, "priority", default=42) == 42


# ── _to_class_name ─────────────────────────────────────────


class TestToClassName:
    def test_basic(self):
        assert cg._to_class_name("demo") == "DemoPlugin"

    def test_dashed(self):
        assert cg._to_class_name("cool-demo") == "CoolDemoPlugin"
