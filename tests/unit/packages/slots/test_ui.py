"""Unit tests for ``packages/slots/ui.py`` — the ``ui:`` manifest slot."""

import dataclasses
import os
from pathlib import Path

import pytest
import yaml

try:
    import _winapi
except ImportError:
    _winapi = None

from kohakuterrarium.packages import locations
from kohakuterrarium.packages.slots import ui as ui_slot
from kohakuterrarium.packages.install import install_package, uninstall_package
from kohakuterrarium.packages.slots.ui import (
    DEFAULT_SDK_RANGE,
    get_package_ui,
    invalidate_ui_cache,
    is_valid_sdk_range,
    list_package_ui,
    parse_ui_slot,
    resolve_ui_asset,
)


def _link_dir(link: Path, target: Path) -> None:
    """Make ``link`` an escaping directory link: a symlink, else a Windows junction."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if _winapi is None:
            pytest.skip("no directory link can be created here")
        _winapi.CreateJunction(str(target), str(link))


def _record(root: Path, ui, name: str = "pkg", **slots) -> dict:
    record = {
        "name": name,
        "version": "1.2.3",
        "path": str(root),
        "ui": ui,
        "plugins": [{"name": "qq_feed"}, {"name": "x"}],
        "tools": [{"name": "qq_send"}, {"name": "x"}],
        "triggers": [{"name": "qq"}],
    }
    record.update(slots)
    return record


def _pkg(tmp_path: Path, name: str = "pkg", files=("ui/index.js",)) -> Path:
    root = tmp_path / name
    for rel in files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export default () => {}\n", encoding="utf-8")
    (root / "ui").mkdir(parents=True, exist_ok=True)
    return root


def _slot(tmp_path: Path, ui: dict, name: str = "pkg"):
    return parse_ui_slot(_record(_pkg(tmp_path, name), ui, name))


class TestParse:
    def test_valid_block_normalizes_paths_and_owners(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "styles": ["ui/style.css"],
                "handler": "pkg_mod.ui:Handler",
                "api": "pkg_mod.api:router",
                "i18n": {"en": "ui/en.json"},
                "contributes": [
                    {"kind": "settings", "id": "bot", "label": "Bot"},
                    {"kind": "panel", "id": "chats", "owner": "plugin:qq_feed"},
                    {"kind": "tool-renderer", "id": "send", "owner": "tool:qq_send"},
                    {"kind": "trigger-renderer", "id": "f", "owner": "trigger:qq"},
                    {"kind": "tab", "id": "t", "owner": "package"},
                    {
                        "kind": "widget",
                        "id": "board",
                        "owner": "tool:qq_send",
                        "renderer": "pkg.board",
                    },
                ],
            },
        )
        assert slot.error == ""
        assert slot.entry == "index.js"
        assert slot.styles == ("style.css",)
        assert slot.i18n == {"en": "en.json"}
        assert slot.sdk == DEFAULT_SDK_RANGE
        owners = {c.id: c.owner for c in slot.contributions}
        assert owners == {
            "bot": "package:pkg",
            "chats": "plugin:qq_feed",
            "send": "tool:qq_send",
            "f": "trigger:qq",
            "t": "package:pkg",
            "board": "tool:qq_send",
        }
        record = slot.to_dict()
        assert record["contributes"][-1]["renderer"] == "pkg.board"
        assert record["contributes"][0]["renderer"] == ""
        assert record["has_api"] is True and record["has_handler"] is True
        assert record["contributes"][1]["owner"] == "plugin:qq_feed"

    def test_overlay_options_default_and_override(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {"kind": "overlay", "id": "pet"},
                    {
                        "kind": "overlay",
                        "id": "hud",
                        "anchor": "top-left",
                        "width": 100,
                        "height": 50,
                        "draggable": False,
                        "resizable": True,
                        "clickThrough": True,
                        "defaultVisible": False,
                        "surface": "window",
                        "alwaysOnTop": False,
                    },
                    {"kind": "tab", "id": "t"},
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c["overlay"] for c in slot.to_dict()["contributes"]}
        assert by_id["pet"] == {
            "anchor": "bottom-right",
            "surface": "app",
            "width": 320,
            "height": 240,
            "draggable": True,
            "resizable": False,
            "clickThrough": False,
            "defaultVisible": True,
            "alwaysOnTop": True,
        }
        assert by_id["hud"] == {
            "anchor": "top-left",
            "surface": "window",
            "width": 100,
            "height": 50,
            "draggable": False,
            "resizable": True,
            "clickThrough": True,
            "defaultVisible": False,
            "alwaysOnTop": False,
        }
        assert by_id["t"] == {}

    def test_command_options(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {
                        "kind": "command",
                        "id": "pet-toggle",
                        "label": "Toggle pet",
                        "shortcut": "Ctrl+Alt+P",
                        "keywords": "cat desk",
                    },
                    {"kind": "command", "id": "plain"},
                    {"kind": "tab", "id": "t"},
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c for c in slot.to_dict()["contributes"]}
        assert by_id["pet-toggle"]["shortcut"] == "Ctrl+Alt+P"
        assert by_id["pet-toggle"]["keywords"] == "cat desk"
        assert by_id["pet-toggle"]["owner"] == "package:pkg"
        assert by_id["plain"]["shortcut"] == "" and by_id["plain"]["keywords"] == ""
        assert by_id["t"]["shortcut"] == ""

    def test_status_item_options(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {"kind": "status-item", "id": "plain"},
                    {
                        "kind": "status-item",
                        "id": "git",
                        "side": "left",
                        "maxWidth": 80,
                        "order": 5,
                    },
                    {"kind": "tab", "id": "t"},
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c for c in slot.to_dict()["contributes"]}
        assert by_id["plain"]["status"] == {"side": "right", "maxWidth": 160}
        assert by_id["git"]["status"] == {"side": "left", "maxWidth": 80}
        assert by_id["git"]["order"] == 5
        assert by_id["t"]["status"] == {}

    def test_message_renderer_declares_its_fence_id(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {
                        "kind": "message-renderer",
                        "id": "poll",
                        "renderer": "pkg.poll",
                    },
                    {
                        "kind": "message-renderer",
                        "id": "tool-poll",
                        "renderer": "pkg.tool-poll",
                        "owner": "plugin:qq_feed",
                    },
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c for c in slot.to_dict()["contributes"]}
        assert by_id["poll"]["renderer"] == "pkg.poll"
        assert by_id["poll"]["owner"] == "package:pkg"
        assert by_id["tool-poll"]["owner"] == "plugin:qq_feed"

    @pytest.mark.parametrize(
        "contribution, message",
        [
            ({"kind": "message-renderer", "id": "p"}, "renderer must be a dotted id"),
            (
                {"kind": "message-renderer", "id": "p", "renderer": "json"},
                "dotted id",
            ),
            (
                {"kind": "message-renderer", "id": "p", "renderer": "kt.poll"},
                "reserved prefix",
            ),
            (
                {"kind": "message-renderer", "id": "p", "renderer": "mcp.poll"},
                "reserved prefix",
            ),
        ],
    )
    def test_bad_message_renderer_invalidates_the_package(
        self, tmp_path, contribution, message
    ):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error

    def test_tray_item_and_notification_channel_are_package_owned(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {"kind": "tray-item", "id": "pause", "label": "Pause bot"},
                    {"kind": "notification-channel", "id": "hook"},
                ],
            },
        )
        assert slot.error == ""
        kinds = {
            c["id"]: (c["kind"], c["owner"]) for c in slot.to_dict()["contributes"]
        }
        assert kinds == {
            "pause": ("tray-item", "package:pkg"),
            "hook": ("notification-channel", "package:pkg"),
        }

    @pytest.mark.parametrize("kind", ["tray-item", "notification-channel"])
    def test_these_kinds_refuse_a_runtime_owner_and_stray_options(self, tmp_path, kind):
        owned = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [{"kind": kind, "id": "x", "owner": "plugin:qq_feed"}],
            },
        )
        assert "owned by" in owned.error
        stray = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [{"kind": kind, "id": "x", "patterns": ["*.c"]}],
            },
            name="other",
        )
        assert "only valid on a file-view" in stray.error

    def test_file_view_patterns(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {
                        "kind": "file-view",
                        "id": "csv",
                        "patterns": ["*.csv", "*.tar.gz"],
                    },
                    {
                        "kind": "file-view",
                        "id": "mk",
                        "patterns": ["Makefile"],
                        "defaultView": True,
                    },
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c["fileView"] for c in slot.to_dict()["contributes"]}
        assert by_id["csv"] == {"patterns": ["*.csv", "*.tar.gz"], "defaultView": False}
        assert by_id["mk"] == {"patterns": ["Makefile"], "defaultView": True}

    @pytest.mark.parametrize(
        "contribution, message",
        [
            ({"kind": "file-view", "id": "v"}, "patterns must be"),
            ({"kind": "file-view", "id": "v", "patterns": []}, "patterns must be"),
            ({"kind": "file-view", "id": "v", "patterns": "*.csv"}, "patterns must be"),
            ({"kind": "file-view", "id": "v", "patterns": ["*"]}, "patterns must be"),
            (
                {"kind": "file-view", "id": "v", "patterns": ["a/b.csv"]},
                "patterns must",
            ),
            (
                {"kind": "file-view", "id": "v", "patterns": ["**/*.csv"]},
                "patterns must",
            ),
            ({"kind": "file-view", "id": "v", "patterns": ["*.c", "*.c"]}, "distinct"),
            (
                {"kind": "file-view", "id": "v", "patterns": ["*.c"], "defaultView": 1},
                "defaultView must",
            ),
            (
                {
                    "kind": "file-view",
                    "id": "v",
                    "patterns": ["*.c"],
                    "owner": "plugin:x",
                },
                "owned by",
            ),
            (
                {"kind": "tab", "id": "t", "patterns": ["*.c"]},
                "only valid on a file-view",
            ),
            (
                {"kind": "tab", "id": "t", "defaultView": True},
                "only valid on a file-view",
            ),
        ],
    )
    def test_bad_file_view_invalidates_the_package(
        self, tmp_path, contribution, message
    ):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error

    def test_graph_badge_and_panel_node_kinds(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {"kind": "graph-badge", "id": "all"},
                    {"kind": "graph-badge", "id": "c", "nodes": ["creature"]},
                    {"kind": "graph-panel", "id": "p", "nodes": ["channel", "group"]},
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c["graph"] for c in slot.to_dict()["contributes"]}
        assert by_id["all"] == {"nodes": ["creature", "channel", "group"]}
        assert by_id["c"] == {"nodes": ["creature"]}
        assert by_id["p"] == {"nodes": ["channel", "group"]}

    @pytest.mark.parametrize(
        "contribution, message",
        [
            ({"kind": "graph-badge", "id": "g", "nodes": ["edge"]}, "nodes must"),
            ({"kind": "graph-badge", "id": "g", "nodes": []}, "nodes must"),
            ({"kind": "graph-panel", "id": "g", "owner": "plugin:x"}, "owned by"),
            (
                {"kind": "tab", "id": "t", "nodes": ["creature"]},
                "only valid on a graph",
            ),
        ],
    )
    def test_bad_graph_contribution_invalidates_the_package(
        self, tmp_path, contribution, message
    ):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error

    def test_panel_dock_flag(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {"kind": "panel", "id": "side", "owner": "plugin:qq_feed"},
                    {
                        "kind": "panel",
                        "id": "docked",
                        "owner": "plugin:qq_feed",
                        "dock": True,
                    },
                    {"kind": "tab", "id": "t"},
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c["dock"] for c in slot.to_dict()["contributes"]}
        assert by_id == {"side": False, "docked": True, "t": False}

    @pytest.mark.parametrize(
        "contribution, message",
        [
            (
                {"kind": "panel", "id": "p", "owner": "plugin:qq_feed", "dock": "yes"},
                "dock must be true or false",
            ),
            ({"kind": "tab", "id": "t", "dock": True}, "only valid on a panel"),
        ],
    )
    def test_bad_dock_flag_invalidates_the_package(
        self, tmp_path, contribution, message
    ):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error

    def test_message_decorator_options(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {"kind": "message-decorator", "id": "plain"},
                    {
                        "kind": "message-decorator",
                        "id": "cost",
                        "position": "header",
                        "maxHeight": 40,
                        "roles": ["assistant", "tool"],
                    },
                    {"kind": "tab", "id": "t"},
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c["decorator"] for c in slot.to_dict()["contributes"]}
        assert by_id["plain"] == {
            "position": "footer",
            "maxHeight": 120,
            "roles": ["assistant"],
        }
        assert by_id["cost"] == {
            "position": "header",
            "maxHeight": 40,
            "roles": ["assistant", "tool"],
        }
        assert by_id["t"] == {}

    @pytest.mark.parametrize(
        "contribution, message",
        [
            ({"kind": "message-decorator", "id": "d", "owner": "plugin:x"}, "owned by"),
            ({"kind": "message-decorator", "id": "d", "position": "side"}, "position"),
            ({"kind": "message-decorator", "id": "d", "maxHeight": 4}, "maxHeight"),
            ({"kind": "message-decorator", "id": "d", "maxHeight": 9000}, "maxHeight"),
            (
                {"kind": "message-decorator", "id": "d", "roles": ["robot"]},
                "roles must",
            ),
            ({"kind": "message-decorator", "id": "d", "roles": []}, "roles must"),
            (
                {"kind": "message-decorator", "id": "d", "targets": ["tab"]},
                "only valid",
            ),
            ({"kind": "tab", "id": "t", "position": "header"}, "only valid on a"),
            ({"kind": "tab", "id": "t", "roles": ["user"]}, "only valid on an action"),
        ],
    )
    def test_bad_message_decorator_invalidates_the_package(
        self, tmp_path, contribution, message
    ):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error

    def test_action_targets_and_roles(self, tmp_path):
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [
                    {"kind": "action", "id": "a", "targets": ["message", "tab"]},
                    {
                        "kind": "action",
                        "id": "b",
                        "targets": ["message"],
                        "roles": ["user", "tool"],
                    },
                    {"kind": "action", "id": "c", "targets": ["composer", "file"]},
                    {"kind": "tab", "id": "t"},
                ],
            },
        )
        assert slot.error == ""
        by_id = {c["id"]: c["action"] for c in slot.to_dict()["contributes"]}
        assert by_id["a"] == {"targets": ["message", "tab"], "roles": ["assistant"]}
        assert by_id["b"] == {"targets": ["message"], "roles": ["user", "tool"]}
        assert by_id["c"] == {"targets": ["composer", "file"]}
        assert by_id["t"] == {}

    @pytest.mark.parametrize(
        "contribution, message",
        [
            ({"kind": "action", "id": "a"}, "targets must be a non-empty list"),
            ({"kind": "action", "id": "a", "targets": []}, "targets must be"),
            ({"kind": "action", "id": "a", "targets": "tab"}, "targets must be"),
            ({"kind": "action", "id": "a", "targets": ["sky"]}, "distinct values"),
            ({"kind": "action", "id": "a", "targets": ["tab", "tab"]}, "distinct"),
            (
                {"kind": "action", "id": "a", "targets": ["tab"], "roles": ["user"]},
                "needs the message target",
            ),
            (
                {
                    "kind": "action",
                    "id": "a",
                    "targets": ["message"],
                    "roles": ["robot"],
                },
                "roles must hold",
            ),
            (
                {"kind": "action", "id": "a", "targets": ["tab"], "owner": "plugin:x"},
                "owned by",
            ),
            ({"kind": "tab", "id": "t", "targets": ["tab"]}, "only valid on an action"),
        ],
    )
    def test_bad_action_invalidates_the_package(self, tmp_path, contribution, message):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error

    @pytest.mark.parametrize(
        "contribution, message",
        [
            ({"kind": "status-item", "id": "s", "owner": "plugin:x"}, "owned by"),
            ({"kind": "status-item", "id": "s", "side": "middle"}, "side"),
            ({"kind": "status-item", "id": "s", "maxWidth": 8}, "maxWidth"),
            ({"kind": "status-item", "id": "s", "maxWidth": 4000}, "maxWidth"),
            ({"kind": "status-item", "id": "s", "maxWidth": True}, "maxWidth"),
            ({"kind": "tab", "id": "t", "side": "left"}, "only valid on a status-item"),
        ],
    )
    def test_bad_status_item_invalidates_the_package(
        self, tmp_path, contribution, message
    ):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error

    @pytest.mark.parametrize(
        "contribution, message",
        [
            ({"kind": "panel", "id": "p"}, "owner is required"),
            ({"kind": "panel", "id": "p", "owner": "package"}, "owner is required"),
            ({"kind": "settings", "id": "s", "owner": "plugin:x"}, "must be owned by"),
            ({"kind": "tool-renderer", "id": "r", "owner": "plugin:x"}, "owned by"),
            ({"kind": "settings", "id": "s", "owner": "package:other"}, "another"),
            ({"kind": "panel", "id": "p", "owner": "gadget:x"}, "<kind>:<name>"),
            ({"kind": "bogus", "id": "x"}, "kind must be one of"),
            ({"kind": "tab", "id": "Bad Id"}, "id must match"),
            ({"kind": "tab", "id": "t", "isolation": "shadow"}, "isolation"),
            ({"kind": "tab", "id": "t", "minDensity": "huge"}, "minDensity"),
            ({"kind": "tab", "id": "t", "order": "1"}, "order"),
            ({"kind": "widget", "id": "w"}, "renderer must be a dotted id"),
            ({"kind": "widget", "id": "w", "renderer": "board"}, "dotted id"),
            ({"kind": "widget", "id": "w", "renderer": "kt.app"}, "reserved prefix"),
            ({"kind": "widget", "id": "w", "renderer": "mcp.app"}, "reserved prefix"),
            ({"kind": "tab", "id": "t", "renderer": "pkg.x"}, "only valid on a widget"),
            ({"kind": "overlay", "id": "o", "owner": "plugin:x"}, "owned by"),
            ({"kind": "overlay", "id": "o", "anchor": "middle"}, "anchor"),
            ({"kind": "command", "id": "c", "owner": "plugin:x"}, "owned by"),
            (
                {"kind": "command", "id": "c", "shortcut": "P"},
                "shortcut must look like",
            ),
            (
                {"kind": "command", "id": "c", "shortcut": "Shift+P"},
                "Ctrl, Alt or Meta",
            ),
            (
                {"kind": "command", "id": "c", "shortcut": "Ctrl+Ctrl+P"},
                "shortcut must",
            ),
            ({"kind": "command", "id": "c", "shortcut": "Ctrl+Enter"}, "shortcut must"),
            ({"kind": "command", "id": "c", "shortcut": 5}, "shortcut must"),
            ({"kind": "command", "id": "c", "keywords": ["a"]}, "keywords must"),
            (
                {"kind": "tab", "id": "t", "shortcut": "Ctrl+P"},
                "only valid on a command",
            ),
            ({"kind": "tab", "id": "t", "keywords": "x"}, "only valid on a command"),
            ({"kind": "overlay", "id": "o", "surface": "tab"}, "surface"),
            ({"kind": "overlay", "id": "o", "alwaysOnTop": 1}, "alwaysOnTop"),
            ({"kind": "overlay", "id": "o", "width": 0}, "width"),
            ({"kind": "overlay", "id": "o", "height": True}, "height"),
            ({"kind": "overlay", "id": "o", "draggable": "yes"}, "draggable"),
            (
                {"kind": "tab", "id": "t", "anchor": "center"},
                "only valid on an overlay",
            ),
        ],
    )
    def test_bad_contribution_invalidates_only_this_package(
        self, tmp_path, contribution, message
    ):
        slot = _slot(tmp_path, {"entry": "ui/index.js", "contributes": [contribution]})
        assert message in slot.error
        assert slot.contributions == ()

    @pytest.mark.parametrize(
        "ui, message",
        [
            ({"entry": "../evil.js"}, "without '..'"),
            ({"entry": "/abs/index.js"}, "without '..'"),
            ({"entry": "src/index.js"}, "under the UI root"),
            ({"root": "missing"}, "does not exist"),
            ({"sdk": "latest"}, "not a valid version range"),
            ({"api": "no-colon"}, "dotted.module:attribute"),
            ({"handler": "a.b"}, "dotted.module:attribute"),
            ({"styles": "ui/a.css"}, "list of paths"),
            ({"i18n": ["en"]}, "locale -> path"),
            ({"contributes": [{"kind": "tab", "id": "t"}]}, "entry is required"),
            (
                {
                    "entry": "ui/index.js",
                    "contributes": [
                        {"kind": "tab", "id": "t"},
                        {"kind": "rail", "id": "t"},
                    ],
                },
                "declared twice",
            ),
        ],
    )
    def test_block_errors(self, tmp_path, ui, message):
        assert message in _slot(tmp_path, ui).error

    def test_frame_only_package_needs_no_entry(self, tmp_path):
        slot = _slot(
            tmp_path,
            {"contributes": [{"kind": "tab", "id": "t", "isolation": "frame"}]},
        )
        assert slot.error == ""
        assert slot.contributions[0].isolation == "frame"

    def test_absent_and_non_mapping_blocks(self, tmp_path):
        root = _pkg(tmp_path)
        assert parse_ui_slot(_record(root, {})) is None
        assert parse_ui_slot(_record(root, None)) is None
        assert "mapping" in parse_ui_slot(_record(root, ["x"])).error

    def test_sdk_range_grammar(self):
        assert is_valid_sdk_range(">=1,<2")
        assert is_valid_sdk_range("^1.2")
        assert is_valid_sdk_range("1.0.0")
        assert not is_valid_sdk_range(">=1,")
        assert not is_valid_sdk_range("v1")


class TestAssets:
    def test_resolve_stays_inside_ui_root(self, tmp_path):
        root = _pkg(tmp_path, files=("ui/index.js", "ui/sub/a.js", "secret.py"))
        slot = parse_ui_slot(_record(root, {"entry": "ui/index.js"}))
        assert resolve_ui_asset(slot, "sub/a.js") == (root / "ui/sub/a.js").resolve()
        assert resolve_ui_asset(slot, "../secret.py") is None
        assert resolve_ui_asset(slot, "/etc/passwd") is None
        assert resolve_ui_asset(slot, "missing.js") is None
        assert resolve_ui_asset(slot, "") is None

    def test_symlink_out_of_root_is_refused(self, tmp_path):
        root = _pkg(tmp_path, files=("ui/index.js",))
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("secret")
        _link_dir(root / "ui" / "escape", outside)
        slot = parse_ui_slot(_record(root, {"entry": "ui/index.js"}))
        assert (root / "ui" / "escape" / "secret.py").is_file()
        assert resolve_ui_asset(slot, "escape/secret.py") is None

    def test_invalid_slot_serves_nothing(self, tmp_path):
        root = _pkg(tmp_path)
        slot = parse_ui_slot(_record(root, {"entry": "../x.js"}))
        assert resolve_ui_asset(slot, "index.js") is None


class TestListing:
    def _install(self, packages: Path, name: str, ui: dict) -> None:
        root = packages / name
        (root / "ui").mkdir(parents=True)
        (root / "ui" / "index.js").write_text("export default () => {}\n")
        (root / "kohaku.yaml").write_text(
            yaml.safe_dump({"name": name, "version": "0.1", "ui": ui})
        )

    def test_lists_installed_ui_and_refreshes_on_manifest_change(
        self, tmp_path, monkeypatch
    ):
        packages = tmp_path / "pkgs"
        packages.mkdir()
        monkeypatch.setattr(locations, "PACKAGES_DIR", packages)
        invalidate_ui_cache()
        self._install(packages, "alpha", {"entry": "ui/index.js"})
        self._install(packages, "beta", {"entry": "../bad.js"})
        (packages / "plain").mkdir()
        (packages / "plain" / "kohaku.yaml").write_text("name: plain\n")

        slots = {s.package: s for s in list_package_ui()}
        assert set(slots) == {"alpha", "beta"}
        assert slots["alpha"].error == "" and slots["beta"].error
        first = get_package_ui("alpha")
        assert get_package_ui("alpha") is first
        assert get_package_ui("plain") is None

        manifest = packages / "alpha" / "kohaku.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "name": "alpha",
                    "ui": {
                        "entry": "ui/index.js",
                        "contributes": [{"kind": "tab", "id": "t"}],
                    },
                }
            )
        )
        stat = manifest.stat()
        os.utime(manifest, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
        # Edits are seen once the cache window ends (or on explicit invalidation).
        invalidate_ui_cache()
        refreshed = get_package_ui("alpha")
        assert refreshed is not first
        assert [c.id for c in refreshed.contributions] == ["t"]
        assert refreshed.fingerprint != first.fingerprint
        assert get_package_ui("beta").fingerprint == slots["beta"].fingerprint

    def test_scan_is_cached_within_the_window_and_install_invalidates(
        self, tmp_path, monkeypatch
    ):
        packages = tmp_path / "pkgs"
        packages.mkdir()
        monkeypatch.setattr(locations, "PACKAGES_DIR", packages)
        invalidate_ui_cache()
        calls = []
        real = ui_slot.packages_fingerprint
        monkeypatch.setattr(
            ui_slot, "packages_fingerprint", lambda: calls.append(1) or real()
        )
        clock = [100.0]
        monkeypatch.setattr(ui_slot.time, "monotonic", lambda: clock[0])
        list_package_ui()
        list_package_ui()
        assert len(calls) == 1
        clock[0] += ui_slot.SCAN_WINDOW_SECONDS + 0.1
        list_package_ui()
        assert len(calls) == 2

        src = tmp_path / "src" / "gamma"
        (src / "ui").mkdir(parents=True)
        (src / "ui" / "index.js").write_text("export default () => {}\n")
        (src / "kohaku.yaml").write_text(
            yaml.safe_dump({"name": "gamma", "ui": {"entry": "ui/index.js"}})
        )
        install_package(str(src))
        assert get_package_ui("gamma") is not None
        uninstall_package("gamma")
        assert get_package_ui("gamma") is None


class TestSafety:
    def test_ui_root_symlinked_outside_the_package_is_refused(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "index.js").write_text("export default () => {}\n")
        (outside / "passwd").write_text("root:x")
        root = tmp_path / "pkg"
        root.mkdir()
        _link_dir(root / "ui", outside)
        slot = parse_ui_slot(_record(root, {"entry": "ui/index.js"}))
        assert "outside the package" in slot.error
        assert resolve_ui_asset(slot, "index.js") is None
        # Even a slot built by hand cannot serve from a root outside the package.
        forged = dataclasses.replace(slot, error="")
        assert resolve_ui_asset(forged, "passwd") is None

    @pytest.mark.parametrize(
        "owner",
        ["plugin:undeclared", "tool:undeclared", "trigger:undeclared"],
    )
    def test_runtime_owner_must_be_declared_by_the_package(self, tmp_path, owner):
        kind = {
            "plugin": "panel",
            "tool": "tool-renderer",
            "trigger": "trigger-renderer",
        }[owner.split(":")[0]]
        slot = _slot(
            tmp_path,
            {
                "entry": "ui/index.js",
                "contributes": [{"kind": kind, "id": "c", "owner": owner}],
            },
        )
        assert "does not declare" in slot.error

    def test_declared_owner_matches_manifest_slot_entries(self, tmp_path):
        root = _pkg(tmp_path)
        ui = {
            "entry": "ui/index.js",
            "contributes": [{"kind": "panel", "id": "p", "owner": "plugin:mine"}],
        }
        ok = parse_ui_slot(_record(root, ui, plugins=["mine"]))
        assert ok.error == ""
        bad = parse_ui_slot(_record(root, ui, plugins=[{"name": "other"}]))
        assert "does not declare plugin 'mine'" in bad.error
