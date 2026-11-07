"""The ``kt-ui`` launcher: one docked picker before any session starts, with a group column:

* **running**: sessions in a reachable KT server; picking one offers attach or stop;
* **recent**: saved sessions (type to search); picking one offers resume, rename, delete;
* each catalog source: its creatures and terrariums, to start.

:func:`launch` returns what was chosen as ``(verb, value)``: ``("attach", url|session_id)``,
``("resume", path)``, ``("run", ref)``, or None when cancelled. :func:`pick_running` is the
running-sessions part alone, for ``kt attach``.
"""

import asyncio
from pathlib import Path

from kohakutui import App
from kohakutui.ui import Confirm, Picker, Prompt

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.builtins.ktui.servers import (
    running_sessions,
    server_url,
    stop_session,
)
from kohakuterrarium.builtins.ktui.sessions import recent_sessions
from kohakuterrarium.cli.select.select import enumerate_runnables
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import delete_session_files
from kohakuterrarium.studio.sessions.summary.edit import reindex, set_title
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

SEP = "|"


def _running_items(url: str, sessions: list[dict]) -> list[dict]:
    items = []
    for s in sessions:
        # the listing gives a count; a full session record gives the creatures
        creatures = s.get("creatures") or []
        if isinstance(creatures, int):
            names = f"{creatures} creature{'s' if creatures != 1 else ''}"
        else:
            names = ", ".join(
                str(c.get("name", "")) for c in creatures if isinstance(c, dict)
            )
        items.append(
            {
                "label": str(s.get("name") or s["session_id"]),
                "detail": one_line(f"{names or '—'} · {s['session_id']}", 60),
                "value": f"attach:{url}{SEP}{s['session_id']}",
                "group": "running",
            }
        )
    return items


def _runnable_items() -> list[dict]:
    try:
        groups = enumerate_runnables()
    except Exception as exc:
        logger.debug("catalog not read", error=str(exc))
        return []
    items = []
    for group in groups:
        for r in group.items():
            icon = "▦" if r.kind == "terrarium" else "●"
            items.append(
                {
                    "label": f"{icon} {r.name}",
                    "detail": one_line(r.description or r.ref, 60) or None,
                    "value": f"run:{r.ref}",
                    "group": group.source or "creatures",
                }
            )
    return items


async def _picker(app: App, items: list[dict], title: str) -> str | None:
    answer = await app.run_dialog_async(
        Picker(
            items=items,
            title=title,
            placeholder="type to search",
            sidebar=True,
            max_visible=14,
        )
    )
    return str(answer) if answer else None


async def _saved(app: App, path: str) -> tuple[str, str] | None:
    """Resume / rename / delete for one saved session; None goes back to the list."""
    name = Path(path).stem
    choice = await app.run_dialog_async(
        Picker(
            items=[
                {"label": "resume", "value": "resume"},
                {"label": "rename", "value": "rename"},
                {"label": "delete", "value": "delete", "tone": "error"},
                {"label": "‹ back", "value": "back"},
            ],
            title=f"Session {name}",
        )
    )
    match choice:
        case "resume":
            return ("resume", path)
        case "rename":
            title = await app.run_dialog_async(Prompt(question=f"New title for {name}"))
            if title and str(title).strip():
                store = await asyncio.to_thread(SessionStore, path)
                try:
                    await set_title(store, str(title).strip())
                    await reindex(store)
                finally:
                    await asyncio.to_thread(store.close, update_status=False)
                app.notify(f"renamed: {str(title).strip()}", "success")
        case "delete":
            sure = await app.run_dialog_async(
                Confirm(question=f"Delete {name} and its files? This cannot be undone.")
            )
            if sure:
                removed = await asyncio.to_thread(delete_session_files, name)
                app.notify(f"deleted {len(removed)} file(s)", "success")
    return None


async def _running(app: App, value: str) -> tuple[str, str] | str | None:
    """Attach / stop for one running session (``value``: ``url|session_id``); None goes back
    to the list, ``"stopped"`` after stopping it."""
    url, _, session_id = value.partition(SEP)
    choice = await app.run_dialog_async(
        Picker(
            items=[
                {"label": "attach", "value": "attach"},
                {"label": "stop", "value": "stop", "tone": "error"},
                {"label": "‹ back", "value": "back"},
            ],
            title=f"Running {session_id}",
        )
    )
    if choice == "attach":
        return ("attach", value)
    if choice == "stop":
        sure = await app.run_dialog_async(
            Confirm(question=f"Stop {session_id}? Its creatures stop; it stays saved.")
        )
        if sure:
            error = await stop_session(url, session_id)
            if error:
                app.notify(f"not stopped: {error}", "error")
                return None
            app.notify(f"stopped {session_id}", "success")
            return "stopped"
    return None


async def launch(
    url: str | None = None, app: App | None = None
) -> tuple[str, str] | None:
    """Runs the launcher in the terminal (inline; ``app``: another one) until something is
    chosen."""
    base = server_url(url)
    running = await running_sessions(base)
    runnables = _runnable_items()
    app = app or App(mode="inline", interrupt="event")
    app.start()
    try:
        while True:
            items = [
                *_running_items(base, running),
                *(
                    {**i, "group": "recent", "value": f"saved:{i['value']}"}
                    for i in recent_sessions()
                ),
                *runnables,
            ]
            if not items:
                print("nothing to start: install a creature (`kt install @kt-biome`)")
                return None
            picked = await _picker(app, items, "kt-ui · start, resume or attach")
            if picked is None:
                return None
            verb, _, value = picked.partition(":")
            if verb == "run":
                return (verb, value)
            if verb == "attach":
                chosen = await _running(app, value)
                if chosen == "stopped":
                    running = await running_sessions(base)
                    continue
            else:
                chosen = await _saved(app, value)
            if chosen is not None:
                return chosen
    finally:
        app.stop()


async def pick_running(
    url: str, sessions: list[dict], app: App | None = None
) -> str | None:
    """One of ``sessions`` (running at ``url``) by its id, or None."""
    app = app or App(mode="inline", interrupt="event")
    app.start()
    try:
        picked = await _picker(app, _running_items(url, sessions), f"Attach · {url}")
    finally:
        app.stop()
    return picked.rpartition(SEP)[2] if picked else None
