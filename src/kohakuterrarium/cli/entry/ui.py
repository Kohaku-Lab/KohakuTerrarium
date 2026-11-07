"""``kt-ui`` — the ktui front door.

* ``kt-ui`` opens the launcher: running sessions in a KT server to attach to, recent saved
  sessions (search, resume, rename, delete), installed creatures and terrariums to start.
* ``kt-ui <creature>`` starts it (``--add`` / ``--channel`` as in ``kt-cli``); with
  ``--daemon`` it runs in the background KT server and outlives the terminal.
* ``kt-ui resume [query|--last]`` resumes a saved session.
* ``kt-ui attach [session] [--url URL] [--creature NAME]`` joins a session running in a
  server; leaving detaches and the session goes on.

``--inline`` / ``--hybrid`` / ``--fullscreen`` override the remembered renderer for this run.
Equivalent to ``kt run <creature> --mode ktui`` / ``kt resume --mode ktui`` / ``kt attach``.
``kt-cli`` and ``kt-tui`` are this command opening inline / hybrid and full screen.
"""

import argparse
import asyncio
import os
import sys
from typing import Callable

from kohakuterrarium.cli.resume import resume_cli
from kohakuterrarium.cli.run import resolve_then_run
from kohakuterrarium.cli.select.args import (
    add_attach_args,
    add_resume_like_args,
    add_run_like_args,
)
from kohakuterrarium.terrarium.engine.cli.ktui_support import (
    RENDERER_ENV,
    ktui_unavailable,
)
from kohakuterrarium.utils.fd_limit import raise_fd_limit
from kohakuterrarium.utils.logging import configure_utf8_stdio
from kohakuterrarium.utils.startup_trace import mark as mark_startup

# kohakutui is optional for KT: what needs it is imported once it is known to be there


def attach_cli(*args, **kwargs):
    from kohakuterrarium.terrarium.engine.cli.ktui_remote import attach_cli as attach

    return attach(*args, **kwargs)


def remembered_renderer() -> str:
    from kohakuterrarium.builtins.ktui.config import KtuiConfig

    return KtuiConfig.load().renderer


def daemon_cli(*args, **kwargs):
    from kohakuterrarium.cli.entry.daemon import daemon_cli as run

    return run(*args, **kwargs)


def launch(*args, **kwargs):
    from kohakuterrarium.builtins.ktui.launcher import launch as run

    return run(*args, **kwargs)


def _renderer_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    for name in ("inline", "hybrid", "fullscreen"):
        group.add_argument(
            f"--{name}",
            dest="renderer",
            action="store_const",
            const=name,
            help=f"draw {name} for this run (default: the remembered renderer)",
        )


def parse(argv: list[str], prog: str = "kt-ui") -> argparse.Namespace:
    verb = argv[0] if argv and argv[0] in ("resume", "attach") else ""
    parser = argparse.ArgumentParser(
        prog=f"{prog} {verb}".strip(),
        description="KohakuTerrarium in ktui. Omit everything for the launcher.",
    )
    if verb == "resume":
        add_resume_like_args(parser)
    elif verb == "attach":
        add_attach_args(parser)
    else:
        add_run_like_args(parser)
        parser.add_argument("--url", default=None, help="KT server for the launcher")
        parser.add_argument(
            "--daemon",
            action="store_true",
            help="run the session in the background KT server (started when needed): "
            "/exit detaches and it keeps working; kt-ui attach comes back",
        )
    _renderer_flags(parser)
    ns = parser.parse_args(argv[1:] if verb else argv)
    ns.verb = verb
    return ns


def _resume(ns: argparse.Namespace, query: str | None) -> int:
    return resume_cli(
        query,
        getattr(ns, "pwd", None),
        getattr(ns, "log_level", "INFO"),
        last=bool(getattr(ns, "last", False)),
        io_mode="ktui",
        llm=getattr(ns, "llm", None),
        log_stderr=getattr(ns, "log_stderr", "auto"),
    )


def _run(ns: argparse.Namespace, ref: str) -> int:
    session = None if getattr(ns, "no_session", False) else getattr(ns, "session", None)
    return resolve_then_run(
        ref,
        io_mode="ktui",
        log_level=getattr(ns, "log_level", "INFO"),
        session=session,
        llm=getattr(ns, "llm", None),
        log_stderr=getattr(ns, "log_stderr", "auto"),
        extra_creatures=list(getattr(ns, "add_creatures", None) or []),
        extra_channels=list(getattr(ns, "add_channels", None) or []),
    )


def main(
    argv: list[str] | None = None,
    *,
    prog: str = "kt-ui",
    preset: Callable[[str], str] | None = None,
) -> int:
    """Run the ``kt-ui`` entry point. ``kt-cli`` / ``kt-tui`` call it with their ``prog``
    and a ``preset`` that maps the remembered renderer to the one they open in."""
    configure_utf8_stdio(log=True)
    raise_fd_limit(log=True)
    ns = parse(sys.argv[1:] if argv is None else argv, prog)
    mark_startup("parser_ready", surface="ktui")
    if reason := ktui_unavailable():
        print(reason, file=sys.stderr)
        return 1
    if not ns.renderer and preset is not None:
        ns.renderer = preset(remembered_renderer())
    if ns.renderer:
        os.environ[RENDERER_ENV] = ns.renderer
    match ns.verb:
        case "resume":
            return _resume(ns, ns.query)
        case "attach":
            return attach_cli(ns.session, ns.url, ns.creature, ns.renderer)
    if ns.daemon:
        if not ns.agent_path:
            print(
                "kt-ui --daemon: name the creature or terrarium to run in the background"
            )
            return 2
        return daemon_cli(ns, ns.agent_path)
    if ns.agent_path:
        return _run(ns, ns.agent_path)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("kt-ui needs an interactive terminal for its launcher; pass a creature.")
        return 2
    chosen = asyncio.run(launch(ns.url))
    if chosen is None:
        return 0
    verb, value = chosen
    match verb:
        case "attach":
            url, _, session_id = value.partition("|")
            return attach_cli(session_id, url, None, ns.renderer)
        case "resume":
            return _resume(ns, value)
        case _:
            return _run(ns, value)


if __name__ == "__main__":
    sys.exit(main())
