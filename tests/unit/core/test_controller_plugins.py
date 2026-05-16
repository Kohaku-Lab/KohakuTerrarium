"""Unit tests for :mod:`kohakuterrarium.core.controller_plugins`."""

import types

import pytest

from kohakuterrarium.commands.base import BaseCommand, CommandResult
from kohakuterrarium.core import controller_plugins as cp
from kohakuterrarium.core.controller_plugins import (
    BUILTIN_COMMANDS,
    register_controller_command,
    register_plugin_and_package_commands,
    run_post_llm_call_chain,
)
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.parsing.patterns import ParserConfig

# ── stubs ────────────────────────────────────────────────────────


class _StubMessage:
    def __init__(self, text: str):
        self.content = text

    def get_text_content(self) -> str:
        return self.content


class _StubConversation:
    def __init__(self, last_text: str | None = ""):
        self._last = _StubMessage(last_text) if last_text is not None else None

    def get_last_assistant_message(self):
        return self._last


class _StubRouter:
    def __init__(self):
        self.calls: list[tuple] = []

    def notify_activity(self, kind, message, metadata=None):
        self.calls.append((kind, message, metadata))


class _StubController:
    def __init__(
        self,
        plugins=None,
        last_text="hello",
        usage=None,
        model="m",
    ):
        self.plugins = plugins
        self.conversation = _StubConversation(last_text)
        self._last_usage = usage or {}
        self.llm = types.SimpleNamespace(model=model)
        self.output_router = _StubRouter()
        self._commands: dict = {}
        self._parser_config = ParserConfig(
            known_tools=set(),
            known_subagents=set(),
            known_commands=set(),
        )

    def register_command(self, name, cmd, override=False):
        register_controller_command(self, name, cmd, override=override)


class _StubPlugin(BasePlugin):
    """Minimal plugin that overrides ``post_llm_call`` to rewrite text."""

    def __init__(self, name: str, replacement: str | None = None, raises=False):
        self.name = name
        self.replacement = replacement
        self.raises = raises

    async def post_llm_call(self, messages, text, usage, model=""):
        if self.raises:
            raise RuntimeError("nope")
        return self.replacement if self.replacement is not None else text


class _NoOpPlugin(BasePlugin):
    """Doesn't override post_llm_call — must be skipped."""

    def __init__(self, name="noop"):
        self.name = name


class _StubPluginManager:
    """Mimics PluginManager._applicable_plugins / collect_commands."""

    def __init__(self, plugins=None, commands_map=None):
        self._plugins = list(plugins or [])
        self._commands = commands_map or []

    def _applicable_plugins(self):
        return list(self._plugins)

    def collect_commands(self):
        return list(self._commands)


class _StubCmd(BaseCommand):
    @property
    def command_name(self):
        return "stub"

    @property
    def description(self):
        return "stub command"

    async def _execute(self, args, context):
        return CommandResult(content="ok")


# ── run_post_llm_call_chain ──────────────────────────────────────


class TestRunPostLLMCallChain:
    async def test_no_plugins_no_op(self):
        c = _StubController(plugins=None)
        await run_post_llm_call_chain(c, [])
        assert c.output_router.calls == []

    async def test_no_overriding_plugins(self):
        # _NoOpPlugin inherits the base method → skipped.
        mgr = _StubPluginManager(plugins=[_NoOpPlugin()])
        c = _StubController(plugins=mgr, last_text="hello")
        await run_post_llm_call_chain(c, [])
        # Unchanged.
        assert c.conversation._last.content == "hello"
        assert c.output_router.calls == []

    async def test_plugin_rewrites_emits_edit_marker(self):
        plug = _StubPlugin(name="rewriter", replacement="HELLO")
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController(plugins=mgr, last_text="hello")
        await run_post_llm_call_chain(c, [])
        # Last message rewritten.
        assert c.conversation._last.content == "HELLO"
        # Marker emitted.
        assert c.output_router.calls
        kind, msg, meta = c.output_router.calls[0]
        assert kind == "assistant_message_edited"
        assert "rewriter" in msg
        assert meta["edited_by"] == ["rewriter"]
        assert meta["final_length"] == 5

    async def test_plugin_returns_same_text_no_marker(self):
        plug = _StubPlugin(name="echo", replacement="hello")
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController(plugins=mgr, last_text="hello")
        await run_post_llm_call_chain(c, [])
        # No mutation → no marker.
        assert c.output_router.calls == []

    async def test_plugin_exception_skipped(self):
        boom = _StubPlugin(name="boom", raises=True)
        ok = _StubPlugin(name="ok", replacement="rewritten")
        mgr = _StubPluginManager(plugins=[boom, ok])
        c = _StubController(plugins=mgr, last_text="hello")
        await run_post_llm_call_chain(c, [])
        # Boom's failure swallowed; second plugin still rewrites.
        assert c.conversation._last.content == "rewritten"
        assert c.output_router.calls[0][2]["edited_by"] == ["ok"]

    async def test_plugin_returns_non_string_ignored(self):
        class _BadPlugin(BasePlugin):
            name = "bad"

            async def post_llm_call(self, messages, text, usage, model=""):
                return 42  # non-string

        mgr = _StubPluginManager(plugins=[_BadPlugin()])
        c = _StubController(plugins=mgr, last_text="hello")
        await run_post_llm_call_chain(c, [])
        assert c.conversation._last.content == "hello"

    async def test_no_last_message(self):
        plug = _StubPlugin(name="r", replacement="x")
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController(plugins=mgr, last_text=None)
        # No last message: original is "" so rewriting to "x" diverges,
        # but ``last is None`` blocks the mutation branch.
        await run_post_llm_call_chain(c, [])
        # Output router still receives nothing because the conditional
        # guard ``last is not None`` is False.
        assert c.output_router.calls == []


# ── register_controller_command ──────────────────────────────────


class TestRegisterControllerCommand:
    def test_register_succeeds(self):
        c = _StubController()
        register_controller_command(c, "myname", _StubCmd())
        assert "myname" in c._commands
        assert "myname" in c._parser_config.known_commands

    def test_builtin_rejected_without_override(self):
        c = _StubController()
        with pytest.raises(ValueError, match="built-in"):
            register_controller_command(c, next(iter(BUILTIN_COMMANDS)), _StubCmd())

    def test_builtin_with_override_succeeds(self):
        c = _StubController()
        bname = next(iter(BUILTIN_COMMANDS))
        register_controller_command(c, bname, _StubCmd(), override=True)
        assert bname in c._commands

    def test_duplicate_rejected_without_override(self):
        c = _StubController()
        register_controller_command(c, "x", _StubCmd())
        with pytest.raises(ValueError, match="Duplicate"):
            register_controller_command(c, "x", _StubCmd())

    def test_duplicate_with_override_succeeds(self):
        c = _StubController()
        register_controller_command(c, "x", _StubCmd())
        register_controller_command(c, "x", _StubCmd(), override=True)
        # Second registration replaced.
        assert "x" in c._commands

    def test_parser_config_optional(self):
        c = _StubController()
        c._parser_config = None  # no parser → silent skip
        register_controller_command(c, "n", _StubCmd())
        assert "n" in c._commands


# ── register_plugin_and_package_commands ─────────────────────────


class TestRegisterPluginAndPackageCommands:
    def test_no_controller_no_op(self):
        agent = types.SimpleNamespace(controller=None)
        register_plugin_and_package_commands(agent)  # must not raise

    def test_controller_without_register_no_op(self):
        agent = types.SimpleNamespace(controller=object())
        register_plugin_and_package_commands(agent)

    def test_registers_plugin_commands(self, monkeypatch):
        monkeypatch.setattr(cp, "list_packages", lambda: [])
        c = _StubController()

        plug = _StubPlugin(name="p")
        cmd = _StubCmd()
        mgr = _StubPluginManager(commands_map=[(plug, {"my_cmd": cmd})])
        agent = types.SimpleNamespace(controller=c, plugins=mgr)
        register_plugin_and_package_commands(agent)
        assert "my_cmd" in c._commands

    def test_plugin_command_override_flag(self, monkeypatch):
        monkeypatch.setattr(cp, "list_packages", lambda: [])
        c = _StubController()
        register_controller_command(c, "x", _StubCmd())

        plug = _StubPlugin(name="p")
        plug.command_override = True  # type: ignore[attr-defined]
        mgr = _StubPluginManager(commands_map=[(plug, {"x": _StubCmd()})])
        agent = types.SimpleNamespace(controller=c, plugins=mgr)
        register_plugin_and_package_commands(agent)  # must not raise
        assert "x" in c._commands

    def test_plugin_collision_without_override_raises(self, monkeypatch):
        monkeypatch.setattr(cp, "list_packages", lambda: [])
        c = _StubController()
        register_controller_command(c, "x", _StubCmd())

        plug = _StubPlugin(name="p")
        # No command_override attribute → defaults to False.
        mgr = _StubPluginManager(commands_map=[(plug, {"x": _StubCmd()})])
        agent = types.SimpleNamespace(controller=c, plugins=mgr)
        with pytest.raises(ValueError, match="Duplicate"):
            register_plugin_and_package_commands(agent)

    def test_package_command_loaded(self, monkeypatch):
        c = _StubController()

        called = {}

        def fake_ensure(pkg_name):
            called["pkg"] = pkg_name

        monkeypatch.setattr(cp, "ensure_package_importable", fake_ensure)

        # Build a tiny module with a Command class.
        import sys

        mod = types.ModuleType("test_pkg_cmd_mod")

        class _PkgCmd(BaseCommand):
            @property
            def command_name(self):
                return "pkgcmd"

            @property
            def description(self):
                return "pkg"

            async def _execute(self, args, context):
                return CommandResult(content="ok")

        mod._PkgCmd = _PkgCmd
        sys.modules["test_pkg_cmd_mod"] = mod

        monkeypatch.setattr(
            cp,
            "list_packages",
            lambda: [
                {
                    "name": "pkg",
                    "commands": [
                        {
                            "name": "pkgcmd",
                            "module": "test_pkg_cmd_mod",
                            "class": "_PkgCmd",
                        }
                    ],
                }
            ],
        )
        agent = types.SimpleNamespace(controller=c, plugins=None)
        register_plugin_and_package_commands(agent)
        assert "pkgcmd" in c._commands
        assert called["pkg"] == "pkg"

    def test_package_command_collision_across_packages(self, monkeypatch):
        monkeypatch.setattr(cp, "ensure_package_importable", lambda n: None)
        monkeypatch.setattr(
            cp,
            "list_packages",
            lambda: [
                {
                    "name": "a",
                    "commands": [{"name": "x", "module": "m", "class": "C"}],
                },
                {
                    "name": "b",
                    "commands": [{"name": "x", "module": "m", "class": "C"}],
                },
            ],
        )
        c = _StubController()
        agent = types.SimpleNamespace(controller=c, plugins=None)
        with pytest.raises(ValueError, match="Collision"):
            register_plugin_and_package_commands(agent)

    def test_package_command_missing_fields_skipped(self, monkeypatch):
        monkeypatch.setattr(cp, "ensure_package_importable", lambda n: None)
        monkeypatch.setattr(
            cp,
            "list_packages",
            lambda: [
                {
                    "name": "p",
                    "commands": [
                        # Missing module/class — silently skipped.
                        {"name": "x"},
                        # Missing name — skipped earlier.
                        {"module": "m", "class": "C"},
                        # Wrong type — skipped.
                        "not a dict",
                    ],
                }
            ],
        )
        c = _StubController()
        agent = types.SimpleNamespace(controller=c, plugins=None)
        register_plugin_and_package_commands(agent)  # must not raise
        assert "x" not in c._commands

    def test_package_command_import_failure_logged_and_skipped(self, monkeypatch):
        monkeypatch.setattr(cp, "ensure_package_importable", lambda n: None)
        monkeypatch.setattr(
            cp,
            "list_packages",
            lambda: [
                {
                    "name": "p",
                    "commands": [
                        {
                            "name": "broken",
                            "module": "definitely_missing_module_xyz",
                            "class": "C",
                        }
                    ],
                }
            ],
        )
        c = _StubController()
        agent = types.SimpleNamespace(controller=c, plugins=None)
        register_plugin_and_package_commands(agent)
        assert "broken" not in c._commands


class TestEditMarkerEmitFailure:
    async def test_emit_failure_swallowed(self):
        """Emit failure in ``_emit_edit_marker`` is logged but not raised."""
        plug = _StubPlugin(name="rewriter", replacement="HELLO")
        mgr = _StubPluginManager(plugins=[plug])

        # Make router.notify_activity raise.
        class _BadRouter:
            def notify_activity(self, *a, **kw):
                raise RuntimeError("boom")

        c = _StubController(plugins=mgr, last_text="hello")
        c.output_router = _BadRouter()
        # Must not raise — defensive try/except.
        await run_post_llm_call_chain(c, [])

    async def test_router_without_notify_activity_skipped(self):
        """When the router lacks ``notify_activity``, the edit marker
        emit early-returns (line 97)."""
        plug = _StubPlugin(name="rewriter", replacement="HELLO")
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController(plugins=mgr, last_text="hello")
        # Replace router with one missing notify_activity.
        c.output_router = object()
        await run_post_llm_call_chain(c, [])


class TestPackageCommandCollisionAtController:
    def test_register_raises_value_error_propagates(self, monkeypatch):
        """When ``register_command`` itself raises ValueError (e.g. against
        an already-registered command and override=False), the error
        propagates from package-command loading (lines 254-261)."""
        c = _StubController()
        register_controller_command(c, "pkgcmd", _StubCmd())

        import sys
        import types as _types

        mod = _types.ModuleType("test_collide_mod")

        class _Dup(BaseCommand):
            @property
            def command_name(self):
                return "pkgcmd"

            @property
            def description(self):
                return "dup"

            async def _execute(self, args, context):
                return CommandResult(content="x")

        mod._Dup = _Dup
        sys.modules["test_collide_mod"] = mod

        monkeypatch.setattr(cp, "ensure_package_importable", lambda n: None)
        monkeypatch.setattr(
            cp,
            "list_packages",
            lambda: [
                {
                    "name": "pkg-x",
                    "commands": [
                        {
                            "name": "pkgcmd",
                            "module": "test_collide_mod",
                            "class": "_Dup",
                        }
                    ],
                }
            ],
        )
        agent = types.SimpleNamespace(controller=c, plugins=None)
        with pytest.raises(ValueError, match="Duplicate"):
            register_plugin_and_package_commands(agent)
