"""CLI resume command — resume a session via the Terrarium engine.

Uses :meth:`Terrarium.resume` to rebuild creatures from a saved
``.kohakutr`` store, then runs the engine TUI focused on the privileged
creature in the resumed graph.
"""

import asyncio

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.resume import announce_migration_if_needed
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.engine_cli import run_engine_with_tui
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
    llm_override: str | None = None,
    log_stderr: str = "auto",
) -> int:
    """Resume an agent or terrarium session via the engine.

    ``io_mode`` is accepted but ignored — every resume runs the engine
    TUI. ``log_stderr="auto"`` skips stderr mirroring because the TUI
    owns the terminal.
    """
    configure_utf8_stdio(log=True)
    set_level(log_level)

    if io_mode in ("cli", "plain"):
        print(
            f"Warning: --mode {io_mode} is not yet supported on the engine "
            "path; using the TUI instead."
        )

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

    try:
        return asyncio.run(_run(path, pwd_override, llm_override))
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


async def _run(path, pwd_override, llm_override) -> int:
    store = SessionStore(path)
    try:
        engine = await Terrarium.resume(
            store, pwd=pwd_override, llm_override=llm_override
        )
        async with engine:
            graph_id = next(iter(engine._topology.graphs.keys()), None)
            if graph_id is None:
                print("Resume produced no graphs; session is empty.")
                return 1
            focus = _pick_focus(engine, graph_id)
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
