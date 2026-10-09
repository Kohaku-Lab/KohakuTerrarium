"""Find the tool and sub-agent jobs a server restart killed.

Every job leaves a start event (``tool_call`` / ``subagent_call``) and an end
event (``tool_result`` / ``subagent_result`` / ``background_result``) in the
session's event log. A job started under the boot that hosted the session,
with no end event (a crash) or ended by a graceful shutdown's cancel, died
with that process. ``state["jobs:reaped:<agent>"]``
lists the jobs already reported, so a later restart does not report them again.
"""

import time
from typing import Any

REAPED_PREFIX = "jobs:reaped:"
ARGS_LIMIT = 120

_STARTS = {"tool_call": "tool", "subagent_call": "subagent"}
_ENDS = ("tool_result", "subagent_result", "background_result")


def _job_id(event: dict) -> str:
    return str(event.get("call_id") or event.get("job_id") or "")


def _torn_down(event: dict, shutdown_at: float | None) -> bool:
    """An end event written by a graceful shutdown cancelling the job."""
    if shutdown_at is None:
        return False
    cut = event.get("cancelled") or event.get("interrupted")
    return bool(cut) and float(event.get("ts") or 0) >= shutdown_at


def unfinished_jobs(
    events: list[dict], since: float = 0.0, shutdown_at: float | None = None
) -> list[dict]:
    """Jobs started at or after ``since`` that never finished, oldest first.

    A job never finished when it has no end event, or when its only end is a
    cancel written at or after ``shutdown_at`` (a graceful server shutdown).
    Each is ``{job_id, kind (tool|subagent), name, detail, background, ts}``;
    ``detail`` is the tool arguments or the sub-agent task, bounded.
    """
    started: dict[str, dict] = {}
    ended: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        job_id = _job_id(event)
        if not job_id:
            continue
        if etype in _STARTS and float(event.get("ts") or 0) >= since:
            started.setdefault(job_id, {"event": event, "kind": _STARTS[etype]})
        elif etype in _ENDS and not _torn_down(event, shutdown_at):
            ended.add(job_id)
    jobs = []
    for job_id, item in started.items():
        if job_id in ended:
            continue
        event = item["event"]
        detail = event.get("task") if item["kind"] == "subagent" else event.get("args")
        jobs.append(
            {
                "job_id": job_id,
                "kind": item["kind"],
                "name": str(event.get("name") or item["kind"]),
                "detail": _bounded(detail),
                "background": bool(event.get("background", False)),
                "ts": float(event.get("ts") or 0),
            }
        )
    return sorted(jobs, key=lambda job: job["ts"])


def _bounded(value: Any) -> str:
    if value in (None, "", {}, []):
        return ""
    if isinstance(value, dict):
        text = ", ".join(f"{k}={v!r}" for k, v in value.items())
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= ARGS_LIMIT else text[: ARGS_LIMIT - 1] + "…"


def reaped_ids(store: Any, agent: str) -> set[str]:
    try:
        value = store.state.get(f"{REAPED_PREFIX}{agent}")
    except Exception:  # noqa: BLE001 - unreadable reads as none reported
        return set()
    return set(value) if isinstance(value, list) else set()


def mark_reaped(store: Any, agent: str, job_ids: list[str]) -> None:
    """Record jobs as reported (requires blocking)."""
    if not job_ids:
        return
    known = reaped_ids(store, agent) | set(job_ids)
    store.state[f"{REAPED_PREFIX}{agent}"] = sorted(known)


def killed_jobs(
    store: Any, agent: str, since: float = 0.0, shutdown_at: float | None = None
) -> list[dict]:
    """Unreported jobs of ``agent`` that the last boot left unfinished (requires blocking)."""
    done = reaped_ids(store, agent)
    return [
        job
        for job in unfinished_jobs(store.get_events(agent), since, shutdown_at)
        if job["job_id"] not in done
    ]


def describe(jobs: list[dict], *, now: float | None = None) -> str:
    """One line per killed job: kind, name, what it was doing, when it started."""
    now = time.time() if now is None else now
    lines = []
    for job in jobs:
        label = "sub-agent" if job["kind"] == "subagent" else "tool"
        ago = _duration(now - job["ts"]) if job["ts"] else ""
        detail = f" ({job['detail']})" if job["detail"] else ""
        started = f", started {ago} ago" if ago else ""
        lines.append(f"- {label} `{job['name']}`{detail}, job {job['job_id']}{started}")
    return "\n".join(lines)


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
