"""Session commands: ``/resume`` (a picker of recent sessions; the chosen one replaces this
one), ``/rename <title>`` and ``/export [md|html|jsonl] [path]``.

A resume cannot happen inside the running engine: the picker records the choice and leaves
the shell, and the launcher (``terrarium/engine/cli/ktui.py``) resumes it in a new engine.
"""

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakutui.ui import Picker

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.session.readonly.metadata import read_session_meta
from kohakuterrarium.studio.persistence.viewer.export import (
    SUPPORTED_FORMATS,
    build_export,
)
from kohakuterrarium.studio.sessions.summary.edit import set_title
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.file.path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

RECENT = 60
META_KEYS = ("name", "config_path", "config_type", "last_active", "pwd")


def session_dir() -> Path:
    """Where sessions are saved: the same rule as ``kt resume`` (``KT_SESSION_DIR``, else
    the config folder's ``sessions``)."""
    explicit = os.environ.get("KT_SESSION_DIR")
    return coerce_fs_path(explicit) if explicit else config_dir() / "sessions"


def recent_sessions(limit: int = RECENT) -> list[dict]:
    """The newest sessions as picker items: title (or file name), config, age."""
    root = session_dir()
    if not root.exists():
        return []
    files = sorted(
        [*root.glob("*.kohakutr"), *root.glob("*.kt")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    out = []
    now = time.time()
    for path in files:
        try:
            meta = read_session_meta(path, keys=META_KEYS)
        except Exception:
            meta = {}
        config = Path(str(meta.get("config_path") or "")).name or "?"
        title = str(meta.get("name") or "") or path.stem
        age = _age(now - path.stat().st_mtime)
        out.append(
            {
                "label": one_line(title, 60),
                "detail": f"{config} · {age}",
                "value": str(path),
                "group": _bucket(now - path.stat().st_mtime),
            }
        )
    return out


def _age(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def _bucket(seconds: float) -> str:
    if seconds < 86400:
        return "today"
    if seconds < 7 * 86400:
        return "this week"
    return "older"


class Sessions:
    def __init__(self, shell: "KtuiShell"):
        self.shell = shell

    def _store(self) -> Any:
        return getattr(self.shell.focused.agent, "session_store", None)

    async def resume(self) -> None:
        shell = self.shell
        items = recent_sessions()
        current = getattr(self._store(), "path", None)
        for item in items:
            item["current"] = current is not None and item["value"] == str(current)
        if not items:
            shell.app.notify("no saved sessions yet", "info")
            return
        picked = await shell.panel(
            Picker(
                items=items,
                title="Resume a session · this one is saved first",
                placeholder="type to search by title or config",
                sidebar=True,
                max_visible=12,
            )
        )
        if not picked or (current is not None and str(picked) == str(current)):
            return
        shell.next_session = str(picked)
        shell.exit()

    async def rename(self, title: str) -> None:
        shell = self.shell
        store = self._store()
        title = title.strip()
        if store is None:
            shell.app.notify("this session is not saved", "warning")
            return
        if not title:
            name = getattr(store, "meta", {}).get("name") or "(untitled)"
            shell.app.notify(f"title: {name} · /rename <new title>", "info")
            return
        await set_title(store, title)
        shell.app.notify(f"session renamed: {title}", "success")

    async def export(self, args: str) -> None:
        shell = self.shell
        store = self._store()
        if store is None:
            shell.app.notify("this session is not saved", "warning")
            return
        parts = args.split(None, 1)
        fmt = parts[0] if parts and parts[0] in SUPPORTED_FORMATS else "md"
        rest = parts[1] if parts and parts[0] in SUPPORTED_FORMATS else args
        name = Path(str(getattr(store, "path", "session"))).stem
        agent = shell.focused.agent.config.name
        try:
            _, body = await store.run(build_export, store, name, fmt, agent)
        except Exception as exc:
            shell.app.notify(f"export failed: {exc}", "error")
            return
        target = Path(rest.strip() or f"{name}-{agent}.{fmt}").expanduser()
        try:
            target.write_text(body, encoding="utf-8")
        except OSError as exc:
            shell.app.notify(f"export not written: {exc}", "error")
            return
        shell.app.notify(f"exported to {target.resolve()}", "success", 6)
