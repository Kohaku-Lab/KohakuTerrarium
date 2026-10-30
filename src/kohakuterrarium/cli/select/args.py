"""Shared argument surface for the cli/tui front doors.

``kt-cli`` / ``kt-tui`` (standalone console scripts) and the ``kt cli`` /
``kt tui`` subcommand aliases all expose the same subset of ``kt run``
options, minus ``--mode`` (the command name fixes the mode) and with an
*optional* creature argument (omitted → startup picker).

:func:`add_run_like_args` is the single definition both paths build on so
they can never drift; :func:`parse_standalone_args` wraps it in a
top-level ``argparse.ArgumentParser`` for the standalone entry modules.

A leading ``resume`` verb (``kt-cli resume <query>``) switches to the
resume argument surface — the ``--mode`` is again fixed by the command
name, so :func:`add_resume_like_args` mirrors ``kt resume`` minus that
flag.
"""

import argparse
import sys

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def add_run_like_args(parser: argparse.ArgumentParser) -> None:
    """Register the run-like options shared by cli/tui front doors."""
    parser.add_argument(
        "agent_path",
        nargs="?",
        default=None,
        help=(
            "Creature folder, recipe folder, or @pkg/... reference. "
            "Omit to choose interactively from a startup picker."
        ),
    )
    parser.add_argument(
        "--llm",
        default=None,
        help="Override LLM profile (e.g., gpt-5.4, gemini, claude-sonnet-4.6)",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--session",
        nargs="?",
        const="__auto__",
        default="__auto__",
        help=(
            "Session file path (default: auto in ~/.kohakuterrarium/sessions/). "
            "Use --no-session to disable."
        ),
    )
    session_group.add_argument(
        "--no-session",
        action="store_true",
        help="Disable session persistence",
    )
    parser.add_argument(
        "--log-level",
        choices=_LOG_LEVELS,
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "--log-stderr",
        choices=["auto", "on", "off"],
        default="auto",
        help=(
            "Mirror logs to stderr. auto=off for cli/tui (the surface owns "
            "the terminal), on=always, off=never"
        ),
    )
    parser.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="CONFIG",
        dest="add_creatures",
        help=(
            "Spawn an additional (non-privileged) creature into the same graph "
            "at startup. Accepts a path or @pkg/creatures/<name>. Repeatable."
        ),
    )
    parser.add_argument(
        "--channel",
        action="append",
        default=[],
        metavar="NAME",
        dest="add_channels",
        help=(
            "Create a shared channel wiring every creature in the graph as "
            "listener + sender. Repeatable."
        ),
    )


def add_resume_like_args(parser: argparse.ArgumentParser) -> None:
    """Register the resume options shared by the cli/tui front doors.

    Mirrors ``kt resume`` minus ``--mode`` (the command name fixes it).
    """
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Session name/prefix, full path, or omit to list recent sessions.",
    )
    parser.add_argument("--pwd", default=None, help="Override working directory")
    parser.add_argument(
        "--last",
        action="store_true",
        help="Resume the most recent session",
    )
    parser.add_argument(
        "--llm",
        default=None,
        help="Override LLM profile (e.g., gpt-5.4, gemini, claude-sonnet-4.6)",
    )
    parser.add_argument(
        "--log-level",
        choices=_LOG_LEVELS,
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "--log-stderr",
        choices=["auto", "on", "off"],
        default="auto",
        help=(
            "Mirror logs to stderr. auto=off for cli/tui (the surface owns "
            "the terminal), on=always, off=never"
        ),
    )


def parse_standalone_args(prog: str) -> argparse.Namespace:
    """Build + parse args for a standalone ``kt-cli`` / ``kt-tui`` binary.

    A leading ``resume`` verb selects the resume surface; the returned
    namespace carries ``resume=True`` and the resume args. Otherwise the
    run-like surface applies and ``resume=False``.
    """
    argv = sys.argv[1:]
    if argv and argv[0] == "resume":
        parser = argparse.ArgumentParser(
            prog=f"{prog} resume",
            description="Resume a saved KohakuTerrarium session.",
        )
        add_resume_like_args(parser)
        ns = parser.parse_args(argv[1:])
        ns.resume = True
        return ns
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "KohakuTerrarium interactive shell. Omit the creature to pick one "
            "from an interactive list, or `resume <query>` to resume a session."
        ),
    )
    add_run_like_args(parser)
    ns = parser.parse_args(argv)
    ns.resume = False
    return ns
