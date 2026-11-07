"""The composer's helpers: ``@`` completion, attachments, pasted images, the external editor.

* ``@`` completes creature names (several creatures) and paths under the focused creature's
  working directory; a folder keeps completing.
* Files become attachments, as the web's paperclip: every ``@path`` in a message that names
  an existing file, ``/attach <path>``, and alt+v (an image on the clipboard). They show as
  chips on the input box; backspace in an empty input drops the last one. On send they become
  content parts the way the web builds them: images as ``image_url`` data URLs, text files
  inline, other files by path.
* ctrl+g opens the draft in ``$VISUAL`` / ``$EDITOR``.
* Each conversation keeps its own unsent draft: switching creature or channel swaps it.
"""

import asyncio
import base64
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart
from kohakuterrarium.utils.logging import get_logger

try:  # optional: alt+v reads an image from the clipboard
    from PIL import ImageGrab
except ImportError:  # pragma: no cover - depends on the environment
    ImageGrab = None

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import Entry, KtuiShell

logger = get_logger(__name__)

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_SUGGESTIONS = 40
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".csv", ".tsv", ".html", ".css", ".xml", ".sh", ".rs", ".go", ".java",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".vue", ".sql", ".log",
}  # fmt: skip
TEXT_MIMES = {"application/json", "application/xml", "application/javascript"}


def working_dir(agent: Any) -> Path:
    """The creature's working directory (its workspace helper, else the executor's)."""
    ws = getattr(agent, "workspace", None)
    try:
        path = (
            ws.get() if ws is not None else getattr(agent.executor, "_working_dir", "")
        )
    except Exception:
        path = ""
    return Path(path or os.getcwd())


def path_suggestions(root: Path, query: str) -> list[dict]:
    """Entries under ``root`` completing ``query`` (``src/ma`` → ``src/main.py``)."""
    folder, _, stem = query.replace("\\", "/").rpartition("/")
    base = (root / folder) if folder else root
    try:
        entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    out = []
    for entry in entries:
        name = entry.name
        if name.startswith(".") and not stem.startswith("."):
            continue
        if stem and not name.lower().startswith(stem.lower()):
            continue
        rel = f"{folder}/{name}" if folder else name
        is_dir = entry.is_dir()
        out.append(
            {
                "label": rel + ("/" if is_dir else ""),
                "value": rel + ("/" if is_dir else ""),
                "detail": "folder" if is_dir else _size(entry),
            }
        )
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def _size(path: Path) -> str:
    try:
        n = path.stat().st_size
    except OSError:
        return ""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def mentioned_files(text: str, root: Path) -> list[Path]:
    """Existing files named by ``@path`` words in ``text``."""
    out = []
    for word in text.split():
        if not word.startswith("@") or len(word) < 2:
            continue
        candidate = Path(word[1:].rstrip(".,;:!?)"))
        path = candidate if candidate.is_absolute() else root / candidate
        if path.is_file() and path not in out:
            out.append(path)
    return out


def to_part(path: Path) -> Any:
    """A content part for ``path``, as the web builds one; raises ValueError when too big."""
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime.startswith("image/") and mime != "image/svg+xml":
        if size > MAX_IMAGE_BYTES:
            raise ValueError(f"{path.name}: images are limited to 5 MB")
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return ImagePart(
            url=f"data:{mime};base64,{data}",
            detail="low",
            source_type="attachment",
            source_name=path.name,
        )
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"{path.name}: attachments are limited to 10 MB")
    if (
        mime.startswith("text/")
        or mime in TEXT_MIMES
        or path.suffix.lower() in TEXT_SUFFIXES
    ):
        return FilePart(
            name=path.name,
            mime=mime,
            content=path.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
            is_inline=True,
        )
    return FilePart(path=str(path), name=path.name, mime=mime)


class ComposerHelper:
    """Answers the composer's ``change`` events and keeps the attachments of the next message."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.files: list[Path] = []
        # what was typed and not sent, per conversation (a creature, or ``ch:<name>``)
        self.drafts: dict[str, str] = {}
        self._showing: str | None = None
        # the draft put aside while a sent message is edited (conversation, text)
        self._stash: tuple[str, str] | None = None
        # a submit clears the box: that report belongs to the conversation it was sent in
        self._sent = False

    def _conversation(self) -> str:
        channel = self.shell.chrome.channel
        return f"ch:{channel.name}" if channel is not None else self.shell.focus_id

    def follow_conversation(self) -> None:
        """Another conversation shown: its own draft goes into the composer (an edit of a
        sent message in the one left is given up)."""
        key = self._conversation()
        if key == self._showing:
            return
        if self.shell.messages.editing is not None:
            self.shell.messages.editing = None
            self.shell.app.notify("edit cancelled", "info")
            self.end_edit(show=False)
        if self._showing is not None and self.shell.composer is not None:
            self.shell.composer.update(value=self.drafts.get(key, ""))
        self._showing = key

    def set_text(self, text: str) -> None:
        """Puts ``text`` in the composer as this conversation's draft."""
        if self.shell.composer is not None:
            self.shell.composer.update(value=text)
        self.drafts[self._conversation()] = text

    def begin_edit(self) -> None:
        """A sent message is being edited: the draft waits until the edit ends."""
        key = self._conversation()
        if self._stash is None:
            self._stash = (key, self.drafts.get(key, ""))

    def end_edit(self, show: bool = True) -> None:
        """The edit ended: the draft put aside comes back."""
        if self._stash is None:
            return
        key, text = self._stash
        self._stash = None
        if show and key == self._conversation():
            self.set_text(text)
        else:
            self.drafts[key] = text

    def submitted(self) -> None:
        self.drafts[self._showing or self._conversation()] = ""
        self._sent = True

    def root(self) -> Path:
        return working_dir(self.shell.focused.agent)

    def handle_change(self, value: Any) -> bool:
        """A composer ``change``; True when handled."""
        if not isinstance(value, dict):
            return False
        if "complete" in value:
            self._complete(value["complete"] or {})
        elif "detach" in value:
            index = int(value["detach"] or 0)
            if 0 <= index < len(self.files):
                self.files.pop(index)
                self.show()
        elif "edit" in value:
            self.shell.spawn(self._edit(str(value["edit"] or "")))
        elif "draft" in value:
            text = str(value["draft"] or "")
            if self._sent and not text:
                # the box cleared by a send: already recorded where it was sent
                self._sent = False
            else:
                self._sent = False
                self.drafts[self._showing or self._conversation()] = text
        else:
            return False
        return True

    def _complete(self, ask: dict) -> None:
        query = str(ask.get("query", ""))
        shell = self.shell
        items: list[dict] = []
        if shell.multi and "/" not in query:
            for entry in shell.entries.values():
                name = entry.creature.name
                if name.lower().startswith(query.lower()):
                    items.append({"label": name, "value": name, "detail": "creature"})
        items += path_suggestions(self.root(), query)
        shell.composer.update(suggestions=items)

    async def _edit(self, text: str) -> None:
        edited = await asyncio.to_thread(self.shell.app.edit_text, text)
        if edited is None:
            self.shell.app.notify("the editor did not save (set $EDITOR)", "warning")
            return
        self.set_text(edited.rstrip("\n"))
        self.shell.composer.focus()

    def attach(self, raw: str) -> None:
        """``/attach <path>``: a file for the next message."""
        raw = raw.strip().strip('"')
        if not raw:
            self.shell.app.notify("usage: /attach <path>", "info")
            return
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.root() / path
        if not path.is_file():
            self.shell.app.notify(f"no file {raw}", "error")
            return
        self.add(path)

    def add(self, path: Path) -> None:
        if path not in self.files:
            self.files.append(path)
        self.show()

    def paste_image(self) -> None:
        """alt+v: the clipboard's image, saved to a temporary file, as an attachment."""
        if ImageGrab is None:
            self.shell.app.notify("pasting images needs Pillow", "warning")
            return
        try:
            image = ImageGrab.grabclipboard()
        except Exception as exc:
            self.shell.app.notify(f"no image on the clipboard ({exc})", "warning")
            return
        if isinstance(image, list):  # files copied in a file manager
            for name in image:
                if Path(name).is_file():
                    self.add(Path(name))
            return
        if image is None:
            self.shell.app.notify("no image on the clipboard", "info")
            return
        fd, name = tempfile.mkstemp(suffix=".png", prefix="ktui-paste-")
        os.close(fd)
        image.save(name, "PNG")
        self.add(Path(name))

    def show(self) -> None:
        if self.shell.composer is not None:
            self.shell.composer.update(attachments=[p.name for p in self.files])

    def content(self, text: str, entry: "Entry") -> str | list:
        """The message to send: plain text, or text plus the attached and mentioned files."""
        files = list(self.files)
        for path in mentioned_files(text, working_dir(entry.agent)):
            if path not in files:
                files.append(path)
        self.files.clear()
        self.show()
        if not files:
            return text
        parts: list[Any] = [TextPart(text=text)] if text.strip() else []
        for path in files:
            try:
                parts.append(to_part(path))
            except (OSError, ValueError) as exc:
                self.shell.app.notify(str(exc), "warning")
        return parts if len(parts) > (1 if text.strip() else 0) else text
