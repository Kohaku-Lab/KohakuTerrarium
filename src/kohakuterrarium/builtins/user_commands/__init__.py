"""
Built-in user commands (slash commands).

Registration follows the same pattern as tool_catalog.py:
@register_user_command("name") on a class triggers registration
at import time.
"""

from kohakuterrarium.builtins.user_commands.registry import (
    get_builtin_user_command,
    list_builtin_user_commands,
    register_user_command,
)

# Import commands to trigger @register_user_command decorators.
# Command modules import the lightweight registry directly, not this package,
# so this catalog import does not create a package-level cycle.
from kohakuterrarium.builtins.user_commands.branch import BranchCommand
from kohakuterrarium.builtins.user_commands.clear import ClearCommand
from kohakuterrarium.builtins.user_commands.compact import CompactCommand
from kohakuterrarium.builtins.user_commands.edit import EditCommand
from kohakuterrarium.builtins.user_commands.exit import ExitCommand
from kohakuterrarium.builtins.user_commands.fork import ForkCommand
from kohakuterrarium.builtins.user_commands.help import HelpCommand
from kohakuterrarium.builtins.user_commands.model import ModelCommand
from kohakuterrarium.builtins.user_commands.plugin import PluginCommand
from kohakuterrarium.builtins.user_commands.regen import RegenCommand
from kohakuterrarium.builtins.user_commands.settings import SettingsCommand
from kohakuterrarium.builtins.user_commands.skill import SkillUserCommand
from kohakuterrarium.builtins.user_commands.status import StatusCommand

__all__ = [
    "register_user_command",
    "get_builtin_user_command",
    "list_builtin_user_commands",
    "BranchCommand",
    "ClearCommand",
    "CompactCommand",
    "EditCommand",
    "ExitCommand",
    "ForkCommand",
    "HelpCommand",
    "ModelCommand",
    "PluginCommand",
    "RegenCommand",
    "SettingsCommand",
    "SkillUserCommand",
    "StatusCommand",
]
