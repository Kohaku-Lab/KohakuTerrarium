"""Unit tests for the shared cli/tui argument surface (``cli.select_args``)."""

import argparse
import sys

from kohakuterrarium.cli.select_args import (
    add_resume_like_args,
    add_run_like_args,
    parse_standalone_args,
)


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

    def test_run_like_path_marks_resume_false(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["kt-cli", "foo"])
        ns = parse_standalone_args("kt-cli")
        assert ns.resume is False
        assert ns.agent_path == "foo"


class TestResumeVerb:
    def test_defaults(self):
        p = argparse.ArgumentParser()
        add_resume_like_args(p)
        ns = p.parse_args([])
        assert ns.query is None
        assert ns.pwd is None
        assert ns.last is False
        assert ns.llm is None
        assert ns.log_level == "INFO"
        assert ns.log_stderr == "auto"

    def test_leading_resume_selects_resume_surface(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["kt-cli", "resume", "mysess", "--last"])
        ns = parse_standalone_args("kt-cli")
        assert ns.resume is True
        assert ns.query == "mysess"
        assert ns.last is True

    def test_resume_without_query_lists(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["kt-tui", "resume"])
        ns = parse_standalone_args("kt-tui")
        assert ns.resume is True
        assert ns.query is None
        assert ns.last is False

    def test_resume_passes_llm_and_pwd(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["kt-cli", "resume", "s", "--llm", "m", "--pwd", "/w"]
        )
        ns = parse_standalone_args("kt-cli")
        assert ns.resume is True
        assert ns.llm == "m"
        assert ns.pwd == "/w"
