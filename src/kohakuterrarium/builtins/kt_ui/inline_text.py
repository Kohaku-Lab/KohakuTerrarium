"""Text form of a reply with inline ``<ui>`` blocks, for terminal outputs.

The Rich CLI and the TUI print assistant text as Markdown; this replaces each
``<ui>`` block with the ``kt.ui`` text profile so raw tags never reach the
screen.
"""

from kohakuterrarium.builtins.kt_ui.fallback import render_fallback
from kohakuterrarium.builtins.kt_ui.markup import MarkupError, check_block
from kohakuterrarium.builtins.kt_ui.markup_lex import split_message
from kohakuterrarium.builtins.kt_ui.spec import SpecError

PLACEHOLDER = "\n_… building UI …_\n"
MAX_ERROR_CHARS = 200


def _block_text(source: str) -> str:
    try:
        validated = check_block(source)
    except (MarkupError, SpecError) as exc:
        reason = str(exc)
        if len(reason) > MAX_ERROR_CHARS:
            reason = reason[: MAX_ERROR_CHARS - 1] + "…"
        return f"\n_[UI block could not be shown: {reason}]_\n"
    body = render_fallback(validated)
    if not body:
        return ""
    fence = "~~~" if "```" in body else "```"
    return f"\n{fence}text\n{body}\n{fence}\n"


def inline_ui_text(text: str, *, final: bool = True) -> str:
    """``text`` with every ``<ui>`` block replaced by its text profile.

    While streaming (``final=False``) a block that has not closed yet shows a
    short placeholder instead of its partial tags.
    """
    if "<ui" not in text:
        return text
    parts: list[str] = []
    for segment in split_message(text):
        if segment["kind"] == "markdown":
            parts.append(segment["text"])
        elif not segment["closed"] and not final:
            parts.append(PLACEHOLDER)
        else:
            parts.append(_block_text(segment["source"]))
    return "".join(parts)


__all__ = ["PLACEHOLDER", "inline_ui_text"]
