"""Unit tests for :mod:`kohakuterrarium.modules.tool.media_policy`."""

from kohakuterrarium.modules.tool.base import BaseTool, ToolResult
from kohakuterrarium.modules.tool.media_policy import (
    METADATA_KEY,
    MediaPolicy,
    resolve_media_policy,
)


class _DefaultTool(BaseTool):
    @property
    def tool_name(self):
        return "default"

    @property
    def description(self):
        return "d"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="")


class _ReferenceTool(_DefaultTool):
    media_policy = MediaPolicy(persist=False, pinned=False)


class TestMediaPolicy:
    def test_defaults_persist_and_pin(self):
        policy = MediaPolicy()
        assert policy.persist is True and policy.pinned is True
        assert policy.to_dict() == {"persist": True, "pinned": True}

    def test_coerce_accepts_policy_mapping_and_nothing(self):
        base = MediaPolicy(persist=False, pinned=False)
        assert MediaPolicy.coerce(MediaPolicy(pinned=False)) == MediaPolicy(
            pinned=False
        )
        # A mapping overrides only the keys it names; the rest come from base.
        assert MediaPolicy.coerce({"pinned": True}, base) == MediaPolicy(
            persist=False, pinned=True
        )
        assert MediaPolicy.coerce(None, base) is base
        assert MediaPolicy.coerce("junk", base) is base

    def test_tool_default_is_the_baseline(self):
        assert resolve_media_policy(_DefaultTool(), {}) == MediaPolicy()
        assert resolve_media_policy(_ReferenceTool(), None) == MediaPolicy(
            persist=False, pinned=False
        )

    def test_result_metadata_overrides_the_tool_default(self):
        tool = _ReferenceTool()
        # A generated result from a reference tool asks to be persisted.
        assert resolve_media_policy(tool, {METADATA_KEY: {"persist": True}}) == (
            MediaPolicy(persist=True, pinned=False)
        )
        assert (
            resolve_media_policy(tool, {METADATA_KEY: MediaPolicy()}) == MediaPolicy()
        )

    def test_objects_without_a_policy_get_the_default(self):
        assert resolve_media_policy(object(), {}) == MediaPolicy()
