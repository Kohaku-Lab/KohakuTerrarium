"""Shared interactive reply submission for the TUI renderer."""

from __future__ import annotations

from typing import Any

from kohakuterrarium.modules.output.event import UIReply


def submit_reply(router: Any, reply: UIReply) -> bool:
    """Submit through either router API and normalize its accepted status."""
    submit = getattr(router, "submit_reply_with_status", None)
    if callable(submit):
        result = submit(reply)
        return bool(result[0]) if isinstance(result, tuple) else bool(result)
    return router.submit_reply(reply) is not False
