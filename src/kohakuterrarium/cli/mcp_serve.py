"""Standalone MCP lifecycle commands; output never implies unverified readiness."""

import argparse
import json
from pathlib import Path

from kohakuterrarium.cli.mcp_setup import add_setup_arguments, setup_cli
from kohakuterrarium.mcp_server.connection import ConnectionStore
from kohakuterrarium.mcp_server.service import (
    connection_url,
    rotate,
    start,
    status,
    stop,
)


def add_mcp_serve_subparser(subparsers):
    parser = subparsers.add_parser(
        "mcp-serve", help="Run KT tools as an authenticated remote MCP server"
    )
    commands = parser.add_subparsers(dest="mcp_serve_command", required=True)
    for command in ("setup", "start", "stop", "status", "url", "rotate"):
        child = commands.add_parser(command)
        child.add_argument("--workspace", type=Path, default=Path.cwd())
        child.add_argument("--state-dir", type=Path, help=argparse.SUPPRESS)
        if command == "url":
            child.add_argument(
                "--configured",
                action="store_true",
                help="Show the saved URL for the next start",
            )
        if command != "url":
            child.add_argument(
                "--json",
                action="store_true",
                help="Print status JSON without credentials",
            )
        if command == "start":
            child.add_argument(
                "--wait",
                type=float,
                default=30,
                help="Seconds to wait for verified public readiness (1-120)",
            )
        if command == "setup":
            add_setup_arguments(child)


def mcp_serve_cli(args) -> int:
    try:
        store = ConnectionStore(args.workspace, args.state_dir)
        command = args.mcp_serve_command
        if command == "setup":
            return setup_cli(args, store)
        if command == "url":
            print(connection_url(store, configured=args.configured))
            return 0
        if command == "start":
            result = start(
                store,
                wait=args.wait,
            )
        elif command == "stop":
            result = stop(store)
        elif command == "rotate":
            result = rotate(store)
        else:
            result = status(store)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        elif command == "rotate":
            print("MCP secret rotated; workspace remains stopped.")
            print(f"Workspace: {result['workspace']}")
            print(
                "Use 'kt mcp-serve start' to start and 'kt mcp-serve url' to copy "
                "the new URL (use the same --workspace). Update your MCP clients."
            )
        else:
            print(
                f"MCP: {result['state']}; local={result.get('local_ready', False)}; public={result.get('public_ready', False)}"
            )
            print(f"Workspace: {result['workspace']}")
            for label, settings in (
                ("Running", result.get("active")),
                ("Configured", result.get("configured")),
            ):
                if settings:
                    print(
                        f"{label}: mode={settings['tunnel']}; origin={settings['public_origin']}; port={settings['port']}"
                    )
            if result.get("restart_required"):
                print("Pending setup settings; stop and start to apply:")
                for key, change in result["pending_changes"].items():
                    print(f"  {key}: {change['running']!r} -> {change['configured']!r}")
            if result.get("error"):
                print(result["error"])
            if command == "start" and result.get("public_ready"):
                print("Connection URL (keep private): " + connection_url(store))
        return 0 if command != "start" or result.get("public_ready") else 1
    except (ValueError, OSError, RuntimeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"MCP: {exc}")
        return 1
