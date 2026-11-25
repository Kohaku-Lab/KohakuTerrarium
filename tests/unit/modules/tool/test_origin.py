"""Unit tests for :mod:`kohakuterrarium.modules.tool.origin`."""

import dataclasses

import pytest

from kohakuterrarium.modules.tool import ToolOrigin as ExportedToolOrigin
from kohakuterrarium.modules.tool.origin import (
    BUILTIN_ORIGIN,
    RUNTIME_ORIGIN,
    TOOL_ORIGIN_KINDS,
    ToolOrigin,
)


class TestLabel:
    @pytest.mark.parametrize(
        ("origin", "label"),
        [
            (ToolOrigin("builtin"), "builtin"),
            (ToolOrigin("runtime"), "runtime"),
            (ToolOrigin("package", "kt-biome"), "package:kt-biome"),
            (ToolOrigin("plugin", "ui.apps"), "plugin:ui.apps"),
            (ToolOrigin("mcp", "sandbox-lab"), "mcp:sandbox-lab"),
            (ToolOrigin("custom", "my-agent"), "custom:my-agent"),
        ],
    )
    def test_label_is_kind_or_kind_colon_name(self, origin, label):
        assert origin.label == label

    def test_to_dict_carries_kind_name_and_label(self):
        assert ToolOrigin("mcp", "sandbox-lab").to_dict() == {
            "kind": "mcp",
            "name": "sandbox-lab",
            "label": "mcp:sandbox-lab",
        }
        assert BUILTIN_ORIGIN.to_dict() == {
            "kind": "builtin",
            "name": "",
            "label": "builtin",
        }


class TestValidation:
    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown tool origin kind 'vendor'"):
            ToolOrigin("vendor", "x")

    def test_every_documented_kind_is_accepted(self):
        assert {ToolOrigin(kind).kind for kind in TOOL_ORIGIN_KINDS} == set(
            TOOL_ORIGIN_KINDS
        )

    def test_origin_is_frozen_and_hashable(self):
        origin = ToolOrigin("plugin", "ui.apps")
        with pytest.raises(dataclasses.FrozenInstanceError):
            origin.name = "other"
        assert {origin, ToolOrigin("plugin", "ui.apps")} == {origin}


def test_constants_and_package_export():
    assert BUILTIN_ORIGIN == ToolOrigin("builtin")
    assert RUNTIME_ORIGIN == ToolOrigin("runtime")
    assert ExportedToolOrigin is ToolOrigin
