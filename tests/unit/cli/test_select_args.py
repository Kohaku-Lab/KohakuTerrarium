"""Unit tests for the shared cli/tui argument surface (``cli.select_args``)."""

import argparse
import sys

from kohakuterrarium.cli.select_args import add_run_like_args, parse_standalone_args


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    add_run_like_args(p)
    return p


class TestAddRunLikeArgs:
    def test_defaults(self):
        ns = _parser().parse_args([])
        assert ns.agent_path is None
        assert ns.session == "__auto__"
        assert ns.no_session is False
        assert ns.add_creatures == []
        assert ns.add_channels == []
        assert ns.log_level == "INFO"
        assert ns.log_stderr == "auto"
        assert ns.llm is None

    def test_explicit_values_and_repeatable(self):
        ns = _parser().parse_args(
            [
                "foo",
                "--no-session",
                "--llm",
                "m",
                "--add",
                "a",
                "--add",
                "b",
                "--channel",
                "c",
                "--log-level",
                "DEBUG",
            ]
        )
        assert ns.agent_path == "foo"
        assert ns.no_session is True
        assert ns.llm == "m"
        assert ns.add_creatures == ["a", "b"]
        assert ns.add_channels == ["c"]
        assert ns.log_level == "DEBUG"

    def test_session_const_vs_explicit(self):
        assert _parser().parse_args(["--session"]).session == "__auto__"
        ns = _parser().parse_args(["--session", "/tmp/x.kohakutr"])
        assert ns.session == "/tmp/x.kohakutr"


class TestParseStandaloneArgs:
    def test_reads_sys_argv(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["kt-cli", "foo", "--llm", "x"])
        ns = parse_standalone_args("kt-cli")
        assert ns.agent_path == "foo"
        assert ns.llm == "x"

    def test_no_arg_leaves_agent_path_none(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["kt-tui"])
        ns = parse_standalone_args("kt-tui")
        assert ns.agent_path is None
