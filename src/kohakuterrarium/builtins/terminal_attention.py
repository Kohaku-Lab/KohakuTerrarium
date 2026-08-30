"""Small, conservative terminal attention indicator."""

import os
import re
import sys
from typing import TextIO

_CONTROL = re.compile(r"[\x00-\x1f\x7f\x1b\x9b\x9c\x9d]")


def safe_title(value: str) -> str:
    """Remove control characters which could terminate/nest terminal controls."""
    return _CONTROL.sub("", str(value)).replace("\r", "").replace("\n", " ")


def set_terminal_title(title: str, *, stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("TERM", "") == "dumb" or not stream.isatty():
        return False
    stream.write(f"\x1b]2;{safe_title(title)}\x1b\\")
    stream.flush()
    return True


def set_attention(
    status: str,
    *,
    app: str = "KohakuTerrarium",
    base_title: str | None = None,
    stream: TextIO | None = None,
) -> bool:
    label = {
        "working": "working",
        "input required": "input required",
        "ready": "ready",
        "idle": "idle",
    }.get(status, "ready")
    title = base_title if base_title is not None else app
    return set_terminal_title(f"{title} - {label}", stream=stream)
