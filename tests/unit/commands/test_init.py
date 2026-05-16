"""Unit tests for :mod:`kohakuterrarium.commands` package re-exports.

The package surface must stay in sync: every name in ``__all__`` is
bound, and the default singleton command instances expose the
documented ``command_name`` values.
"""

import kohakuterrarium.commands as commands_pkg
from kohakuterrarium.commands.read import (
    InfoCommand,
    JobsCommand,
    ReadCommand,
    WaitCommand,
    info_command,
    jobs_command,
    read_command,
    wait_command,
)


class TestCommandsPackageExports:
    def test_all_names_are_bound(self):
        for name in commands_pkg.__all__:
            assert hasattr(commands_pkg, name), f"{name} in __all__ but not bound"

    def test_command_classes_exported(self):
        assert commands_pkg.ReadCommand is ReadCommand
        assert commands_pkg.InfoCommand is InfoCommand
        assert commands_pkg.JobsCommand is JobsCommand
        assert commands_pkg.WaitCommand is WaitCommand


class TestDefaultCommandInstances:
    def test_default_instances_have_documented_names(self):
        assert read_command.command_name == "read_job"
        assert info_command.command_name == "info"
        assert jobs_command.command_name == "jobs"
        assert wait_command.command_name == "wait"

    def test_default_instances_are_correct_types(self):
        assert isinstance(read_command, ReadCommand)
        assert isinstance(info_command, InfoCommand)
        assert isinstance(jobs_command, JobsCommand)
        assert isinstance(wait_command, WaitCommand)

    def test_descriptions_are_non_empty_one_liners(self):
        for cmd in (read_command, info_command, jobs_command, wait_command):
            assert cmd.description
            assert "\n" not in cmd.description
