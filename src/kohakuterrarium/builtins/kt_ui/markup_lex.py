"""Lexer for ``kt.ui`` inline markup, and the splitter that finds ``<ui>`` blocks.

The markup is JSX-like tags written inside an assistant reply (see
``plans/chat-widgets/08-inline-ui.md``). :func:`lex` turns one block into
tokens; :func:`split_message` cuts a whole reply into Markdown and ``<ui>``
segments. ``ktui/markup.js`` in the frontend implements the same rules, and
both run ``tests/fixtures/kt_ui_markup.json``.
"""

import re
from dataclasses import dataclass, field
from typing import Any

TEXT_CHILD = {
    "text": "value",
    "heading": "value",
    "badge": "value",
    "stat": "value",
    "button": "label",
    "callout": "value",
    "quote": "value",
    "link": "label",
    "quiz": "question",
}
RAW_CHILD = {
    "code": "value",
    "diagram": "source",
    "html": "source",
    "math": "value",
    "copy": "value",
}
NESTED_RAW = frozenset({"html"})

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_ATTR = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_BLOCK_START = re.compile(r"<ui(?=[\s>/]|$)")
_FENCE = re.compile(r"(`{3,}|~{3,})")
_ANY_CLOSE = re.compile(r"</[A-Za-z_]")


class MarkupError(ValueError):
    """A ``<ui>`` block that cannot be turned into a spec."""


@dataclass
class Attr:
    """One attribute: ``kind`` is ``str`` (quoted), ``expr`` (braced) or ``bare``."""

    name: str
    kind: str
    value: str = ""


@dataclass
class Token:
    """``open`` / ``close`` / ``text`` with its source span."""

    kind: str
    start: int
    end: int
    name: str = ""
    attrs: list[Attr] = field(default_factory=list)
    self_closing: bool = False
    text: str = ""
    raw: bool = False


@dataclass
class Lexed:
    """Tokens of a block; ``incomplete`` names what the input ended inside."""

    tokens: list[Token]
    incomplete: str = ""


class _Incomplete(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def _read_quoted(text: str, i: int) -> int:
    """Index just past the string starting at ``text[i]`` (a quote)."""
    end = text.find(text[i], i + 1)
    if end < 0:
        raise _Incomplete("unterminated string")
    return end + 1


def read_braced(text: str, i: int) -> int:
    """Index just past the balanced ``{…}`` starting at ``text[i]``."""
    depth, j, n = 0, i, len(text)
    while j < n:
        ch = text[j]
        if ch in "\"'":
            end = text.find(ch, j + 1)
            if end < 0:
                raise _Incomplete("unterminated expression")
            j = end + 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise _Incomplete("unterminated expression")


def _open_tag(text: str, i: int) -> Token:
    n = len(text)
    match = _NAME.match(text, i + 1)
    assert match is not None
    token = Token("open", i, i, name=match.group(0))
    j = match.end()
    while True:
        while j < n and text[j] in " \t\r\n":
            j += 1
        if j >= n:
            raise _Incomplete("unfinished tag")
        if text.startswith("/>", j):
            token.self_closing, token.end = True, j + 2
            return token
        if text[j] == ">":
            token.end = j + 1
            return token
        attr = _ATTR.match(text, j)
        if attr is None:
            if text[j] == "/" and j + 1 >= n:
                raise _Incomplete("unfinished tag")
            raise MarkupError(f"bad character {text[j]!r} in <{token.name}>")
        j = attr.end()
        k = j
        while k < n and text[k] in " \t":
            k += 1
        if k >= n:
            raise _Incomplete("unfinished tag")
        if text[k] != "=":
            token.attrs.append(Attr(attr.group(0), "bare"))
            continue
        k += 1
        while k < n and text[k] in " \t":
            k += 1
        if k >= n:
            raise _Incomplete("unfinished tag")
        if text[k] in "\"'":
            end = _read_quoted(text, k)
            token.attrs.append(Attr(attr.group(0), "str", text[k + 1 : end - 1]))
        elif text[k] == "{":
            end = read_braced(text, k)
            token.attrs.append(Attr(attr.group(0), "expr", text[k + 1 : end - 1]))
        else:
            raise MarkupError(
                f"attribute {attr.group(0)!r} of <{token.name}> needs a quoted "
                "value or {expression}"
            )
        j = end


def _close_tag(text: str, i: int) -> Token:
    match = _NAME.match(text, i + 2)
    assert match is not None
    j = match.end()
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j >= len(text):
        raise _Incomplete("unfinished tag")
    if text[j] != ">":
        raise MarkupError(f"bad closing tag </{match.group(0)}")
    return Token("close", i, j + 1, name=match.group(0))


def _nested_end(text: str, i: int, name: str) -> int:
    """The matching close tag of ``name``, counting nested opens; else the end."""
    pattern = re.compile(rf"<(/?){re.escape(name)}(?=[\s>/])[^>]*>")
    depth = 1
    for match in pattern.finditer(text, i):
        if match.group(1):
            depth -= 1
            if depth == 0:
                return match.start()
        elif not match.group(0).endswith("/>"):
            depth += 1
    return len(text)


def _content_end(text: str, i: int, name: str) -> int:
    """Where the text of a text or raw element ends: its own close tag, else any."""
    if name in NESTED_RAW:
        return _nested_end(text, i, name)
    own = re.compile(rf"</{re.escape(name)}\s*>").search(text, i)
    if own is not None:
        return own.start()
    other = _ANY_CLOSE.search(text, i)
    return other.start() if other is not None else len(text)


def _is_tag_start(text: str, i: int) -> bool:
    nxt = text[i + 1 : i + 2]
    if nxt.isascii() and (nxt.isalpha() or nxt == "_"):
        return True
    after = text[i + 2 : i + 3]
    return nxt == "/" and after.isascii() and (after.isalpha() or after == "_")


def lex(text: str, start: int = 0, *, stop_at_ui_close: bool = False) -> Lexed:
    """Tokens from ``text[start:]``; stops after the ``</ui>`` closing the block.

    With ``stop_at_ui_close`` the lexer stops after the close tag that empties
    the element stack. Input that ends inside a tag or expression sets
    ``incomplete``; malformed tags raise :class:`MarkupError`.
    """
    tokens: list[Token] = []
    stack: list[str] = []
    i, n = start, len(text)
    try:
        while i < n:
            if text[i] == "<" and _is_tag_start(text, i):
                if text[i + 1] == "/":
                    token = _close_tag(text, i)
                    tokens.append(token)
                    i = token.end
                    if token.name in stack:
                        del stack[len(stack) - 1 - stack[::-1].index(token.name) :]
                    if stop_at_ui_close and not stack:
                        break
                    continue
                token = _open_tag(text, i)
                tokens.append(token)
                i = token.end
                if token.self_closing:
                    if stop_at_ui_close and not stack:
                        break
                    continue
                stack.append(token.name)
                if token.name in TEXT_CHILD or token.name in RAW_CHILD:
                    end = _content_end(text, i, token.name)
                    if end > i:
                        tokens.append(
                            Token(
                                "text",
                                i,
                                end,
                                text=text[i:end],
                                raw=token.name in RAW_CHILD,
                            )
                        )
                    i = end
                continue
            nxt = text.find("<", i + 1)
            while nxt >= 0 and not _is_tag_start(text, nxt):
                nxt = text.find("<", nxt + 1)
            end = n if nxt < 0 else nxt
            tokens.append(Token("text", i, end, text=text[i:end]))
            i = end
    except _Incomplete as exc:
        return Lexed(tokens, exc.what)
    return Lexed(tokens)


def _block_end(text: str, start: int) -> tuple[int, bool]:
    try:
        lexed = lex(text, start, stop_at_ui_close=True)
    except MarkupError:
        close = text.find("</ui>", start)
        return (close + 5, True) if close >= 0 else (len(text), False)
    if lexed.incomplete or not lexed.tokens:
        return len(text), False
    last = lexed.tokens[-1]
    first = lexed.tokens[0]
    if last.kind == "close" and last.name == "ui":
        return last.end, True
    if first.self_closing and len(lexed.tokens) == 1:
        return first.end, True
    return len(text), False


def split_message(text: str) -> list[dict[str, Any]]:
    """Cut a reply into ``markdown`` and ``ui`` segments (``source``, ``closed``).

    A block starts with ``<ui`` at the start of a line (up to three spaces of
    indent) outside fenced code; an open block runs to the end of the text.
    """
    segments: list[dict[str, Any]] = []
    md_start, i, n = 0, 0, len(text)
    fence = ""
    at_line_start = True
    while i < n:
        line_end = text.find("\n", i)
        line_end = n if line_end < 0 else line_end
        line = text[i:line_end]
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if at_line_start and indent <= 3:
            marker = _FENCE.match(stripped)
            if fence:
                if marker and marker.group(1)[0] == fence[0]:
                    if (
                        len(marker.group(1)) >= len(fence)
                        and not stripped[marker.end() :].strip()
                    ):
                        fence = ""
            elif marker:
                fence = marker.group(1)
            elif _BLOCK_START.match(stripped):
                start = i + indent
                end, closed = _block_end(text, start)
                if i > md_start:
                    segments.append({"kind": "markdown", "text": text[md_start:i]})
                segments.append(
                    {"kind": "ui", "source": text[start:end], "closed": closed}
                )
                md_start = i = end
                at_line_start = False
                continue
        i = line_end + 1
        at_line_start = True
    if md_start < n:
        segments.append({"kind": "markdown", "text": text[md_start:]})
    return segments


__all__ = [
    "Attr",
    "Lexed",
    "MarkupError",
    "NESTED_RAW",
    "RAW_CHILD",
    "TEXT_CHILD",
    "Token",
    "lex",
    "read_braced",
    "split_message",
]
