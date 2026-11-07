"""The plain state a :class:`CreatureView` keeps beside its document: token usage, running
jobs, and the messages the message actions (copy, edit, retry, versions) act on."""

import time
from dataclasses import dataclass, field
from typing import Any

from kohakutui.ui import Node


@dataclass
class Usage:
    """Token and context figures for the footer."""

    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached: int = 0
    last_prompt: int = 0
    max_context: int = 0

    def context_pct(self) -> int | None:
        if not self.max_context or not self.last_prompt:
            return None
        return min(100, self.last_prompt * 100 // self.max_context)


# keys on a message in full-screen navigation (ctrl+↑ or a click): copy, edit, retry, versions
USER_ACTIONS = [
    {"key": "c", "label": "copy", "value": "msg:copy"},
    {"key": "e", "label": "edit", "value": "msg:edit"},
    {"key": "r", "label": "retry", "value": "msg:retry"},
]
REPLY_ACTIONS = [
    {"key": "c", "label": "copy", "value": "msg:copy"},
    {"key": "r", "label": "retry", "value": "msg:retry"},
]
VERSION_ACTIONS = [
    {"key": "[", "label": "older version", "value": "msg:prev"},
    {"key": "]", "label": "newer version", "value": "msg:next"},
]


@dataclass
class Message:
    """A user message or reply in the transcript: what copy / edit / retry act on."""

    kind: str  # "user" | "assistant"
    text: str
    turn: int | None
    node: Node


@dataclass
class Job:
    """A running tool or sub-agent, foreground or background."""

    job_id: str
    name: str
    kind: str
    detail: str = ""
    background: bool = False
    started: float = field(default_factory=time.monotonic)
    # the call's arguments (file tools draw their diff from them)
    args: Any = None
