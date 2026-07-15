"""Register and export the built-in slash-command catalog."""

from kohakuterrarium.builtins.user_commands.registry import (
    get_builtin_user_command,
    list_builtin_user_commands,
    register_user_command,
)

# Importing each command runs its registration decorator. Command modules depend
# only on the lightweight registry, keeping this catalog outside their import path.
from kohakuterrarium.builtins.user_commands.branch import BranchCommand
from kohakuterrarium.builtins.user_commands.channels import ChannelsCommand
from kohakuterrarium.builtins.user_commands.clear import ClearCommand
from kohakuterrarium.builtins.user_commands.compact import CompactCommand
from kohakuterrarium.builtins.user_commands.drives import DrivesCommand
from kohakuterrarium.builtins.user_commands.edit import EditCommand
from kohakuterrarium.builtins.user_commands.env import EnvCommand
from kohakuterrarium.builtins.user_commands.exit import ExitCommand
from kohakuterrarium.builtins.user_commands.fork import ForkCommand
from kohakuterrarium.builtins.user_commands.help import HelpCommand
from kohakuterrarium.builtins.user_commands.jobs import JobsCommand
from kohakuterrarium.builtins.user_commands.model import ModelCommand
from kohakuterrarium.builtins.user_commands.module import ModuleCommand
from kohakuterrarium.builtins.user_commands.plugin import PluginCommand
from kohakuterrarium.builtins.user_commands.regen import RegenCommand
from kohakuterrarium.builtins.user_commands.scratchpad import ScratchpadCommand
from kohakuterrarium.builtins.user_commands.settings import SettingsCommand
from kohakuterrarium.builtins.user_commands.skill import SkillUserCommand
from kohakuterrarium.builtins.user_commands.spawn import SpawnCommand
from kohakuterrarium.builtins.user_commands.start import StartCommand
from kohakuterrarium.builtins.user_commands.status import StatusCommand
from kohakuterrarium.builtins.user_commands.stop import StopCommand
from kohakuterrarium.builtins.user_commands.system_prompt import SystemPromptCommand
from kohakuterrarium.builtins.user_commands.tool_options import ToolOptionsCommand
from kohakuterrarium.builtins.user_commands.triggers import TriggersCommand
from kohakuterrarium.builtins.user_commands.workspace import WorkspaceCommand

__all__ = [
    "register_user_command",
    "get_builtin_user_command",
    "list_builtin_user_commands",
    "BranchCommand",
    "ChannelsCommand",
    "ClearCommand",
    "CompactCommand",
    "DrivesCommand",
    "EditCommand",
    "EnvCommand",
    "ExitCommand",
    "ForkCommand",
    "HelpCommand",
    "JobsCommand",
    "ModelCommand",
    "ModuleCommand",
    "PluginCommand",
    "RegenCommand",
    "ScratchpadCommand",
    "SettingsCommand",
    "SkillUserCommand",
    "SpawnCommand",
    "StartCommand",
    "StatusCommand",
    "StopCommand",
    "SystemPromptCommand",
    "ToolOptionsCommand",
    "TriggersCommand",
    "WorkspaceCommand",
]
