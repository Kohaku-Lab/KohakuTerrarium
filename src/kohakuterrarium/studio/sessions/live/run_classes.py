"""Bring a resumed session back to what each creature was doing.

:func:`read_run_classes` reads the run records before resume starts
anything; :func:`apply_run_classes` then stops the creatures that were
stopped and, when asked (boot restore), tells the ones cut off mid-turn to
keep working once their saved state is restored.
"""

import asyncio
from pathlib import Path
from typing import Any

import kohakuterrarium.session.run_state as run_state
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

INTERRUPTED = "interrupted"
CONTINUE_NUDGE = (
    "[The server restarted while you were in the middle of your last turn, so "
    "that turn was cut off. Pick up where you left off and keep working on the "
    "task.]"
)


def read_run_classes(path: str | Path) -> dict[str, str]:
    """``agent → idle | stopped | interrupted`` from a session file (requires blocking)."""
    store = SessionStore.open_readonly(Path(path))
    try:
        agents = list(store.meta.get("agents") or [])
        runs = run_state.read_runs(store, agents)
    finally:
        store.close(update_status=False)
    return {name: run_state.classify(record) for name, record in runs.items()}


async def _nudge_when_ready(creature: Any) -> None:
    try:
        await creature.wait_restoration_ready()
        await creature.inject_input(CONTINUE_NUDGE, source="resume")
    except Exception as exc:  # noqa: BLE001 - one creature must not stop the others
        logger.warning(
            "continue nudge failed",
            creature=getattr(creature, "name", "?"),
            error=str(exc),
        )


async def apply_run_classes(
    engine: Any, session_id: str, classes: dict[str, str], *, nudge: bool
) -> dict[str, list[str]]:
    """Stop the stopped creatures of ``session_id``; nudge the interrupted ones if ``nudge``."""
    stopped: list[str] = []
    interrupted: list[str] = []
    if engine is None:
        return {"stopped": stopped, "interrupted": interrupted}
    graph = next((g for g in engine.list_graphs() if g.graph_id == session_id), None)
    for creature_id in sorted(graph.creature_ids if graph else ()):
        creature = engine.get_creature(creature_id)
        verdict = classes.get(creature.name, run_state.IDLE)
        if verdict == run_state.STOPPED:
            await engine.stop(creature_id)
            stopped.append(creature.name)
        elif verdict == INTERRUPTED:
            interrupted.append(creature.name)
            if nudge:
                asyncio.get_running_loop().create_task(_nudge_when_ready(creature))
    return {"stopped": stopped, "interrupted": interrupted}
