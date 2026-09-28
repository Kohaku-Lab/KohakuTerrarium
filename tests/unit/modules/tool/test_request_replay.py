"""Implementation defaults and explicit request replay overrides."""

import pytest

from kohakuterrarium.builtins.tools.image_gen import ImageGenTool
from kohakuterrarium.modules.tool.base import ToolConfig
from kohakuterrarium.modules.tool.request_replay import tool_request_replay


class UnsafeImageTool(ImageGenTool):
    request_replay = "forbid"


def test_implementation_default_and_config_override_in_both_directions():
    assert tool_request_replay(object()) == "allow"
    assert tool_request_replay(ImageGenTool()) == "allow"
    assert tool_request_replay(UnsafeImageTool()) == "forbid"
    assert (
        tool_request_replay(UnsafeImageTool(config=ToolConfig(request_replay="allow")))
        == "allow"
    )
    tool = ImageGenTool(config=ToolConfig(request_replay="forbid"))
    tool.config.extra = {"quality": "low"}
    assert tool_request_replay(tool) == "forbid"


def test_invalid_programmatic_override_is_rejected():
    with pytest.raises(ValueError, match="request_replay"):
        tool_request_replay(ImageGenTool(config=ToolConfig(request_replay=True)))
