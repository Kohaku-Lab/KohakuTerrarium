"""Built-in bodies for file tool calls, as the web's tool bodies: ``edit`` and ``multi_edit``
show their change as a diff (+ / − lines with numbers), ``write`` the written file as added
lines. Any other tool keeps its output box. Built from the call's arguments, so a resumed
session shows the same diffs. Pictures (an image a reply or a tool produced, an attached one)
are drawn in half blocks."""

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kohakutui.ui import Cell, Component, Diff, Image, Stack, Text

from kohakuterrarium.llm.media.reference import artifact_reference, session_media_path

DIFF_LINES = 80


def tool_body(name: str, args: Any, output: str) -> Component | None:
    """The body for a finished call of ``name``, or None for the plain output box."""
    if not isinstance(args, dict):
        return None
    path = str(args.get("path", "") or "")
    match name:
        case "edit" if args.get("diff"):
            return Diff(text=str(args["diff"]), path=path, max_lines=DIFF_LINES)
        case "edit" if "old" in args:
            new = str(args.get("new", ""))
            return Diff(
                old=str(args.get("old", "")),
                new=new,
                path=_name(path),
                start=_line_of(path, new),
                max_lines=DIFF_LINES,
            )
        case "multi_edit" if isinstance(args.get("edits"), list):
            edits = [e for e in args["edits"] if isinstance(e, dict) and "old" in e]
            if not edits:
                return None
            # the path titles the first change; each change is its own short diff
            diffs: list[Component] = [
                Diff(
                    old=str(e.get("old", "")),
                    new=str(e.get("new", "")),
                    path=_name(path) if i == 0 else None,
                    start=_line_of(path, str(e.get("new", ""))),
                    max_lines=DIFF_LINES // 2,
                )
                for i, e in enumerate(edits)
            ]
            return Stack(children=diffs)
        case "write" if "content" in args:
            return Diff(
                old="",
                new=str(args.get("content", "")),
                path=_name(path),
                max_lines=DIFF_LINES // 2,
            )
    return None


# files larger than this are not read to number a diff
NUMBER_MAX_BYTES = 2 * 1024 * 1024


def _name(path: str) -> str | None:
    """The file name: the call's header already shows the whole path."""
    return Path(path).name or None if path else None


def _line_of(path: str, text: str) -> int:
    """The line of ``path`` where ``text`` now starts (1 when unknown)."""
    if not path or not text:
        return 1
    try:
        file = Path(path)
        if file.stat().st_size > NUMBER_MAX_BYTES:
            return 1
        content = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 1
    at = content.find(text)
    return content.count("\n", 0, at) + 1 if at >= 0 else 1


IMAGE_WIDTH = 48


@dataclass
class MediaPlace:
    """Where a conversation's media live: its session file (artifact URLs such as
    ``/api/sessions/<name>/artifacts/...`` are files beside it) and, attached to a server, the
    server's address (artifacts not on this machine open from there)."""

    session_path: str | None = None
    server: str | None = None


def media_path(url: str, place: MediaPlace | None = None) -> Path | None:
    """The local file a media URL names (a path, ``file://`` or a session artifact)."""
    if url.startswith(("http://", "https://", "data:")):
        return None
    try:
        if artifact_reference(url) is not None:
            if place is None or not place.session_path:
                return None
            found = session_media_path(url, session_path=Path(place.session_path))
            return found if found is not None and found.is_file() else None
    except ValueError:
        return None
    path = Path(url.removeprefix("file://"))
    return path if path.is_file() else None


def _target(url: str, path: Path | None, place: MediaPlace | None) -> str:
    """What a click on the media's line opens."""
    if path is not None:
        return path.resolve().as_uri()
    if url.startswith("/") and place is not None and place.server:
        return place.server.rstrip("/") + url
    return url


def image_cell(
    url: str,
    name: str = "",
    width: int = IMAGE_WIDTH,
    place: MediaPlace | None = None,
) -> Cell:
    """A transcript cell for a picture: drawn from a data URL or a local file (a session
    artifact included), with a link to open it; else only the link."""
    source: Any = None
    path = None
    label = name or "image"
    if url.startswith("data:") and "," in url:
        try:
            source = base64.b64decode(url.split(",", 1)[1], validate=False)
        except (binascii.Error, ValueError):
            source = None
    else:
        path = media_path(url, place)
        if path is not None:
            source, label = path, name or path.name
    children: list[Component] = []
    if source is not None:
        children.append(Image.from_file(source, width=width, alt=label))
    if not url.startswith("data:"):
        target = _target(url, path, place)
        children.append(Text(f"{label} · open", link=target, tone="muted"))
    return Cell(role="system", icon="🖼", title=label, children=children)


def media_cell(url: str, name: str, kind: str, place: MediaPlace | None = None) -> Cell:
    """Video or audio: no player in a terminal, so its file as a link to open elsewhere."""
    icon = "▶" if kind == "video" else "♪"
    path = media_path(url, place)
    target = _target(url, path, place)
    label = name or (path.name if path else url.rsplit("/", 1)[-1]) or kind
    shown = str(path) if path is not None else url
    children: list[Component] = [
        Text(f"{shown} · open it in a player", link=target, tone="muted")
    ]
    return Cell(role="system", icon=icon, title=f"{kind} · {label}", children=children)


def media_of(content: Any) -> list[tuple[str, str, str]]:
    """(kind, url, name) of the pictures, videos and sounds in tool output parts."""
    if not isinstance(content, list):
        return []
    out = [("image", url, name) for url, name in images_of(content)]
    for part in content:
        file = part.get("file") if isinstance(part, dict) else None
        if not isinstance(file, dict) or part.get("type") != "file":
            continue
        mime = str(file.get("mime") or "")
        kind = mime.split("/", 1)[0]
        url = str(file.get("path") or "")
        if kind in ("video", "audio", "image") and url:
            out.append((kind, url, str(file.get("name") or "")))
    return out


def images_of(content: Any) -> list[tuple[str, str]]:
    """(url, name) of the image parts of message content (parts or their dicts)."""
    if not isinstance(content, list):
        return []
    out = []
    for part in content:
        if isinstance(part, dict):
            if part.get("type") != "image_url":
                continue
            image = part.get("image_url") or {}
            meta = part.get("meta") or {}
            url = image.get("url", "") if isinstance(image, dict) else str(image)
            name = meta.get("source_name") or part.get("source_name") or ""
        elif getattr(part, "type", "") == "image_url":
            url, name = getattr(part, "url", ""), getattr(part, "source_name", "") or ""
        else:
            continue
        if url:
            out.append((str(url), str(name)))
    return out
