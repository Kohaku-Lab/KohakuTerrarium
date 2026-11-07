"""The Status page, as the web's Status tab: a Session card, Usage (tokens, then each
creature's context as a bar), Health, and the Agents, Channels, Drives and Jobs lists. Entries
act like the web's row buttons: an agent opens its chat or, through its children, switches
model or stops / starts; a channel opens its chat; a drive or a job opens its detail in the
side panel; a job's child stops it.
"""

import time
from typing import TYPE_CHECKING, Any

import kohakuterrarium.builtins.ktui.chrome_data as data
from kohakuterrarium.builtins.ktui.format import duration, tokens
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)


def _uptime(started: float) -> str:
    return duration(time.monotonic() - started)


def session_fields(shell: "KtuiShell", started: float) -> list[dict]:
    entry = shell.focused
    busy = any(shell.is_busy(e) for e in shell.entries.values())
    graph = getattr(entry.creature, "graph_id", "") or "—"
    model = entry.view.usage.model or "?"
    provider, _, name = model.partition("/")
    return [
        {"label": "status", "value": "running" if busy else "idle"},
        {"label": "uptime", "value": _uptime(started)},
        {"label": "model", "value": name or provider},
        {"label": "provider", "value": provider if name else "—"},
        {"label": "session", "value": str(graph)},
    ]


def usage_fields(shell: "KtuiShell") -> list[dict]:
    prompt = sum(e.view.usage.tokens_in for e in shell.entries.values())
    cached = sum(e.view.usage.cached for e in shell.entries.values())
    out = sum(e.view.usage.tokens_out for e in shell.entries.values())
    return [
        {"label": "prompt", "value": tokens(prompt)},
        {"label": "completion", "value": tokens(out)},
        {"label": "cached", "value": tokens(cached)},
        {"label": "total", "value": tokens(prompt + out)},
    ]


def context_bars(shell: "KtuiShell") -> dict | None:
    """Each creature's last prompt against its context window, as gauge bars."""
    labels, values = [], []
    for entry in shell.entries.values():
        pct = entry.view.usage.context_pct()
        if pct is None:
            continue
        labels.append(entry.view.name)
        values.append(pct)
    if not labels:
        return None
    return {
        "labels": labels,
        "series": [{"name": "context %", "data": values}],
        "max": 100,
    }


def _connection(shell: "KtuiShell") -> str:
    if not getattr(shell.engine, "keeps_running", False):
        return "in process"
    session = shell.engine.session
    state = "connected" if session.connected else "reconnecting…"
    return f"{session.base_url} · {state}"


def health_fields(shell: "KtuiShell") -> list[dict]:
    stopped = 0
    for entry in shell.entries.values():
        if not getattr(entry.agent, "_running", True):
            stopped += 1
    jobs = sum(len(e.view.jobs) for e in shell.entries.values())
    unread = sum(e.view.unread for e in shell.entries.values())
    unread += sum(c.unread for c in shell.chrome.channels.values())
    return [
        {"label": "connection", "value": _connection(shell)},
        {"label": "stopped agents", "value": str(stopped)},
        {"label": "running jobs", "value": str(jobs)},
        {"label": "waiting for you", "value": str(shell.interactions.count)},
        {"label": "unread", "value": str(unread)},
    ]


def agent_items(shell: "KtuiShell") -> list[dict]:
    out = []
    for cid, entry in shell.entries.items():
        view, usage = entry.view, entry.view.usage
        running = getattr(entry.agent, "_running", True)
        busy = shell.is_busy(entry)
        state = "working" if busy else ("idle" if running else "stopped")
        pct = usage.context_pct()
        job = next(reversed(view.jobs.values()), None) if view.jobs else None
        detail = " · ".join(
            p
            for p in (
                state,
                usage.model or "?",
                f"ctx {pct}%" if pct is not None else "",
                f"{tokens(usage.tokens_in + usage.tokens_out)} tokens",
                f"now: {job.name}" if job else "",
            )
            if p
        )
        out.append(
            {
                "label": view.name,
                "icon": "⚙" if busy else ("●" if running else "○"),
                "tone": "info" if busy else ("success" if running else None),
                "detail": detail,
                "value": f"creature:{cid}",
                "children": [
                    {"label": "open chat", "value": f"creature:{cid}"},
                    {"label": "switch model…", "value": f"st:model:{cid}"},
                    {
                        "label": "stop" if running else "start",
                        "value": f"st:{'stop' if running else 'start'}:{cid}",
                    },
                ],
            }
        )
    return out


def channel_items(shell: "KtuiShell") -> list[dict]:
    graph = data.graph_of(shell)
    if graph is None:
        return []
    known = data.names(shell)
    out = []
    for name in sorted(graph.channels):
        senders = [
            known.get(c, c)
            for c in graph.creature_ids
            if name in graph.send_edges.get(c, ())
        ]
        listeners = [
            known.get(c, c)
            for c in graph.creature_ids
            if name in graph.listen_edges.get(c, ())
        ]
        tab = shell.chrome.channels.get(name)
        unread = tab.unread if tab else 0
        out.append(
            {
                "label": f"#{name}",
                "detail": f"sends: {', '.join(senders) or '—'} · listens: "
                f"{', '.join(listeners) or '—'}"
                + (f" · {unread} new" if unread else ""),
                "value": f"channel:{name}",
            }
        )
    return out


def job_items(shell: "KtuiShell") -> list[dict]:
    now = time.monotonic()
    out = []
    for cid, entry in shell.entries.items():
        for job in entry.view.jobs.values():
            out.append(
                {
                    "label": job.name,
                    "icon": (
                        "◆"
                        if job.kind == "subagent"
                        else ("⏵" if job.background else "⚙")
                    ),
                    "detail": f"{job.kind} · {entry.view.name} · {duration(now - job.started)}",
                    "value": f"{'trace' if job.kind == 'subagent' else 'job'}:{cid}:{job.job_id}",
                    "children": [
                        {"label": "stop", "value": f"st:jobstop:{cid}:{job.job_id}"}
                    ],
                }
            )
    return out


def drive_items(drives: data.Drives) -> list[dict]:
    return [{**item, "children": None} for item in drives.items()]


def status_sections(shell: "KtuiShell", drives: data.Drives, started: float) -> list:
    out: list[tuple[str, str | None, str, Any]] = [
        ("session", "Session", "kv", session_fields(shell, started)),
        ("usage", "Usage", "kv", usage_fields(shell)),
    ]
    bars = context_bars(shell)
    if bars:
        out.append(("context", "Context", "bars", bars))
    out.append(("health", "Health", "kv", health_fields(shell)))
    out.append(("agents", f"Agents · {len(shell.entries)}", "tree", agent_items(shell)))
    channels = channel_items(shell)
    if channels:
        out.append(("channels", f"Channels · {len(channels)}", "tree", channels))
    drive_list = drive_items(drives)
    if getattr(shell.engine, "drives", None) is not None:
        new = {"label": "+ new goal", "value": "cfg:goal", "tone": "info"}
        out.append(
            ("drives", f"Drives · {len(drive_list)}", "tree", [*drive_list, new])
        )
    jobs = job_items(shell)
    out.append(
        (
            "jobs",
            f"Jobs · {len(jobs)}",
            "tree",
            jobs or [{"label": "none running", "value": "st:none"}],
        )
    )
    return out


async def status_action(shell: "KtuiShell", what: str) -> None:
    """``st:<what>``: switch model, stop / start a creature, stop a job."""
    kind, _, rest = what.partition(":")
    match kind:
        case "model":
            entry = shell.entries.get(rest)
            if entry is not None:
                await shell.settings.pick_model(entry)
        case "stop" | "start":
            try:
                if kind == "stop":
                    await shell.engine.stop(rest)
                else:
                    await shell.engine.start(rest)
                shell.app.notify(
                    f"{kind} · {data.names(shell).get(rest, rest)}", "success"
                )
            except Exception as exc:
                shell.app.notify(f"{kind} failed: {exc}", "error")
        case "jobstop":
            cid, _, job_id = rest.partition(":")
            entry = shell.entries.get(cid)
            job = entry.view.jobs.get(job_id) if entry else None
            if entry is not None and job is not None:
                entry.agent._cancel_job(job_id, job.name)
                shell.app.notify(f"stopping {job.name}", "info")
    shell.refresh()
