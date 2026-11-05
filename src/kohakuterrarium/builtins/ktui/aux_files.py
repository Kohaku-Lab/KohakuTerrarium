"""The aux pane's Files tab: the working directory as a tree whose directories load when
opened, and a preview of a chosen file docked at the input."""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from kohakutui.ui import Card

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

# never listed: version control and caches
SKIP = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    ".venv-win",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
MAX_ENTRIES = 200
PREVIEW_BYTES = 16_000
PREVIEW_LINES = 120


class FileTree:
    """Directory listings loaded on demand, keyed by path relative to the root."""

    def __init__(self, root: Path | None = None):
        self.root = (root or Path.cwd()).resolve()
        self.loaded: dict[str, list[tuple[str, bool]]] = {}

    def _list(self, rel: str) -> list[tuple[str, bool]]:
        """(name, is_dir) of a directory, directories first; [] when unreadable."""
        try:
            with os.scandir(self.root / rel) as it:
                entries = [
                    (e.name, e.is_dir(follow_symlinks=False))
                    for e in it
                    if e.name not in SKIP
                ]
        except OSError:
            return []
        entries.sort(key=lambda e: (not e[1], e[0].lower()))
        return entries[:MAX_ENTRIES]

    def expand(self, value: str) -> bool:
        """Loads a directory the user opened; True when it was not loaded yet."""
        rel = value.removeprefix("dir:")
        if not value.startswith("dir:") or rel in self.loaded:
            return False
        self.loaded[rel] = self._list(rel)
        return True

    def items(self) -> list[dict]:
        if "" not in self.loaded:
            self.loaded[""] = self._list("")
        return self._items("")

    def _items(self, rel: str) -> list[dict]:
        out = []
        for name, is_dir in self.loaded.get(rel, []):
            path = f"{rel}/{name}" if rel else name
            if is_dir:
                # not loaded yet: can open; loaded and empty: nothing to open
                kids = self._items(path) or None if path in self.loaded else True
                out.append(
                    {
                        "label": name + "/",
                        "value": f"dir:{path}",
                        "icon": "▣",
                        "children": kids,
                    }
                )
            else:
                out.append({"label": name, "value": f"file:{path}"})
        return out

    async def preview(self, shell: "KtuiShell", rel: str) -> None:
        path = (self.root / rel).resolve()
        if self.root not in path.parents:
            return
        try:
            with path.open("rb") as f:
                raw = f.read(PREVIEW_BYTES)
        except OSError as exc:
            shell.app.notify(f"{rel}: {exc.strerror or exc}", "warning")
            return
        if b"\0" in raw:
            body = f"binary file · {path.stat().st_size} bytes"
        else:
            lines = raw.decode("utf-8", errors="replace").splitlines()[:PREVIEW_LINES]
            fence = "````" if any(l.startswith("```") for l in lines) else "```"
            lang = path.suffix.lstrip(".")
            body = f"{fence}{lang}\n" + "\n".join(lines) + f"\n{fence}"
        await shell.panel(
            Card(title=rel, body=body, actions=[{"id": "close", "label": "Close"}])
        )
