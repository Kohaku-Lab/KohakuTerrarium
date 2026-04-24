"""
Commands module - framework commands for controller.

Commands are special actions the legacy/custom text-format controller
path can use to interact with the framework.
"""

from kohakuterrarium.commands.base import (
    BaseCommand,
    Command,
    CommandResult,
    parse_command_args,
)
from kohakuterrarium.commands.read import (
    InfoCommand,
    JobsCommand,
    ReadCommand,
    WaitCommand,
)

__all__ = [
    # Protocol and base
    "Command",
    "BaseCommand",
    "CommandResult",
    "parse_command_args",
    # Implementations
    "ReadCommand",
    "InfoCommand",
    "JobsCommand",
    "WaitCommand",
]
