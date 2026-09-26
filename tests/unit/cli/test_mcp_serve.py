"""MCP CLI reports machine-readable failures without credential output."""

import argparse
import json

from kohakuterrarium.cli.mcp_serve import add_mcp_serve_subparser, mcp_serve_cli


def test_json_status_of_unconfigured_workspace(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "status",
            "--workspace",
            str(tmp_path),
            "--state-dir",
            str(tmp_path / "state"),
            "--json",
        ]
    )
    assert mcp_serve_cli(args) == 1
    assert "error" in json.loads(capsys.readouterr().out)
