"""Interactive and scriptable setup for a workspace's standalone MCP server."""

import argparse
import json
import sys
from pathlib import Path

from kohakuterrarium.mcp_server.setup import SetupSession


def add_setup_arguments(parser):
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--mode", choices=("ngrok", "external"))
    parser.add_argument("--origin", help="Stable HTTPS origin, without the MCP path")
    parser.add_argument("--port", type=int)
    parser.add_argument("--ngrok-bin")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ngrok-config", type=Path)
    group.add_argument("--clear-ngrok-config", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--config", type=Path, help="MCP tools and delegation configuration"
    )
    group.add_argument("--clear-config", action="store_true")
    parser.add_argument("--import-connection", type=Path, help=argparse.SUPPRESS)


def _prompt(label, current=""):
    answer = input(f"{label} [{current}]: ").strip()
    return answer or current


def _path_prompt(label, current):
    answer = _prompt(
        label + " (empty keeps current; '-' restores default)", current or ""
    )
    return (None, True) if answer == "-" else (Path(answer) if answer else None, False)


def _wizard(store, original, options):
    defaults = original.summary() if original else {}
    print(f"Workspace: {store.workspace}")
    print("ngrok: KT manages the tunnel; external: you maintain the HTTPS entry.")
    mode = _prompt(
        "Mode (ngrok/external)", options["tunnel"] or defaults.get("tunnel", "ngrok")
    )
    if mode not in {"ngrok", "external"}:
        raise ValueError("Mode must be ngrok or external")
    options["tunnel"] = mode
    options["public_origin"] = (
        _prompt(
            "Public HTTPS origin",
            options["public_origin"] or defaults.get("public_origin", ""),
        )
        or None
    )
    options["port"] = int(
        _prompt(
            "Local port",
            str(
                options["port"]
                if options["port"] is not None
                else defaults.get("port", 8765)
            ),
        )
    )
    path = options["tools_config"] or (
        None if options["clear_tools_config"] else defaults.get("tools_config")
    )
    options["tools_config"], options["clear_tools_config"] = _path_prompt(
        "MCP configuration", "-" if options["clear_tools_config"] else path
    )
    if mode == "ngrok":
        options["ngrok_bin"] = _prompt(
            "ngrok executable",
            options["ngrok_bin"] or defaults.get("ngrok_bin", "ngrok"),
        )
        path = options["ngrok_config"] or (
            None if options["clear_ngrok_config"] else defaults.get("ngrok_config")
        )
        options["ngrok_config"], options["clear_ngrok_config"] = _path_prompt(
            "ngrok configuration", "-" if options["clear_ngrok_config"] else path
        )
    return options


def setup_cli(args, store) -> int:
    session = SetupSession.open(store)
    options = {
        "public_origin": args.origin,
        "tunnel": args.mode,
        "port": args.port,
        "ngrok_bin": args.ngrok_bin,
        "ngrok_config": args.ngrok_config,
        "tools_config": args.config,
        "clear_tools_config": args.clear_config,
        "clear_ngrok_config": args.clear_ngrok_config,
        "import_connection": args.import_connection,
    }
    interactive = sys.stdin.isatty() and not args.non_interactive and not args.json
    try:
        if interactive:
            options = _wizard(store, session.original, options)
        candidate = session.prepare(**options)
        if interactive:
            previous = session.original.summary() if session.original else {}
            print("\nConfiguration to save (setup does not start the service):")
            for key, value in candidate.summary().items():
                if key not in previous or previous[key] != value:
                    print(f"  {key}: {previous.get(key)!r} -> {value!r}")
            if previous and previous["public_origin"] != candidate.public_origin:
                print(
                    "Public origin changed: update the connection URL in ChatGPT after restart."
                )
            print(
                "Existing instances keep their running settings until stopped and started again."
            )
            if input("Save configuration? [y/N]: ").strip().lower() not in {"y", "yes"}:
                print("Setup cancelled; no configuration was saved.")
                return 0
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; no configuration was saved.")
        return 1
    # Cancellation of input is safe to describe as unsaved. Once committing,
    # interruption may occur after the atomic replace has already succeeded.
    result = session.save(candidate)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"MCP configuration saved for {store.workspace}.")
        print(
            f"Mode: {candidate.tunnel}; origin: {candidate.public_origin}; local port: {candidate.port}"
        )
        if result["origin_changed"]:
            print(
                "Public origin changed: update the connection URL in ChatGPT after restart."
            )
        if result["restart_required"]:
            print("Pending settings: run kt mcp-serve stop, then kt mcp-serve start.")
        elif result["running"]:
            print("The existing instance is unchanged.")
        else:
            print(
                "Run kt mcp-serve start to go online, then copy its private connection URL."
            )
    return 0
