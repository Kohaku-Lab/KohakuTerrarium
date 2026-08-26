"""KohakuTerrarium command-line entry point."""

import argparse
import sys

from kohakuterrarium.cli.select_args import add_run_like_args
from kohakuterrarium.utils.logging import configure_utf8_stdio
from kohakuterrarium.utils.startup_trace import mark as mark_startup


def _build_parser():
    from kohakuterrarium.cli._main import _build_parser as build_parser

    return build_parser()


def _version_requested(argv: list[str]) -> bool:
    return "--version" in argv and all(
        arg in {"--version", "--verbose"} for arg in argv
    )


def _build_surface_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"kt {command}")
    if command in {"cli", "tui"}:
        add_run_like_args(parser)
    elif command == "web":
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Bind host (default: 127.0.0.1, use 0.0.0.0 for LAN)",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8001,
            help="Bind port (auto-increments if busy)",
        )
        parser.add_argument(
            "--dev",
            action="store_true",
            help="API-only mode (run vite dev server separately)",
        )
        parser.add_argument(
            "--log-level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            default="INFO",
            help="Logging level",
        )
    elif command == "app":
        parser.add_argument(
            "--port", type=int, default=8001, help="Internal server port"
        )
        parser.add_argument(
            "--log-level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            default="INFO",
            help="Logging level",
        )
    return parser


def _dispatch_surface(command: str, args: argparse.Namespace) -> int:
    mark_startup("dispatch_selected", surface=command, command=command)
    if command in {"cli", "tui"}:
        from kohakuterrarium.cli.run import resolve_then_run

        return resolve_then_run(
            args.agent_path,
            io_mode=command,
            log_level=args.log_level,
            session=None if args.no_session else args.session,
            llm=args.llm,
            log_stderr=args.log_stderr,
            extra_creatures=args.add_creatures,
            extra_channels=args.add_channels,
        )
    from kohakuterrarium.serving.web import run_desktop_app, run_web_server

    if command == "web":
        run_web_server(
            host=args.host,
            port=args.port,
            dev=args.dev,
            log_level=args.log_level,
        )
    else:
        run_desktop_app(port=args.port, log_level=args.log_level)
    return 0


def main() -> int:
    configure_utf8_stdio(log=False)
    argv = sys.argv[1:]
    if _version_requested(argv):
        from kohakuterrarium.cli.version import format_version_report

        mark_startup("parser_ready", surface="cli")
        mark_startup("dispatch_selected", surface="cli", command="version")
        print(format_version_report(verbose="--verbose" in argv))
        return 0
    if not argv:
        from kohakuterrarium.serving.web import run_desktop_app

        mark_startup("parser_ready", surface="desktop")
        mark_startup("dispatch_selected", surface="desktop", command="desktop")
        run_desktop_app(log_level="INFO")
        return 0
    if argv[0] in {"cli", "tui", "web", "app"}:
        command = argv[0]
        args = _build_surface_parser(command).parse_args(argv[1:])
        mark_startup("parser_ready", surface=command)
        return _dispatch_surface(command, args)
    from kohakuterrarium.cli._main import main as cli_main

    return cli_main()


__all__ = ["main"]
