"""Restore the live-session list on server boot.

Each row left by an earlier boot is claimed, its creatures' run records are
read before anything starts, and the session is resumed in place; then the
stopped creatures are stopped again, the ones cut off mid-turn are told to
keep working (a user message that never started a turn is not re-sent), and
any whose tool or sub-agent jobs the restart killed are told which. Drive
work recovers through the Drive runtime's own reconcile on creature start. A
row that fails keeps its error for the Lab to show with a Retry.
"""

import asyncio
from pathlib import Path
from typing import Any, Callable

import kohakuterrarium.session.run_state as run_state
from kohakuterrarium.session.resume_target import resolve_resume_path
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence.resume import resume_session
from kohakuterrarium.studio.sessions.live.registry import live_sessions, path_key
from kohakuterrarium.studio.sessions.live.run_classes import (
    apply_run_classes,
    read_killed_jobs,
    read_run_classes,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

ServiceForDir = Callable[[str], Any]


async def _target_of(path: str) -> Path:
    """The file a row resumes: the row's file, or the session a merge moved it into."""
    return Path(await asyncio.to_thread(resolve_resume_path, path))


async def restore_row(service: Any, row: dict, target: Path | None = None) -> dict:
    """Resume one live-session row; returns what happened to it."""
    registry = live_sessions()
    path = row["path"]
    if target is None:
        try:
            target = await _target_of(path)
        except FileNotFoundError:
            registry.remove(path)
            return {"path": path, "status": "missing"}
        except Exception as exc:  # noqa: BLE001
            registry.update(path, failed=str(exc), claimed_by=run_state.BOOT_ID)
            return {"path": path, "status": "failed", "error": str(exc)}
    if not target.is_file():
        registry.remove(path)
        return {"path": path, "status": "missing"}
    registry.update(path, claimed_by=run_state.BOOT_ID, failed=None)
    try:
        classes = await asyncio.to_thread(read_run_classes, target)
        jobs = await asyncio.to_thread(read_killed_jobs, target)
        session = await resume_session(service, target, restore_runs=False)
    except Exception as exc:  # noqa: BLE001 - recorded for the Lab's Retry
        logger.warning("session restore failed", path=str(target), error=str(exc))
        registry.update(path, failed=str(exc))
        return {"path": path, "status": "failed", "error": str(exc)}
    if path_key(target) != path_key(path):
        registry.remove(path)
    applied = await apply_run_classes(
        host_engine_or_none(service),
        session.session_id,
        classes,
        nudge=True,
        jobs=jobs,
    )
    logger.info(
        "Session restored after restart",
        path=str(target),
        session_id=session.session_id,
        stopped=applied["stopped"],
        interrupted=applied["interrupted"],
        killed=applied["killed"],
    )
    return {
        "path": path,
        "status": "restored",
        "session_id": session.session_id,
        **applied,
    }


async def restore_live_sessions(service_for_dir: ServiceForDir) -> list[dict]:
    """Restore every row an earlier boot left; ``service_for_dir`` maps a row's session dir to its service."""
    outcomes = []
    seen: set[str] = set()
    registry = live_sessions()
    for row in registry.rows():
        if (
            row.get("claimed_by") == run_state.BOOT_ID
            or row.get("boot_id") == run_state.BOOT_ID
        ):
            continue
        try:
            target = await _target_of(row["path"])
        except FileNotFoundError:
            registry.remove(row["path"])
            outcomes.append({"path": row["path"], "status": "missing"})
            continue
        except Exception as exc:  # noqa: BLE001
            registry.update(row["path"], failed=str(exc), claimed_by=run_state.BOOT_ID)
            outcomes.append(
                {"path": row["path"], "status": "failed", "error": str(exc)}
            )
            continue
        key = path_key(target)
        if key in seen:
            if path_key(row["path"]) != key:
                registry.remove(row["path"])
            outcomes.append({"path": row["path"], "status": "merged"})
            continue
        seen.add(key)
        service = service_for_dir(row.get("session_dir") or "")
        if service is None:
            outcomes.append({"path": row["path"], "status": "skipped"})
            continue
        outcomes.append(await restore_row(service, row, target))
    return outcomes
