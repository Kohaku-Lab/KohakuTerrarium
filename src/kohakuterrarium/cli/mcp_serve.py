"""Standalone MCP lifecycle commands; output never implies unverified readiness."""

import json
import os
from pathlib import Path

from kohakuterrarium.cli.mcp_setup import add_setup_arguments, setup_cli
from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.management import manage
from kohakuterrarium.mcp_server.migration import migrate
from kohakuterrarium.utils.logging import enable_file_logging
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
    for command in (
        "setup",
        "start",
        "stop",
        "status",
        "url",
        "rotate",
        "migrate",
        "workspace",
    ):
        child = commands.add_parser(command)
        if command == "workspace":
            actions = child.add_subparsers(dest="workspace_command", required=True)
            for operation in ("add", "list", "remove"):
                action = actions.add_parser(operation)
                action.add_argument("--home-dir", type=Path)
                action.add_argument("--json", action="store_true")
                if operation != "list":
                    action.add_argument("name")
                if operation == "add":
                    action.add_argument("path", type=Path)
                if operation == "remove":
                    action.add_argument("--force", action="store_true")
            continue
        child.add_argument(
            "--home-dir",
            type=Path,
            help="KT configuration environment (defaults to KT_CONFIG_DIR)",
        )
        if command == "migrate":
            child.add_argument(
                "--workspace",
                type=Path,
                required=True,
                help="Stopped legacy workspace to import",
            )
            child.add_argument(
                "--name", required=True, help="Registered workspace name"
            )
            child.add_argument("--legacy-state-dir", type=Path)
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
        if args.home_dir:
            os.environ["KT_CONFIG_DIR"] = str(args.home_dir.expanduser().resolve())
            enable_file_logging()
        store = EndpointStore(args.home_dir)
        command = args.mcp_serve_command
        if command == "setup":
            return setup_cli(args, store)
        if command in {"workspace", "migrate"}:
            if command == "migrate":
                result = migrate(
                    store, args.workspace, args.name, args.legacy_state_dir
                )
            else:
                result = manage(
                    store,
                    args.workspace_command,
                    name=getattr(args, "name", None),
                    path=getattr(args, "path", None),
                    force=getattr(args, "force", False),
                )
            print(
                json.dumps(result, ensure_ascii=False, indent=None if args.json else 2)
            )
            if command == "migrate" and not args.json:
                print(
                    "New endpoint credentials created. Start the endpoint and register its new URL in your MCP client."
                )
            return 0
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
            print("MCP secret rotated; endpoint remains stopped.")
            print(f"Configuration environment: {result['home_dir']}")
            print(
                "Use 'kt mcp-serve start' to start and 'kt mcp-serve url' to copy "
                "the new URL (use the same --home-dir). Update your MCP clients."
            )
        else:
            print(
                f"MCP: {result['state']}; local={result.get('local_ready', False)}; public={result.get('public_ready', False)}"
            )
            print(f"Configuration environment: {result['home_dir']}")
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
