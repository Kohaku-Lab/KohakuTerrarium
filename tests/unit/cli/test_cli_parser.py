"""Unit tests for the top-level ``kt`` argument parser."""

import argparse

import pytest

from kohakuterrarium.cli import _build_surface_parser
from kohakuterrarium.cli import _main as cli


class TestRunParser:
    def test_session_defaults_remain_automatic(self):
        args = cli._build_parser().parse_args(["run", "agent"])
        assert args.session == "__auto__"
        assert args.no_session is False

    def test_session_and_no_session_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as exc_info:
            cli._build_parser().parse_args(
                ["run", "agent", "--session", "run.kohakutr", "--no-session"]
            )
        assert exc_info.value.code == 2


def test_fast_surface_parsers_match_full_parser_contract():
    parser = cli._build_parser()
    commands = next(
        action for action in parser._actions if action.dest == "command"
    ).choices

    def signature(surface_parser):
        return [
            (
                tuple(action.option_strings),
                action.dest,
                action.default,
                action.required,
                action.choices,
                action.nargs,
            )
            for action in surface_parser._actions
        ]

    for command in ("web", "app", "cli", "tui"):
        assert signature(_build_surface_parser(command)) == signature(commands[command])


class TestMainStartup:
    def test_configures_utf8_before_building_parser(self, monkeypatch, capsys):
        events = []

        class _Parser:
            def parse_args(self):
                return argparse.Namespace(version=True, verbose=False)

        monkeypatch.setattr(
            cli,
            "configure_utf8_stdio",
            lambda **kwargs: events.append(("utf8", kwargs)),
            raising=False,
        )
        monkeypatch.setattr(
            cli,
            "raise_fd_limit",
            lambda **kwargs: events.append(("fd_limit", kwargs)),
            raising=False,
        )
        monkeypatch.setattr(
            cli,
            "_build_parser",
            lambda: events.append(("parser", {})) or _Parser(),
        )
        monkeypatch.setattr(cli, "format_version_report", lambda **_kwargs: "version")

        assert cli.main() == 0
        assert capsys.readouterr().out == "version\n"
        assert events == [
            ("utf8", {"log": False}),
            ("fd_limit", {}),
            ("parser", {}),
        ]

    def test_records_parser_and_dispatch_milestones(self, monkeypatch):
        milestones = []

        class _Parser:
            def parse_args(self):
                return argparse.Namespace(version=True, verbose=False)

        monkeypatch.setattr(cli, "configure_utf8_stdio", lambda **_kwargs: None)
        monkeypatch.setattr(cli, "_build_parser", lambda: _Parser())
        monkeypatch.setattr(cli, "format_version_report", lambda **_kwargs: "version")
        monkeypatch.setattr(
            cli,
            "mark_startup",
            lambda event, **fields: milestones.append((event, fields)),
            raising=False,
        )

        assert cli.main() == 0
        assert milestones == [
            ("parser_ready", {"surface": "cli"}),
            ("dispatch_selected", {"surface": "cli", "command": "version"}),
        ]
