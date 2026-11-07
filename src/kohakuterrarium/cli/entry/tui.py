"""``kt-tui`` — the full-screen terminal front door.

With kohakutui installed it is ktui (``kt-ui``) opening full screen. Without it, and as
``legacy-tui``, it is the Textual TUI: ``kt run <creature> --mode tui`` with an optional
creature (startup picker when omitted) and a leading ``resume`` verb.
"""

import sys

from kohakuterrarium.cli.select.args import parse_standalone_args
from kohakuterrarium.terrarium.engine.cli.ktui_support import ktui_unavailable
from kohakuterrarium.utils.fd_limit import raise_fd_limit
from kohakuterrarium.utils.logging import configure_utf8_stdio
from kohakuterrarium.utils.startup_trace import mark as mark_startup


def resume_cli(*args, **kwargs):
    from kohakuterrarium.cli.resume import resume_cli as resume

    return resume(*args, **kwargs)


def ui_main(*args, **kwargs):
    from kohakuterrarium.cli.entry.ui import main as run_ui

    return run_ui(*args, **kwargs)


def resolve_then_run(*args, **kwargs):
    from kohakuterrarium.cli.run import resolve_then_run as run

    return run(*args, **kwargs)


def main(argv: list[str] | None = None) -> int:
    """``kt-tui``: ktui (full screen); the full-screen Textual TUI when kohakutui is not installed."""
    if ktui_unavailable():
        return legacy_main(argv, prog="kt-tui")
    return ui_main(argv, prog="kt-tui", preset=fullscreen)


def legacy_main(argv: list[str] | None = None, prog: str = "legacy-tui") -> int:
    """Run the standalone TUI entry point."""
    configure_utf8_stdio(log=True)
    raise_fd_limit(log=True)
    args = parse_standalone_args(prog=prog, argv=argv)
    mark_startup("parser_ready", surface="tui")
    if args.resume:
        return resume_cli(
            args.query,
            args.pwd,
            args.log_level,
            last=args.last,
            io_mode="tui",
            llm=args.llm,
            log_stderr=args.log_stderr,
        )
    session = None if args.no_session else args.session
    return resolve_then_run(
        args.agent_path,
        io_mode="tui",
        log_level=args.log_level,
        session=session,
        llm=args.llm,
        log_stderr=args.log_stderr,
        extra_creatures=args.add_creatures,
        extra_channels=args.add_channels,
    )


def fullscreen(_remembered: str) -> str:
    """kt-tui always opens full screen."""
    return "fullscreen"


if __name__ == "__main__":
    sys.exit(main())
