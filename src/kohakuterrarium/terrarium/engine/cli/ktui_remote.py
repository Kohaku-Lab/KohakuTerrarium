"""``kt attach``: ktui on a session that runs in a KT server (``kt serve`` / ``kt web``).

The server is ``--url``, else ``KT_SERVER_URL``, else the one ``kt serve`` recorded in
``~/.kohakuterrarium/run/web.json``, else ``http://127.0.0.1:8001``. Without a session id
the running sessions are listed to pick from (one is taken directly). Leaving ktui detaches:
the session keeps running in the server.
"""

import asyncio
import os
import sys

from kohakuterrarium.builtins.ktui.config import KtuiConfig
from kohakuterrarium.builtins.ktui.launcher import pick_running
from kohakuterrarium.builtins.ktui.remote import RemoteError, RemoteSession
from kohakuterrarium.builtins.ktui.servers import running_sessions, server_url
from kohakuterrarium.builtins.ktui.shell import KtuiShell
from kohakuterrarium.terrarium.engine.cli.ktui_support import (
    RENDERER_ENV,
    ktui_unavailable,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


async def attach_ktui(
    url: str,
    session_id: str,
    creature: str | None = None,
    renderer: str | None = None,
) -> int:
    """Runs ktui on ``session_id`` until the user leaves; the session stays in the server."""
    config = KtuiConfig.load()
    renderer = renderer or os.environ.get(RENDERER_ENV)
    if renderer:
        config.renderer = renderer
        config.normalized()
    session = RemoteSession(url, session_id)
    try:
        await session.open()
    except RemoteError as exc:
        await session.close()
        print(f"cannot attach to {session_id} at {url}: {exc}", file=sys.stderr)
        return 1
    creatures = list(session.creatures.values())
    focus = next((c for c in creatures if creature in (c.name, c.creature_id)), None)
    if focus is None:
        if creature:
            names = ", ".join(c.name for c in creatures)
            print(
                f"no creature {creature!r} in {session_id} ({names})", file=sys.stderr
            )
            await session.close()
            return 1
        focus = creatures[0]

    shell = KtuiShell(session.engine, focus.creature_id, config)
    for c in [focus, *(c for c in creatures if c is not focus)]:
        shell.attach(c)
    session.on_state = lambda level, text: shell.app.notify(text, level)

    async def redraw() -> None:
        for entry in shell.entries.values():
            entry.view.reset()
            entry.view.jobs.clear()
            await entry.output.on_resume(await session.history(entry.creature))
            entry.view.processing = entry.agent.processing
        shell.refresh()

    session.on_reconnect = redraw
    shell.chrome.capture()
    link = asyncio.get_running_loop().create_task(session.run())
    try:
        for entry in shell.entries.values():
            if entry is not shell.focused:
                await entry.output.on_resume(await session.history(entry.creature))
        await shell.run(resume_events=await session.history(focus) or None)
    finally:
        shell.chrome.release()
        link.cancel()
        await session.close()
    print(f"\nDetached; {session_id} keeps running at {url}. Back to it:")
    print(f"  kt attach {session_id}")
    return 0


async def _choose(url: str, session_id: str | None) -> str | None:
    if session_id:
        return session_id
    sessions = await running_sessions(url)
    if not sessions:
        print(
            f"no running sessions at {url} (start one with `kt serve start`)",
            file=sys.stderr,
        )
        return None
    if len(sessions) == 1:
        return str(sessions[0]["session_id"])
    return await pick_running(url, sessions)


def attach_cli(
    session_id: str | None,
    url: str | None = None,
    creature: str | None = None,
    renderer: str | None = None,
) -> int:
    if reason := ktui_unavailable():
        print(reason, file=sys.stderr)
        return 1
    base = server_url(url)

    async def main() -> int:
        chosen = await _choose(base, session_id)
        if not chosen:
            return 1
        return await attach_ktui(base, chosen, creature, renderer)

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 130
