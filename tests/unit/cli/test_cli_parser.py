"""Unit tests for the top-level ``kt`` argument parser."""

import pytest

from kohakuterrarium.cli import _build_parser


class TestRunParser:
    def test_session_defaults_remain_automatic(self):
        args = _build_parser().parse_args(["run", "agent"])
        assert args.session == "__auto__"
        assert args.no_session is False

    def test_session_and_no_session_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(
                ["run", "agent", "--session", "run.kohakutr", "--no-session"]
            )
        assert exc_info.value.code == 2
