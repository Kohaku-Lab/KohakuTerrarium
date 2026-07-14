"""CLI resume command — resume a session via the Terrarium engine.

Uses :meth:`Terrarium.resume` to rebuild creatures from a saved
``.kohakutr`` store, then runs a user-facing surface focused on the
privileged creature in the resumed graph.  ``io_mode`` selects the
surface: ``cli`` / ``plain`` mount the rich inline CLI, everything else
mounts the full-screen TUI.
"""

import asyncio
import os
import sys

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.resume import announce_migration_if_needed
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.engine_cli import run_engine_with_tui
from kohakuterrarium.terrarium.engine_rich_cli import run_engine_with_rich_cli
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    set_level,
)


def resume_cli(
    query: str | None,
    pwd_override: str | None,
    log_level: str,
    last: bool = False,
    io_mode: str | None = None,
    llm: str | None = None,
    log_stderr: str = "auto",
) -> int:
    """Resume an agent or terrarium session via the engine.

    ``io_mode`` selects the resumed surface: ``"cli"`` / ``"plain"``
    mount the rich inline CLI, everything else mounts the full-screen
    TUI. ``log_stderr="auto"`` skips stderr mirroring because both
    surfaces own the terminal.
    """
    configure_utf8_stdio(log=True)
    set_level(log_level)

    if log_stderr == "on":
        enable_stderr_logging(log_level)

    path = _resolve_session(query, last=last)
    if path is None:
        if query:
            print(f"No session found matching: {query}")
        else:
            print("No sessions found in ~/.kohakuterrarium/sessions/")
        return 1

    announce_migration_if_needed(path)
    pwd_override = _resolve_missing_pwd(path, pwd_override)

    try:
        return asyncio.run(_run(path, pwd_override, llm, io_mode))
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        if path.exists():
            print("\nSession saved. To resume:")
            print(f"  kt resume {path.stem}")


def _resolve_missing_pwd(path, pwd_override: str | None) -> str | None:
    """When the saved working dir no longer exists, ask for a new one
    (interactive only); an explicit ``--pwd`` always wins."""
    if pwd_override:
        return pwd_override
    try:
        store = SessionStore(path)
        try:
            saved = (store.load_meta() or {}).get("pwd", "")
        finally:
            store.close()
    except Exception:
        return pwd_override
    if not saved or os.path.isdir(saved):
        return pwd_override
    if not sys.stdin.isatty():
        print(f"Warning: saved working dir missing: {saved} (using current dir)")
        return pwd_override
    print(f"Saved working dir no longer exists: {saved}")
    while True:
        entered = input("New working dir (empty = current dir): ").strip()
        if not entered:
            return os.getcwd()
        if os.path.isdir(entered):
            return entered
        print(f"Not a directory: {entered}")


async def _run(path, pwd_override, llm, io_mode: str | None) -> int:
    store = SessionStore(path)
    try:
        engine = await Terrarium.resume(store, pwd=pwd_override, llm=llm)
        async with engine:
            graph_id = next(iter(engine._topology.graphs.keys()), None)
            if graph_id is None:
                print("Resume produced no graphs; session is empty.")
                return 1
            focus = _pick_focus(engine, graph_id)
            # Resume rebuilds the focus creature with NoneInput and starts
            # it, so neither launcher's stdin-swap fires; both drive input
            # via ``inject_input``. ``plain`` has no distinct engine surface
            # — alias it to the rich inline CLI.
            if io_mode in ("cli", "plain"):
                await run_engine_with_rich_cli(engine, focus, store)
            else:
                await run_engine_with_tui(engine, focus, store)
            return 0
    finally:
        store.close()


def _pick_focus(engine: Terrarium, graph_id: str) -> str:
    graph = engine.get_graph(graph_id)
    privileged: list[str] = []
    fallback: list[str] = []
    for cid in sorted(graph.creature_ids):
        try:
            c = engine.get_creature(cid)
        except KeyError:
            continue
        if getattr(c, "is_privileged", False):
            privileged.append(cid)
        else:
            fallback.append(cid)
    if privileged:
        return privileged[0]
    if fallback:
        return fallback[0]
    raise RuntimeError(f"resumed graph {graph_id!r} has no creatures")
