"""``kt-cli`` — rich inline CLI front door.

Equivalent to ``kt run <creature> --mode cli``, but the creature argument
is optional: omit it to choose from the startup picker.  A leading
``resume`` verb (``kt-cli resume <query>``) resumes a saved session in
the rich CLI instead.  Forces ``io_mode="cli"``; everything else routes
through the shared ``resolve_then_run`` / ``resume_cli`` cores.
"""

import sys

from kohakuterrarium.cli.select_args import parse_standalone_args
from kohakuterrarium.utils.fd_limit import raise_fd_limit
from kohakuterrarium.utils.logging import configure_utf8_stdio
from kohakuterrarium.utils.startup_trace import mark as mark_startup


def resume_cli(*args, **kwargs):
    from kohakuterrarium.cli.resume import resume_cli as resume

    return resume(*args, **kwargs)


def resolve_then_run(*args, **kwargs):
    from kohakuterrarium.cli.run import resolve_then_run as run

    return run(*args, **kwargs)


def main() -> int:
    """Run the standalone rich CLI entry point."""
    configure_utf8_stdio(log=True)
    raise_fd_limit(log=True)
    args = parse_standalone_args(prog="kt-cli")
    mark_startup("parser_ready", surface="cli")
    if args.resume:
        return resume_cli(
            args.query,
            args.pwd,
            args.log_level,
            last=args.last,
            io_mode="cli",
            llm=args.llm,
            log_stderr=args.log_stderr,
        )
    session = None if args.no_session else args.session
    return resolve_then_run(
        args.agent_path,
        io_mode="cli",
        log_level=args.log_level,
        session=session,
        llm=args.llm,
        log_stderr=args.log_stderr,
        extra_creatures=args.add_creatures,
        extra_channels=args.add_channels,
    )


if __name__ == "__main__":
    sys.exit(main())
