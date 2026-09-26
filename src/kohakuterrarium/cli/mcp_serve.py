"""Standalone MCP lifecycle commands; output never implies unverified readiness."""

import argparse
import json
from pathlib import Path

from kohakuterrarium.mcp_server.connection import ConnectionStore
from kohakuterrarium.mcp_server.service import start, status, stop


def add_mcp_serve_subparser(subparsers):
    parser = subparsers.add_parser(
        "mcp-serve", help="Run KT tools as an authenticated remote MCP server"
    )
    commands = parser.add_subparsers(dest="mcp_serve_command", required=True)
    for command in ("start", "stop", "status", "url"):
        child = commands.add_parser(command)
        child.add_argument("--workspace", type=Path, default=Path.cwd())
        child.add_argument("--state-dir", type=Path, help=argparse.SUPPRESS)
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
            child.add_argument(
                "--public-origin", help="Fixed HTTPS origin; required on first start"
            )
            child.add_argument("--tunnel", choices=("ngrok", "external"))
            child.add_argument("--port", type=int)
            child.add_argument("--ngrok-bin")
            child.add_argument("--ngrok-config", type=Path)
            child.add_argument(
                "--config", type=Path, help="Dedicated MCP tool configuration"
            )
            child.add_argument(
                "--import-connection",
                type=Path,
                help="Explicitly reuse a phase-one connection record",
            )


def mcp_serve_cli(args) -> int:
    try:
        store = ConnectionStore(args.workspace, args.state_dir)
        command = args.mcp_serve_command
        if command == "url":
            print(store.load().url)
            return 0
        if command == "start":
            result = start(
                store,
                wait=args.wait,
                public_origin=args.public_origin,
                tunnel=args.tunnel,
                port=args.port,
                ngrok_bin=args.ngrok_bin,
                ngrok_config=args.ngrok_config,
                tools_config=args.config,
                import_connection=args.import_connection,
            )
        elif command == "stop":
            result = stop(store)
        else:
            result = status(store)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(
                f"MCP: {result['state']}; local={result.get('local_ready', False)}; public={result.get('public_ready', False)}"
            )
            print(f"Workspace: {result['workspace']}")
            if result.get("error"):
                print(result["error"])
            if command == "start" and result.get("public_ready"):
                print("Connection URL (keep private): " + store.load().url)
        return 0 if command != "start" or result.get("public_ready") else 1
    except (ValueError, OSError, RuntimeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"MCP: {exc}")
        return 1
