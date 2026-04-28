"""Unit tests for ``kohakuterrarium.api.studio.introspect``."""

from pathlib import Path

from kohakuterrarium.studio.catalog.introspect import (
    builtin_schema,
    custom_schema,
    resolve_module_source,
)

SAMPLE_TOOL = '''\
"""Discord send tool (abbreviated fixture)."""

from typing import Any
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult


class SendDiscordTool(BaseTool):
    def __init__(
        self,
        client_name: str = "default",
        filtered_keywords: list[str] | None = None,
        keywords_file: str | None = None,
        drop_base_chance: float = 0.25,
        drop_increment: float = 0.15,
        drop_max_chance: float = 0.7,
        dedup_threshold: float = 0.85,
        dedup_window: int = 5,
        *,
        verbose: bool = False,
        config=None,
    ):
        super().__init__(config)

    @property
    def tool_name(self) -> str:
        return "send_discord"
'''


def test_builtin_schema_tools_has_common_fields():
    out = builtin_schema("tools")
    names = [p["name"] for p in out["params"]]
    assert "timeout" in names
    assert "max_output" in names
    assert "notify_controller_on_background_complete" in names


def test_builtin_schema_subagents_has_turn_fields():
    out = builtin_schema("subagents")
    names = [p["name"] for p in out["params"]]
    assert "max_turns" in names
    assert "interactive" in names
    assert "can_modify" in names


def test_builtin_schema_unknown_kind_is_empty():
    assert builtin_schema("bogus") == {"params": [], "warnings": []}


def test_custom_schema_extracts_init_params():
    out = custom_schema(SAMPLE_TOOL, "SendDiscordTool")
    # `config` is hidden; everything else surfaces.
    by_name = {p["name"]: p for p in out["params"]}
    assert "config" not in by_name
    assert "self" not in by_name
    assert by_name["client_name"]["default"] == "default"
    assert by_name["client_name"]["type_hint"] == "str"
    assert by_name["client_name"]["required"] is False
    # Union type hint survives (ast.unparse)
    assert "None" in by_name["filtered_keywords"]["type_hint"]
    # Float default preserved
    assert by_name["drop_base_chance"]["default"] == 0.25
    # Keyword-only arg surfaces too
    assert by_name["verbose"]["default"] is False


def test_custom_schema_handles_missing_class():
    out = custom_schema(SAMPLE_TOOL, "NopeTool")
    assert out["params"] == []
    codes = [w["code"] for w in out["warnings"]]
    assert "class_not_found" in codes


def test_custom_schema_handles_syntax_error():
    out = custom_schema("def broken(:\n", "X")
    codes = [w["code"] for w in out["warnings"]]
    assert "syntax_error" in codes


def test_custom_schema_falls_back_to_first_class_without_name():
    # When no class_name is passed, we use the first top-level class.
    out = custom_schema(SAMPLE_TOOL, None)
    assert any(p["name"] == "client_name" for p in out["params"])


def test_custom_schema_no_init():
    source = "class X:\n" "    def method(self, a: int = 1) -> None:\n" "        pass\n"
    out = custom_schema(source, "X")
    assert out["params"] == []


def test_custom_schema_hides_config_kwarg():
    # Confirm the framework-injected `config` kwarg is dropped from
    # the form — it's plumbing, not a user option.
    source = (
        "class MyTool:\n"
        "    def __init__(self, a: int = 1, config=None):\n"
        "        pass\n"
    )
    out = custom_schema(source, "MyTool")
    names = [p["name"] for p in out["params"]]
    assert "config" not in names
    assert names == ["a"]


def test_custom_schema_warns_on_variadic():
    source = (
        "class MyTool:\n" "    def __init__(self, *args, **kwargs):\n" "        pass\n"
    )
    out = custom_schema(source, "MyTool")
    codes = [w["code"] for w in out["warnings"]]
    assert "variadic_ignored" in codes


def test_resolve_module_source_relative(tmp_path: Path):
    (tmp_path / "custom").mkdir()
    (tmp_path / "custom" / "my_tool.py").write_text(SAMPLE_TOOL, encoding="utf-8")
    src = resolve_module_source(tmp_path, "./custom/my_tool.py")
    assert src is not None
    assert "class SendDiscordTool" in src


def test_resolve_module_source_missing_returns_none(tmp_path: Path):
    src = resolve_module_source(tmp_path, "./does_not_exist.py")
    assert src is None


def test_resolve_module_source_dotted(tmp_path: Path):
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "tools").mkdir()
    (tmp_path / "modules" / "tools" / "my.py").write_text(SAMPLE_TOOL, encoding="utf-8")
    src = resolve_module_source(tmp_path, "modules.tools.my")
    assert src is not None
    assert "class SendDiscordTool" in src
