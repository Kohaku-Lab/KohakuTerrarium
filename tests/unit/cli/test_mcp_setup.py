"""The setup wizard writes only after confirmation against its original version."""

import argparse
import io
import os

import pytest

from kohakuterrarium.cli.mcp_serve import add_mcp_serve_subparser, mcp_serve_cli
from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.setup import SetupSession


class TerminalInput(io.StringIO):
    def isatty(self):
        return True


def test_wizard_confirmation_cancel_and_eof(tmp_path, monkeypatch, capsys):
    store = EndpointStore(tmp_path / "state")
    original = store.configure(public_origin="https://old.example", tunnel="external")
    before = store.record_path.read_bytes()
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "setup",
            "--home-dir",
            str(tmp_path / "state"),
        ]
    )
    for answer, exit_code in (("n\n", 0), ("", 1)):
        monkeypatch.setattr(
            "sys.stdin", TerminalInput("\nhttps://new.example\n\n\n" + answer)
        )
        assert mcp_serve_cli(args) == exit_code
        assert store.record_path.read_bytes() == before
        assert original.secret not in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", TerminalInput("\nhttps://new.example\n\n\ny\n"))
    assert mcp_serve_cli(args) == 0
    assert store.load().public_origin == "https://new.example"
    assert store.load().secret == original.secret
    output = capsys.readouterr().out
    assert "update the connection URL" in output and original.secret not in output


def test_wizard_cannot_overwrite_another_save_during_review(
    tmp_path, monkeypatch, capsys
):
    store = EndpointStore(tmp_path / "state")
    store.configure(public_origin="https://old.example", tunnel="external")

    class RacingTerminal(TerminalInput):
        def readline(self, *args):
            line = super().readline(*args)
            if line == "y\n":
                other = SetupSession.open(store)
                other.save(other.prepare(port=9100))
            return line

    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "setup",
            "--home-dir",
            str(tmp_path / "state"),
        ]
    )
    monkeypatch.setattr("sys.stdin", RacingTerminal("\nhttps://new.example\n\n\ny\n"))
    assert mcp_serve_cli(args) == 1
    assert "changed in another command" in capsys.readouterr().out
    assert store.load().port == 9100
    assert store.load().public_origin == "https://old.example"


def test_interrupt_after_atomic_commit_is_not_reported_as_unsaved(
    tmp_path, monkeypatch, capsys
):
    store = EndpointStore(tmp_path / "state")
    store.configure(public_origin="https://old.example", tunnel="external")
    replace = os.replace

    def interrupted_replace(source, destination):
        replace(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupted_replace)
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "setup",
            "--non-interactive",
            "--home-dir",
            str(tmp_path / "state"),
            "--origin",
            "https://new.example",
        ]
    )
    with pytest.raises(KeyboardInterrupt):
        mcp_serve_cli(args)
    assert store.load().public_origin == "https://new.example"
    assert "no configuration was saved" not in capsys.readouterr().out
