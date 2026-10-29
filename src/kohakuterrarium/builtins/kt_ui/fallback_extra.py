"""Text forms of the ``kt.ui`` media, link and widget components (``fallback.py``)."""

import re
from typing import Any

from kohakuterrarium.builtins.kt_ui.expr import ExprError, interpolate, to_display

HTML_SUMMARY_CHARS = 60
_HIDDEN_HTML = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")

EXTRA_TYPES = frozenset(
    {"html", "math", "link", "icon", "gallery", "compare", "copy", "timer", "quiz"}
)


def _text(value: Any, scope: dict[str, Any]) -> str:
    if not isinstance(value, str):
        return to_display(value)
    try:
        return to_display(interpolate(value, scope))
    except ExprError:
        return "?"


def _list(value: Any, scope: dict[str, Any]) -> list:
    if isinstance(value, str):
        try:
            value = interpolate(value, scope)
        except ExprError:
            return []
    return value if isinstance(value, list) else []


def html_summary(source: Any, title: str = "") -> str:
    """``title``, else the page ``<title>``, else its first visible text."""
    if title:
        return title
    text = source if isinstance(source, str) else ""
    found = _TITLE.search(text)
    if found and found.group(1).strip():
        return _SPACE.sub(" ", found.group(1)).strip()[:HTML_SUMMARY_CHARS]
    visible = _SPACE.sub(
        " ", _TAG.sub(" ", _HIDDEN_HTML.sub(" ", _TITLE.sub(" ", text)))
    )
    return visible.strip()[:HTML_SUMMARY_CHARS] or "custom view"


def clock(seconds: Any) -> str:
    """``mm:ss`` (or ``h:mm:ss``) for a number of seconds."""
    total = int(seconds) if isinstance(seconds, (int, float)) else 0
    hours, rest = divmod(max(0, total), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _compare(node: dict, scope: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in _list(node.get("items"), scope):
        if not isinstance(item, dict):
            continue
        lines.append(_text(item.get("title"), scope))
        lines.extend(f"  + {_text(p, scope)}" for p in item.get("pros") or [])
        lines.extend(f"  - {_text(c, scope)}" for c in item.get("cons") or [])
        if item.get("verdict"):
            lines.append(f"  → {_text(item['verdict'], scope)}")
    return lines


def _gallery(node: dict, scope: dict[str, Any]) -> list[str]:
    items = _list(node.get("items"), scope)
    lines = [f"[gallery: {len(items)} image{'s' if len(items) != 1 else ''}]"]
    for item in items:
        if isinstance(item, dict) and (item.get("caption") or item.get("alt")):
            lines.append(f"- {_text(item.get('caption') or item.get('alt'), scope)}")
    return lines


def _quiz(node: dict, scope: dict[str, Any]) -> list[str]:
    lines = [_text(node.get("question"), scope)]
    for option in _list(node.get("options"), scope):
        label = (
            option.get("label", option.get("value"))
            if isinstance(option, dict)
            else option
        )
        lines.append(f"  ( ) {_text(label, scope)}")
    return lines


def extra_lines(node: dict, scope: dict[str, Any]) -> list[str]:
    """Lines for one node of a type in :data:`EXTRA_TYPES`."""
    match node.get("type"):
        case "html":
            title = _text(node.get("title", ""), scope)
            return [f"[HTML view: {html_summary(node.get('source'), title)}]"]
        case "math":
            return str(node.get("value", "")).splitlines()
        case "link":
            href = _text(node.get("href"), scope)
            label = _text(node.get("label", ""), scope)
            return [f"{label} ({href})" if label and label != href else href]
        case "icon":
            label = _text(node.get("label", ""), scope)
            return [label or f"[{node.get('name')}]"]
        case "gallery":
            return _gallery(node, scope)
        case "compare":
            return _compare(node, scope)
        case "copy":
            label = _text(node.get("label", ""), scope)
            value = _text(node.get("value"), scope)
            return [f"{label}: {value}" if label else value]
        case "timer":
            label = _text(node.get("label", ""), scope)
            mode = node.get("mode", "countdown")
            start = clock(node.get("seconds", 0) if mode == "countdown" else 0)
            return [f"{label + ': ' if label else ''}{start} ({mode})"]
        case "quiz":
            return _quiz(node, scope)
    return []


__all__ = ["EXTRA_TYPES", "clock", "extra_lines", "html_summary"]
