"""Shared argument surface for the cli/tui front doors.

``kt-cli`` / ``kt-tui`` (standalone console scripts) and the ``kt cli`` /
``kt tui`` subcommand aliases all expose the same subset of ``kt run``
options, minus ``--mode`` (the command name fixes the mode) and with an
*optional* creature argument (omitted → startup picker).

:func:`add_run_like_args` is the single definition both paths build on so
they can never drift; :func:`parse_standalone_args` wraps it in a
top-level ``argparse.ArgumentParser`` for the standalone entry modules.
"""

import argparse

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
    parser.add_argument(
        "--session",
        nargs="?",
        const="__auto__",
        default="__auto__",
        help=(
            "Session file path (default: auto in ~/.kohakuterrarium/sessions/). "
            "Use --no-session to disable."
        ),
    )
    parser.add_argument(
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


def parse_standalone_args(prog: str) -> argparse.Namespace:
    """Build + parse args for a standalone ``kt-cli`` / ``kt-tui`` binary."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "KohakuTerrarium interactive shell. Omit the creature to pick one "
            "from an interactive list."
        ),
    )
    add_run_like_args(parser)
    return parser.parse_args()
