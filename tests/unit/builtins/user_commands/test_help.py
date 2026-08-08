from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.modules.user_command.base import UserCommandContext


async def test_model_picker_shortcuts_are_frontend_specific():
    command = get_builtin_user_command("help")
    result = await command.execute(
        "",
        UserCommandContext(extra={"command_registry": {}}),
    )

    rich_section, tui_section = result.output.split("TUI model picker", 1)

    assert "Rich CLI model picker" in rich_section
    assert "Left / Right" in rich_section
    assert "Tab / Shift+Tab" in rich_section
    assert "Enter              Apply selected preset + variations" in rich_section
    assert "Ctrl+S             Apply selected preset + variations" in tui_section
    assert "Left / Right" not in tui_section
    assert "Tab / Shift+Tab" not in tui_section
