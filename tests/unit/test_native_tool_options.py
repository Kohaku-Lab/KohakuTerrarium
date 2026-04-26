"""Tests for provider-native tool option storage and validation."""

import json
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_native_tools import NATIVE_TOOL_OPTIONS_KEY
from kohakuterrarium.core.native_tool_validation import (
    NativeToolOptionError,
    validate_native_tool_options,
)
from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.core.session import Session
from kohakuterrarium.builtins.tools.image_gen import ImageGenTool


class _Registry:
    def __init__(self, tool):
        self._tool = tool

    def get_tool(self, name):
        return self._tool if name == self._tool.tool_name else None


class _State(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Agent:
    def __init__(self):
        self.config = SimpleNamespace(name="agent")
        self.session = Session(key="agent")
        self.session.scratchpad = Scratchpad()
        self.tool = ImageGenTool()
        self.registry = _Registry(self.tool)
        self.session_store = SimpleNamespace(state=_State())


def test_validate_image_gen_options_rejects_bad_size():
    schema = ImageGenTool.provider_native_option_schema()
    with pytest.raises(NativeToolOptionError):
        validate_native_tool_options("image_gen", {"size": "99999x1"}, schema)


def test_scratchpad_hides_reserved_keys():
    pad = Scratchpad()
    pad.set("note", "visible")
    pad.set("__secret__", "hidden")

    assert pad.to_dict() == {"note": "visible"}
    assert pad.list_keys() == ["note"]
    assert "__secret__" in pad.list_keys(include_reserved=True)
    assert "hidden" not in pad.to_prompt_section()


def test_native_tool_options_migrates_legacy_scratchpad_to_private_state():
    from kohakuterrarium.core.agent_native_tools import NativeToolOptions

    agent = _Agent()
    agent.session.scratchpad.set(
        NATIVE_TOOL_OPTIONS_KEY,
        json.dumps({"image_gen": {"size": "1024x1024", "quality": "high"}}),
    )
    helper = NativeToolOptions(agent)

    helper.apply()

    assert helper.get("image_gen") == {"size": "1024x1024", "quality": "high"}
    assert agent.session.scratchpad.get(NATIVE_TOOL_OPTIONS_KEY) is None
    assert agent.session_store.state["agent:native_tool_options"] == {
        "image_gen": {"size": "1024x1024", "quality": "high"}
    }
    assert agent.tool.size == "1024x1024"
    assert agent.tool.quality == "high"
