"""Unit tests for :mod:`kohakuterrarium.modules.user_command.base`.

Behavior-first: slash-command parsing, UI payload constructors,
BaseUserCommand error wrapping, UserCommandResult.success semantics.
"""

from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommand,
    UserCommandContext,
    UserCommandResult,
    parse_slash_command,
    ui_confirm,
    ui_info_panel,
    ui_list,
    ui_notify,
    ui_select,
    ui_text,
)


class TestParseSlashCommand:
    def test_command_with_args(self):
        assert parse_slash_command("/model claude-opus-4.6") == (
            "model",
            "claude-opus-4.6",
        )

    def test_command_without_args(self):
        assert parse_slash_command("/help") == ("help", "")

    def test_name_is_lowercased(self):
        assert parse_slash_command("/MODEL gpt") == ("model", "gpt")

    def test_args_keep_their_case_and_remaining_spaces(self):
        # Only the first split happens — args retain internal whitespace.
        assert parse_slash_command("/say Hello   World") == (
            "say",
            "Hello   World",
        )

    def test_bare_slash_yields_empty_name(self):
        assert parse_slash_command("/") == ("", "")

    def test_multiple_leading_slashes_stripped(self):
        assert parse_slash_command("///model x") == ("model", "x")


class TestUIConstructors:
    def test_ui_text(self):
        assert ui_text("hi") == {"type": "text", "message": "hi"}

    def test_ui_notify_default_level(self):
        assert ui_notify("done") == {
            "type": "notify",
            "message": "done",
            "level": "info",
        }

    def test_ui_notify_custom_level(self):
        assert ui_notify("oops", level="error")["level"] == "error"

    def test_ui_confirm_carries_action_and_args(self):
        payload = ui_confirm("Delete?", action="delete", action_args="id-1")
        assert payload == {
            "type": "confirm",
            "message": "Delete?",
            "action": "delete",
            "action_args": "id-1",
        }

    def test_ui_select_structure(self):
        payload = ui_select(
            "Pick a model",
            options=[{"value": "a", "label": "A"}],
            current="a",
            action="model",
        )
        assert payload["type"] == "select"
        assert payload["current"] == "a"
        assert payload["action"] == "model"
        assert payload["options"][0]["value"] == "a"

    def test_ui_info_panel_structure(self):
        payload = ui_info_panel("Status", [{"key": "Model", "value": "gpt"}])
        assert payload["type"] == "info_panel"
        assert payload["fields"][0]["key"] == "Model"

    def test_ui_list_structure(self):
        payload = ui_list("Plugins", [{"label": "budget", "description": "x"}])
        assert payload["type"] == "list"
        assert payload["items"][0]["label"] == "budget"


class TestUserCommandResult:
    def test_success_true_without_error(self):
        assert UserCommandResult(output="ok").success is True

    def test_success_false_with_error(self):
        assert UserCommandResult(error="boom").success is False

    def test_defaults_consumed_true_no_data(self):
        r = UserCommandResult()
        assert r.consumed is True
        assert r.data is None


class _OkCommand(BaseUserCommand):
    name = "echo"
    description = "echoes args"
    layer = CommandLayer.INPUT

    async def _execute(self, args, context):
        return UserCommandResult(output=f"echo: {args}")


class _RaisingCommand(BaseUserCommand):
    name = "boom"
    description = "always raises"
    layer = CommandLayer.AGENT

    async def _execute(self, args, context):
        raise RuntimeError("command crashed")


class TestBaseUserCommand:
    async def test_successful_execute_passes_through(self):
        result = await _OkCommand().execute("hello", UserCommandContext())
        assert result.output == "echo: hello"
        assert result.success is True

    async def test_exception_becomes_error_result(self):
        # Contract: BaseUserCommand.execute wraps any exception into a
        # failed UserCommandResult instead of propagating.
        result = await _RaisingCommand().execute("", UserCommandContext())
        assert result.success is False
        assert result.error == "command crashed"

    def test_aliases_default_empty(self):
        assert _OkCommand.aliases == []

    def test_concrete_command_satisfies_protocol(self):
        assert isinstance(_OkCommand(), UserCommand)
