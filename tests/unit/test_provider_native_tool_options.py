"""Coverage for the per-agent (session-wise) native-tool-option pipeline.

Exercises:

* ``BaseTool.provider_native_option_schema`` and the ``ImageGenTool``
  override (free-form ``size`` + strict enums elsewhere).
* ``list_provider_native_tools`` / ``get_provider_native_option_schema``
  surface the schema for UIs.
* ``ImageGenTool.refresh_native_options`` re-reads ``ToolConfig.extra``
  while explicit constructor kwargs keep priority.
* ``NativeToolOptions`` (composition helper) get/set/list/apply +
  scratchpad persistence.
* ``/tool_options`` slash-command parses bare keys, enums, JSON, and
  the ``--reset`` form.
"""

import json
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tool_catalog import (
    get_provider_native_option_schema,
    list_provider_native_tools,
)
from kohakuterrarium.builtins.tools.image_gen import ImageGenTool
from kohakuterrarium.builtins.user_commands.tool_options import ToolOptionsCommand
from kohakuterrarium.core.agent_native_tools import (
    NATIVE_TOOL_OPTIONS_KEY,
    NativeToolOptions,
)
from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.modules.tool.base import BaseTool, ToolConfig
from kohakuterrarium.modules.user_command.base import UserCommandContext

# ── Schema declaration ─────────────────────────────────────────────


def test_base_tool_default_schema_is_empty():
    assert BaseTool.provider_native_option_schema() == {}


def test_image_gen_size_is_freeform_with_suggestions():
    """Size must accept custom values like 2048x2048 — NOT enum."""
    spec = ImageGenTool.provider_native_option_schema()["size"]
    assert spec["type"] == "string"
    assert "1024x1024" in spec["suggestions"]
    assert "2048x2048" in spec["suggestions"]
    assert spec.get("placeholder")


def test_image_gen_quality_is_strict_enum():
    spec = ImageGenTool.provider_native_option_schema()["quality"]
    assert spec["type"] == "enum"
    assert set(spec["values"]) == {"auto", "low", "medium", "high"}


def test_list_provider_native_tools_includes_option_schema():
    listing = list_provider_native_tools()
    by_name = {entry["name"]: entry for entry in listing}
    image_gen = by_name.get("image_gen")
    assert image_gen is not None
    assert "option_schema" in image_gen
    assert "size" in image_gen["option_schema"]


def test_get_provider_native_option_schema_returns_same_shape():
    direct = get_provider_native_option_schema("image_gen")
    assert direct == ImageGenTool.provider_native_option_schema()


def test_get_provider_native_option_schema_unknown_tool():
    assert get_provider_native_option_schema("does_not_exist") == {}


# ── ImageGenTool refresh_native_options ───────────────────────────


def test_refresh_native_options_picks_up_post_construction_extra():
    tool = ImageGenTool(config=ToolConfig(extra={"size": "1024x1024"}))
    assert tool.size == "1024x1024"
    tool.config.extra = {"size": "1536x1024", "quality": "low"}
    tool.refresh_native_options()
    assert tool.size == "1536x1024"
    assert tool.quality == "low"


def test_explicit_kwargs_still_beat_extra_after_refresh():
    """Programmatic ``size=...`` kwarg keeps priority over extra."""
    tool = ImageGenTool(size="1024x1024", config=ToolConfig(extra={}))
    assert tool.size == "1024x1024"
    tool.config.extra = {"size": "1536x1024"}
    tool.refresh_native_options()
    assert tool.size == "1024x1024"  # explicit kwarg still wins


# ── Per-agent NativeToolOptions helper ────────────────────────────


class _FakeRegistry:
    def __init__(self, tools: dict[str, BaseTool]) -> None:
        self._tools = tools

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools)


def _make_fake_agent(tool: ImageGenTool) -> SimpleNamespace:
    """Stand-in agent with just the surfaces NativeToolOptions touches."""
    scratchpad = Scratchpad()
    session = SimpleNamespace(scratchpad=scratchpad)
    registry = _FakeRegistry({tool.tool_name: tool})
    return SimpleNamespace(
        config=SimpleNamespace(name="fake"),
        registry=registry,
        session=session,
        _explicit_session=None,
    )


def test_set_updates_tool_in_place_and_persists():
    tool = ImageGenTool()
    agent = _make_fake_agent(tool)
    helper = NativeToolOptions(agent)

    applied = helper.set("image_gen", {"size": "2048x2048", "quality": "high"})
    assert applied == {"size": "2048x2048", "quality": "high"}
    # Tool refreshed in-place
    assert tool.size == "2048x2048"
    assert tool.quality == "high"
    # Scratchpad written
    raw = agent.session.scratchpad.get(NATIVE_TOOL_OPTIONS_KEY)
    assert json.loads(raw) == {"image_gen": {"size": "2048x2048", "quality": "high"}}


def test_partial_set_merges_into_existing_overrides():
    """Multi-step edit: setting one field then another should preserve
    the first. Regression for the studio modules panel sending only
    changed keys per debounced save."""
    tool = ImageGenTool()
    agent = _make_fake_agent(tool)
    helper = NativeToolOptions(agent)

    # Step 1 — user sets size from the studio panel.
    helper.set("image_gen", {"size": "2048x2048"})
    assert helper.get("image_gen") == {"size": "2048x2048"}
    assert tool.size == "2048x2048"

    # Step 2 — user sets quality. The frontend only sends the changed
    # key; size must survive.
    applied = helper.set("image_gen", {"quality": "high"})
    assert applied == {"size": "2048x2048", "quality": "high"}
    assert helper.get("image_gen") == {"size": "2048x2048", "quality": "high"}
    assert tool.size == "2048x2048"
    assert tool.quality == "high"


def test_partial_set_with_none_deletes_one_key():
    """Sending ``{"<key>": None}`` should drop just that key from the
    override, not wipe the rest."""
    tool = ImageGenTool()
    agent = _make_fake_agent(tool)
    helper = NativeToolOptions(agent)

    helper.set("image_gen", {"size": "2048x2048", "quality": "high"})
    applied = helper.set("image_gen", {"size": None})
    assert applied == {"quality": "high"}
    assert helper.get("image_gen") == {"quality": "high"}


def test_set_with_empty_dict_clears_override():
    tool = ImageGenTool()
    agent = _make_fake_agent(tool)
    helper = NativeToolOptions(agent)
    helper.set("image_gen", {"size": "2048x2048"})
    assert helper.get("image_gen") == {"size": "2048x2048"}
    # Clearing
    cleared = helper.set("image_gen", {})
    assert cleared == {}
    assert helper.get("image_gen") == {}
    # Scratchpad key removed entirely
    assert agent.session.scratchpad.get(NATIVE_TOOL_OPTIONS_KEY) is None


def test_apply_restores_from_scratchpad():
    tool = ImageGenTool()
    agent = _make_fake_agent(tool)
    # Simulate resume: scratchpad already populated, no in-memory map
    agent.session.scratchpad.set(
        NATIVE_TOOL_OPTIONS_KEY,
        json.dumps({"image_gen": {"size": "1536x1024"}}),
    )
    helper = NativeToolOptions(agent)
    assert helper.get("image_gen") == {}  # before apply
    helper.apply()
    assert helper.get("image_gen") == {"size": "1536x1024"}
    # And the tool was refreshed in place
    assert tool.size == "1536x1024"


def test_apply_with_corrupt_scratchpad_is_safe():
    tool = ImageGenTool()
    agent = _make_fake_agent(tool)
    agent.session.scratchpad.set(NATIVE_TOOL_OPTIONS_KEY, "not-json{")
    helper = NativeToolOptions(agent)
    helper.apply()  # must not raise
    assert helper.list() == {}


def test_set_drops_empty_string_values():
    """Empty strings are treated as 'unset' so the JSON form's empty
    inputs don't persist phantom blanks."""
    tool = ImageGenTool()
    agent = _make_fake_agent(tool)
    helper = NativeToolOptions(agent)
    applied = helper.set("image_gen", {"size": "1024x1024", "quality": ""})
    assert applied == {"size": "1024x1024"}


# ── /tool_options slash command ───────────────────────────────────


def _make_context(tool: ImageGenTool):
    agent = _make_fake_agent(tool)
    agent.native_tool_options = NativeToolOptions(agent)
    ctx = UserCommandContext(agent=agent, session=agent.session)
    return ctx, agent


@pytest.mark.asyncio
async def test_slash_overview_lists_tools():
    tool = ImageGenTool()
    ctx, _ = _make_context(tool)
    cmd = ToolOptionsCommand()
    res = await cmd.execute("", ctx)
    assert res.error is None
    assert "image_gen" in (res.output or "")
    assert "(defaults)" in (res.output or "")


@pytest.mark.asyncio
async def test_slash_set_enum_and_freeform():
    tool = ImageGenTool()
    ctx, agent = _make_context(tool)
    cmd = ToolOptionsCommand()
    res = await cmd.execute("image_gen size=2048x2048 quality=high", ctx)
    assert res.error is None, res.error
    assert agent.native_tool_options.get("image_gen") == {
        "size": "2048x2048",
        "quality": "high",
    }
    assert tool.size == "2048x2048"


@pytest.mark.asyncio
async def test_slash_rejects_invalid_enum_value():
    tool = ImageGenTool()
    ctx, agent = _make_context(tool)
    cmd = ToolOptionsCommand()
    res = await cmd.execute("image_gen quality=ultra", ctx)
    assert res.error is not None
    assert "ultra" in res.error
    # Nothing got saved
    assert agent.native_tool_options.get("image_gen") == {}


@pytest.mark.asyncio
async def test_slash_reset_clears_overrides():
    tool = ImageGenTool()
    ctx, agent = _make_context(tool)
    agent.native_tool_options.set("image_gen", {"size": "2048x2048"})
    cmd = ToolOptionsCommand()
    res = await cmd.execute("image_gen --reset", ctx)
    assert res.error is None
    assert agent.native_tool_options.get("image_gen") == {}


@pytest.mark.asyncio
async def test_slash_unknown_tool_errors():
    tool = ImageGenTool()
    ctx, _ = _make_context(tool)
    cmd = ToolOptionsCommand()
    res = await cmd.execute("nonexistent size=1024x1024", ctx)
    assert res.error is not None
    assert "nonexistent" in res.error
