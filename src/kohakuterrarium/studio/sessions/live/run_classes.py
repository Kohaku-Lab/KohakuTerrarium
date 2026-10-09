"""Bring a resumed session back to what each creature was doing.

:func:`read_run_classes` reads the run records and :func:`read_killed_jobs`
the jobs the restart killed, before resume starts anything;
:func:`apply_run_classes` then stops the creatures that were stopped and,
when asked (boot restore), tells the ones cut off mid-turn or whose jobs
were killed what happened, once their saved state is restored.
"""

import asyncio
from pathlib import Path
from typing import Any

import kohakuterrarium.session.job_reaper as job_reaper
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


def read_killed_jobs(path: str | Path) -> dict[str, list[dict]]:
    """``agent → jobs the last boot left unfinished`` from a session file (requires blocking).

    Only jobs started after that boot began hosting the session count; a
    session without that mark falls back to the start of each agent's last turn.
    """
    store = SessionStore.open_readonly(Path(path))
    try:
        agents = list(store.meta.get("agents") or [])
        lifecycle = run_state.read_lifecycle(store)
        hosted_at = lifecycle.get("hosted_at")
        shutdown_at = (
            lifecycle.get("updated_at")
            if lifecycle.get("stop_reason") == run_state.STOP_SHUTDOWN
            else None
        )
        out: dict[str, list[dict]] = {}
        for agent in agents:
            since = hosted_at or (run_state.read_run(store, agent) or {}).get(
                "started_at"
            )
            if since is None:
                continue
            jobs = job_reaper.killed_jobs(store, agent, float(since), shutdown_at)
            if jobs:
                out[agent] = jobs
    finally:
        store.close(update_status=False)
    return out


def nudge_text(interrupted: bool, jobs: list[dict]) -> str:
    """The message a restored creature gets about its cut-off turn and killed jobs."""
    if not jobs:
        return CONTINUE_NUDGE
    lead = (
        "The server restarted while you were in the middle of your last turn, so "
        "that turn was cut off."
        if interrupted
        else "The server restarted."
    )
    return (
        f"[{lead} These jobs you started were killed and did not finish; their "
        f"results are lost:\n{job_reaper.describe(jobs)}\n"
        "Rerun any you still need, or carry on without them.]"
    )


async def _nudge_when_ready(creature: Any, text: str, on_sent=None) -> None:
    try:
        await creature.wait_restoration_ready()
        await creature.inject_input(text, source="resume")
        if on_sent is not None:
            await on_sent()
    except Exception as exc:  # noqa: BLE001 - one creature must not stop the others
        logger.warning(
            "restart nudge failed",
            creature=getattr(creature, "name", "?"),
            error=str(exc),
        )


def _reaper(store: Any, agent: str, jobs: list[dict]):
    async def mark() -> None:
        if store is not None:
            ids = [job["job_id"] for job in jobs]
            await store.run(job_reaper.mark_reaped, store, agent, ids)

    return mark


async def apply_run_classes(
    engine: Any,
    session_id: str,
    classes: dict[str, str],
    *,
    nudge: bool,
    jobs: dict[str, list[dict]] | None = None,
) -> dict[str, Any]:
    """Stop the stopped creatures of ``session_id``; with ``nudge``, wake the rest that need it.

    A creature cut off mid-turn, or whose jobs (``jobs``) the restart killed,
    is told so once its saved state is restored; reported jobs are recorded
    so a later restart does not report them again.
    """
    stopped: list[str] = []
    interrupted: list[str] = []
    killed: dict[str, list[dict]] = {}
    jobs = jobs or {}
    if engine is None:
        return {"stopped": stopped, "interrupted": interrupted, "killed": killed}
    store = getattr(engine, "_session_stores", {}).get(session_id)
    graph = next((g for g in engine.list_graphs() if g.graph_id == session_id), None)
    for creature_id in sorted(graph.creature_ids if graph else ()):
        creature = engine.get_creature(creature_id)
        verdict = classes.get(creature.name, run_state.IDLE)
        if verdict == run_state.STOPPED:
            await engine.stop(creature_id)
            stopped.append(creature.name)
            continue
        cut_off = verdict == INTERRUPTED
        lost = jobs.get(creature.name, [])
        if cut_off:
            interrupted.append(creature.name)
        if lost:
            killed[creature.name] = [
                {"job_id": job["job_id"], "kind": job["kind"], "name": job["name"]}
                for job in lost
            ]
        if nudge and (cut_off or lost):
            asyncio.get_running_loop().create_task(
                _nudge_when_ready(
                    creature,
                    nudge_text(cut_off, lost),
                    _reaper(store, creature.name, lost) if lost else None,
                )
            )
    return {"stopped": stopped, "interrupted": interrupted, "killed": killed}
