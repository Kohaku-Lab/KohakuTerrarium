"""Shape checks for ``kt.ui`` list items and whole nodes, used by ``spec.py``.

Item checks return a problem text or ``None``; node checks return
``(prop, problem)`` pairs for rules that span several props of one node.
"""

import json
import re
from typing import Any

from kohakuterrarium.builtins.kt_ui.components import COLUMN_FORMATS, STEP_STATUS
from kohakuterrarium.builtins.kt_ui.expr import template_expression

MAX_TREE_NODES = 500
MAX_TREE_DEPTH = 12
MAX_GALLERY_ITEMS = 48
MAX_COMPARE_ITEMS = 6
MAX_HTML_BYTES = 48 * 1024
RESERVED_STATE_PREFIX = "__"

_OUTWARD_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def is_safe_src(value: Any) -> bool:
    """An image source the client may load: a ``data:image/`` URL or a same-origin path."""
    return isinstance(value, str) and (
        value.startswith("data:image/")
        or (value.startswith("/") and not value.startswith("//"))
    )


def is_outward_url(value: Any) -> bool:
    """An ``http(s)://`` link with no spaces or quotes."""
    return isinstance(value, str) and bool(_OUTWARD_URL.fullmatch(value))


def _option_shape(item: Any) -> str | None:
    if isinstance(item, str) or (isinstance(item, dict) and "value" in item):
        return None
    return "an option is a string or {value, label}"


def _field_shape(item: Any) -> str | None:
    if isinstance(item, dict) and "label" in item and "value" in item:
        return None
    return "a field is {label, value}"


def _column_shape(item: Any) -> str | None:
    if isinstance(item, str):
        return None
    if not isinstance(item, dict) or "key" not in item:
        return "a column is a key string or {key, label, align, format}"
    if item.get("format", "text") not in COLUMN_FORMATS:
        return f"a column format is one of {', '.join(COLUMN_FORMATS)}"
    digits = item.get("digits", 0)
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 10:
        return "a column's digits is a whole number 0-10"
    return None


def _step_shape(item: Any) -> str | None:
    if isinstance(item, str):
        return None
    if not isinstance(item, dict) or "title" not in item:
        return "a step is a string or {title, detail, status}"
    if item.get("status", "todo") not in STEP_STATUS:
        return f"a step status is one of {', '.join(STEP_STATUS)}"
    return None


def _event_shape(item: Any) -> str | None:
    if isinstance(item, str) or (isinstance(item, dict) and "title" in item):
        return None
    return "a timeline entry is a string or {title, date, detail, tone}"


def _tree_size(items: list) -> str | None:
    count, stack = 0, list(items)
    while stack:
        item = stack.pop()
        count += 1
        if count > MAX_TREE_NODES:
            return f"a tree has at most {MAX_TREE_NODES} nodes"
        if isinstance(item, dict) and isinstance(item.get("children"), list):
            stack.extend(item["children"])
    return None


def _tree_shape(item: Any, depth: int = 0) -> str | None:
    if depth > MAX_TREE_DEPTH:
        return f"a tree is at most {MAX_TREE_DEPTH} levels deep"
    if isinstance(item, str):
        return None
    if not isinstance(item, dict) or "label" not in item:
        return "a tree node is a string or {label, children, badge, open}"
    children = item.get("children", [])
    if not isinstance(children, list):
        return "a tree node's children is an array of nodes"
    for child in children:
        problem = _tree_shape(child, depth + 1)
        if problem:
            return problem
    return None


def _series_shape(item: Any) -> str | None:
    if isinstance(item, dict) and "data" in item:
        return None
    return "a series is {name, data}"


def _gallery_shape(item: Any) -> str | None:
    src = (
        item
        if isinstance(item, str)
        else (item or {}).get("src") if isinstance(item, dict) else None
    )
    if src is None:
        return "a gallery item is a src string or {src, alt, caption}"
    if template_expression(src) is None and not is_safe_src(src):
        return "a gallery src is a data:image/ URL or a same-origin path"
    return None


def _gallery_size(items: list) -> str | None:
    if len(items) > MAX_GALLERY_ITEMS:
        return f"a gallery has at most {MAX_GALLERY_ITEMS} images"
    return None


def _compare_shape(item: Any) -> str | None:
    if not isinstance(item, dict) or "title" not in item:
        return "a compare item is {title, pros, cons, verdict}"
    for key in ("pros", "cons"):
        value = item.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"a compare item's {key} is a list of strings"
    return None


def _compare_size(items: list) -> str | None:
    if not 1 <= len(items) <= MAX_COMPARE_ITEMS:
        return f"compare holds 1-{MAX_COMPARE_ITEMS} items"
    return None


def _option_values(options: Any) -> list[Any] | None:
    if not isinstance(options, list):
        return None
    return [o.get("value") if isinstance(o, dict) else o for o in options]


def _quiz_checks(node: dict) -> list[tuple[str, str]]:
    values = _option_values(node.get("options"))
    answer = node.get("answer")
    if values is None or not isinstance(answer, str):
        return []
    if answer not in [str(v) for v in values]:
        return [("answer", "must be the value of one of the options")]
    return []


def _timer_checks(node: dict) -> list[tuple[str, str]]:
    if node.get("mode", "countdown") == "countdown" and "seconds" not in node:
        return [("seconds", "a countdown needs 'seconds'")]
    return []


def _html_checks(node: dict) -> list[tuple[str, str]]:
    source = node.get("source")
    if isinstance(source, str):
        size = len(source.encode("utf-8"))
        if size > MAX_HTML_BYTES:
            return [("source", f"is {size} bytes; the limit is {MAX_HTML_BYTES}")]
    return []


def _form_checks(node: dict) -> list[tuple[str, str]]:
    if "send" not in node and "message" not in node:
        return [("send", "a form needs 'send' or 'message'")]
    return []


NODE_LIST_CHECKS = {
    ("tree", "items"): _tree_size,
    ("gallery", "items"): _gallery_size,
    ("compare", "items"): _compare_size,
}

NODE_ITEM_SHAPES = {
    ("gallery", "items"): _gallery_shape,
    ("compare", "items"): _compare_shape,
    ("quiz", "options"): _option_shape,
    ("steps", "items"): _step_shape,
    ("timeline", "items"): _event_shape,
    ("tree", "items"): _tree_shape,
}

ITEM_SHAPES = {
    "options": _option_shape,
    "fields": _field_shape,
    "columns": _column_shape,
    "series": _series_shape,
}

NODE_CHECKS = {
    "form": _form_checks,
    "html": _html_checks,
    "quiz": _quiz_checks,
    "timer": _timer_checks,
}


def state_name_problem(name: str) -> str | None:
    """Why a ``state`` key is reserved, or ``None``."""
    if name.startswith(RESERVED_STATE_PREFIX):
        return f"names starting with {RESERVED_STATE_PREFIX!r} are reserved"
    return None


def json_size(value: Any) -> int:
    """UTF-8 size of ``value`` as compact JSON."""
    return len(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8")
    )


__all__ = [
    "ITEM_SHAPES",
    "MAX_HTML_BYTES",
    "MAX_TREE_DEPTH",
    "MAX_TREE_NODES",
    "NODE_CHECKS",
    "NODE_ITEM_SHAPES",
    "NODE_LIST_CHECKS",
    "RESERVED_STATE_PREFIX",
    "is_outward_url",
    "is_safe_src",
    "json_size",
    "state_name_problem",
]
