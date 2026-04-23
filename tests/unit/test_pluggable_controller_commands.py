"""Tests for pluggable ``##xxx##`` controller commands (cluster C.1).

Covers register_command's collision/override rules directly and the
end-to-end controller path via a fake LLM script.
"""

import logging

import pytest

from kohakuterrarium.commands.base import BaseCommand, CommandResult
from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.controller_plugins import (
    BUILTIN_COMMANDS,
    register_controller_command,
)


class _DummyCommand(BaseCommand):
    """Minimal BaseCommand returning a fixed content string."""

    def __init__(self, content: str):
        self._content = content

    @property
    def command_name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "Dummy command for tests"

    async def _execute(self, args: str, context) -> CommandResult:
        return CommandResult(content=f"{self._content}:{args.strip()}")


class _NullLLM:
    """Minimal LLM that never runs — we only touch the command registry."""

    model = "test/model"

    async def chat(self, *args, **kwargs):
        if False:
            yield ""
        return


def _make_controller() -> Controller:
    # The controller construction pipeline needs an LLM but nothing else
    # for this test; we only exercise register_command.
    return Controller(_NullLLM(), ControllerConfig())


# ── Tests ────────────────────────────────────────────────────────────


def test_builtins_are_registered():
    ctrl = _make_controller()
    for name in BUILTIN_COMMANDS:
        assert name in ctrl._commands


def test_register_custom_command_succeeds():
    ctrl = _make_controller()
    cmd = _DummyCommand("recall")
    ctrl.register_command("recall", cmd)
    assert ctrl._commands["recall"] is cmd


def test_duplicate_without_override_raises():
    ctrl = _make_controller()
    ctrl.register_command("recall", _DummyCommand("a"))
    with pytest.raises(ValueError, match="Duplicate command 'recall'"):
        ctrl.register_command("recall", _DummyCommand("b"))


def test_duplicate_with_override_succeeds_and_logs_warning(caplog):
    ctrl = _make_controller()
    first = _DummyCommand("a")
    second = _DummyCommand("b")
    ctrl.register_command("recall", first)
    # Temporarily re-enable propagation so pytest's caplog can see the
    # framework's structured warning (kohakuterrarium's root logger
    # sets propagate=False by default).
    kt_root = logging.getLogger("kohakuterrarium")
    original_propagate = kt_root.propagate
    kt_root.propagate = True
    try:
        with caplog.at_level(
            logging.WARNING, logger="kohakuterrarium.core.controller_plugins"
        ):
            ctrl.register_command("recall", second, override=True)
    finally:
        kt_root.propagate = original_propagate
    assert ctrl._commands["recall"] is second
    assert any("overridden" in (r.getMessage() or "").lower() for r in caplog.records)


def test_builtin_without_override_raises_with_helpful_message():
    ctrl = _make_controller()
    with pytest.raises(ValueError, match="built-in controller command"):
        ctrl.register_command("info", _DummyCommand("fake"))


def test_builtin_with_override_succeeds():
    ctrl = _make_controller()
    custom_info = _DummyCommand("custom_info")
    ctrl.register_command("info", custom_info, override=True)
    assert ctrl._commands["info"] is custom_info


@pytest.mark.asyncio
async def test_custom_command_handler_runs_via_dispatch():
    """End-to-end: a registered command invoked via ``_handle_command``."""
    from kohakuterrarium.parsing import CommandEvent

    ctrl = _make_controller()
    register_controller_command(ctrl, "recall", _DummyCommand("memory"))
    result = await ctrl._handle_command(CommandEvent(command="recall", args="foo bar"))
    assert result.error is None
    assert result.content == "memory:foo bar"


def test_every_builtin_name_protected():
    """Each built-in name raises without override=True."""
    ctrl = _make_controller()
    for name in BUILTIN_COMMANDS:
        with pytest.raises(ValueError, match="built-in controller command"):
            ctrl.register_command(name, _DummyCommand("x"))


# ── Package-manifest command registration (A.3 consumer wiring) ───────


def test_register_package_commands_loads_entries(monkeypatch):
    """``_register_package_commands`` imports and registers every
    ``commands:`` entry declared by an installed package."""
    import sys
    import types

    from kohakuterrarium.core import controller_plugins as cp

    fake_module = types.ModuleType("_pkg_cmd_test_module")
    fake_module.FakeRecall = type(
        "FakeRecall",
        (_DummyCommand,),
        {"__init__": lambda self: _DummyCommand.__init__(self, "pkg-recall")},
    )
    sys.modules["_pkg_cmd_test_module"] = fake_module

    monkeypatch.setattr(
        cp,
        "list_packages",
        lambda: [
            {
                "name": "demo-pack",
                "commands": [
                    {
                        "name": "pkg-recall",
                        "module": "_pkg_cmd_test_module",
                        "class": "FakeRecall",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(cp, "ensure_package_importable", lambda _n: None)

    ctrl = _make_controller()
    cp._register_package_commands(ctrl)
    assert "pkg-recall" in ctrl._commands


def test_register_package_commands_collision_raises(monkeypatch):
    """Two packages declaring the same command name → hard error."""
    from kohakuterrarium.core import controller_plugins as cp

    monkeypatch.setattr(
        cp,
        "list_packages",
        lambda: [
            {
                "name": "pack-a",
                "commands": [
                    {"name": "dup", "module": "m", "class": "C"},
                ],
            },
            {
                "name": "pack-b",
                "commands": [
                    {"name": "dup", "module": "m", "class": "C"},
                ],
            },
        ],
    )
    ctrl = _make_controller()
    with pytest.raises(ValueError, match="Collision for command name 'dup'"):
        cp._register_package_commands(ctrl)


def test_register_package_commands_missing_fields_skipped(monkeypatch, caplog):
    """Entries without ``module``/``class`` are logged and skipped, not fatal."""
    from kohakuterrarium.core import controller_plugins as cp

    monkeypatch.setattr(
        cp,
        "list_packages",
        lambda: [
            {
                "name": "bad-pack",
                "commands": [
                    {"name": "brokencmd"},  # missing module + class
                ],
            }
        ],
    )
    monkeypatch.setattr(cp, "ensure_package_importable", lambda _n: None)

    ctrl = _make_controller()
    cp._register_package_commands(ctrl)  # must not raise
    assert "brokencmd" not in ctrl._commands
