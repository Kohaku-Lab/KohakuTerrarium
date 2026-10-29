"""Convert one ``<ui>`` block of inline markup into a ``kt.ui`` v1 spec.

The rules are in ``plans/chat-widgets/08-inline-ui.md``; the prop kinds in
``components.py`` decide how each attribute converts. The result goes through
the normal :func:`~kohakuterrarium.builtins.kt_ui.spec.validate_spec`.
"""

import json
import math
import re
import textwrap
from typing import Any

from kohakuterrarium.builtins.kt_ui.components import COMMON_PROPS, COMPONENTS
from kohakuterrarium.builtins.kt_ui.expr import ExprError, evaluate, free_names, parse
from kohakuterrarium.builtins.kt_ui.markup_lex import (
    RAW_CHILD,
    TEXT_CHILD,
    Attr,
    MarkupError,
    _Incomplete,
    lex,
    read_braced,
    split_message,
)
from kohakuterrarium.builtins.kt_ui.spec import ValidatedSpec, validate_spec
from kohakuterrarium.builtins.kt_ui.values import number_text

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SEND_TO_MESSAGE = frozenset({"button", "form"})
_DECLARATIONS = ("state", "let")


def _plain(value: Any) -> Any:
    """Whole-number floats as ints, recursively, for a readable spec."""
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        return int(value) if abs(value) < 2**53 else value
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _parse(source: str, where: str) -> tuple:
    try:
        return parse(source)
    except ExprError as exc:
        raise MarkupError(f"bad expression {source!r} in {where}: {exc}") from exc


def _constant(source: str, where: str) -> tuple[bool, Any]:
    """``(True, value)`` for an expression with no names, else ``(False, None)``."""
    node = _parse(source, where)
    if free_names(node):
        return False, None
    try:
        return True, _plain(evaluate(node))
    except ExprError as exc:
        raise MarkupError(f"bad expression {source!r} in {where}: {exc}") from exc


def unparse(node: tuple) -> str:
    """Expression source text for a parsed AST (fully parenthesized)."""
    match node[0]:
        case "lit":
            value = node[1]
            if isinstance(value, bool) or value is None:
                return json.dumps(value)
            if isinstance(value, float):
                return number_text(value)
            return json.dumps(value, ensure_ascii=False)
        case "name":
            return node[1]
        case "list":
            return "[" + ", ".join(unparse(item) for item in node[1]) + "]"
        case "obj":
            parts = []
            for key, value in node[1]:
                shown = key if _IDENT.fullmatch(key) else json.dumps(key)
                parts.append(f"{shown}: {unparse(value)}")
            return "{" + ", ".join(parts) + "}"
        case "call":
            return f"{node[1]}(" + ", ".join(unparse(a) for a in node[2]) + ")"
        case "member":
            return f"{unparse(node[1])}.{node[2]}"
        case "index":
            return f"{unparse(node[1])}[{unparse(node[2])}]"
        case "neg":
            return f"-({unparse(node[1])})"
        case "pos":
            return f"+({unparse(node[1])})"
        case "not":
            return f"not ({unparse(node[1])})"
        case "and" | "or":
            return f"({unparse(node[1])} {node[0]} {unparse(node[2])})"
        case "cond":
            return f"({unparse(node[1])} ? {unparse(node[2])} : {unparse(node[3])})"
        case "bin":
            return f"({unparse(node[2])} {node[1]} {unparse(node[3])})"
    raise MarkupError("bad expression node")


def _clean(text: str) -> str:
    """Drop blank edge lines, dedent and trim."""
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return textwrap.dedent("\n".join(lines)).strip()


def _template(text: str, closed: bool) -> str:
    """Markup text with ``{expr}`` to spec text with ``{{ expr }}``."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("{{", i):
            out.append("{")
            i += 2
        elif text.startswith("}}", i):
            out.append("}")
            i += 2
        elif text[i] == "{":
            try:
                end = read_braced(text, i)
            except _Incomplete as exc:
                if closed:
                    raise MarkupError(f"{exc.what} in text {text!r}") from None
                return "".join(out).rstrip()
            out.append("{{ " + text[i + 1 : end - 1].strip() + " }}")
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


class _Converter:
    def __init__(self, closed: bool) -> None:
        self.closed = closed

    def attr(self, node_type: str, attr: Attr) -> tuple[str, Any]:
        name = attr.name
        if name == "send" and node_type in _SEND_TO_MESSAGE:
            name = "message"
        spec = {**COMMON_PROPS, **COMPONENTS[node_type].props}.get(name)
        kind = spec.kind if spec is not None else "text"
        where = f"<{node_type} {attr.name}>"
        if kind in ("children", "tabs", "child"):
            raise MarkupError(f"{where} is set by child elements")
        if attr.kind == "bare":
            return name, True
        if kind == "expr":
            return name, attr.value.strip()
        if kind == "setmap":
            if attr.kind != "expr":
                raise MarkupError(f"{where} needs {{key: expression, …}}")
            node = _parse(attr.value, where)
            if node[0] != "obj":
                raise MarkupError(f"{where} needs {{key: expression, …}}")
            return name, {key: unparse(value) for key, value in node[1]}
        if attr.kind == "str":
            templated = kind in ("text", "url")
            return name, (_template(attr.value, True) if templated else attr.value)
        ok, value = _constant(attr.value, where)
        return name, value if ok else "{{ " + attr.value.strip() + " }}"

    def text_node(self, text: str) -> dict[str, Any] | None:
        cleaned = _clean(text)
        if not cleaned:
            return None
        return {"type": "text", "value": _template(cleaned, self.closed)}

    def node(self, name: str, attrs: list[Attr], children: list) -> dict[str, Any]:
        if name in _DECLARATIONS:
            raise MarkupError(f"<{name}> belongs directly inside <ui>")
        if name == "ui":
            raise MarkupError("<ui> cannot be nested")
        node: dict[str, Any] = {"type": name}
        component = COMPONENTS.get(name)
        for attr in attrs:
            if component is None:
                node[attr.name] = (
                    attr.value if attr.kind != "bare" else True
                )  # validator reports the unknown type
                continue
            key, value = self.attr(name, attr)
            node[key] = value
        if any(isinstance(c, tuple) and c[0] == "decl" for c in children):
            raise MarkupError("<state> and <let> belong directly inside <ui>")
        texts = [c for c in children if isinstance(c, tuple)]
        nodes = [c for c in children if isinstance(c, dict)]
        target = TEXT_CHILD.get(name) or RAW_CHILD.get(name)
        if target is not None:
            content = _clean("".join(t[1] for t in texts))
            if content:
                node[target] = (
                    content if name in RAW_CHILD else _template(content, self.closed)
                )
            return node
        kinds = {p.kind: k for k, p in (component.props if component else {}).items()}
        items = self.items(children)
        if "children" in kinds or "tabs" in kinds or component is None:
            if items or component is not None:
                node["children"] = items
        elif "child" in kinds:
            if len(items) == 1:
                node["child"] = items[0]
            elif items:
                node["child"] = {"type": "stack", "gap": "sm", "children": items}
        elif nodes or any(_clean(t[1]) for t in texts):
            raise MarkupError(f"<{name}> takes no children")
        return node

    def items(self, children: list) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for child in children:
            if isinstance(child, dict):
                out.append(child)
            else:
                node = self.text_node(child[1])
                if node is not None:
                    out.append(node)
        return out

    def root(self, attrs: list[Attr], children: list) -> dict[str, Any]:
        spec: dict[str, Any] = {}
        for attr in attrs:
            if attr.name == "title" and attr.kind == "str":
                spec["title"] = _template(attr.value, True)
            elif attr.kind == "str":
                spec[attr.name] = attr.value
            elif attr.kind == "bare":
                spec[attr.name] = True
            else:
                ok, value = _constant(attr.value, f"<ui {attr.name}>")
                spec[attr.name] = value if ok else attr.value
        state: dict[str, Any] = {}
        computed: dict[str, str] = {}
        body = []
        for child in children:
            if isinstance(child, tuple) and child[0] == "decl":
                self.declare(child[1], child[2], state, computed)
            else:
                body.append(child)
        if state:
            spec["state"] = state
        if computed:
            spec["computed"] = computed
        items = self.items(body)
        if len(items) == 1:
            spec["root"] = items[0]
        elif items:
            spec["root"] = {"type": "stack", "gap": "md", "children": items}
        return spec

    def declare(
        self, name: str, attrs: list[Attr], state: dict, computed: dict
    ) -> None:
        for attr in attrs:
            where = f"<{name} {attr.name}>"
            if name == "let":
                computed[attr.name] = (
                    attr.value.strip() if attr.kind != "bare" else "true"
                )
                continue
            if attr.kind == "str":
                state[attr.name] = attr.value
            elif attr.kind == "bare":
                state[attr.name] = True
            else:
                ok, value = _constant(attr.value, where)
                if not ok:
                    raise MarkupError(f"{where}: a state value must be a constant")
                state[attr.name] = value


def convert_block(source: str) -> dict[str, Any]:
    """Turn one ``<ui>`` block into a spec; raise :class:`MarkupError` if malformed.

    A block whose source does not end with ``</ui>`` is still streaming: open
    elements are closed and an unfinished last tag is dropped.
    """
    closed = source.rstrip().endswith("</ui>")
    lexed = lex(source)
    if lexed.incomplete and closed:
        raise MarkupError(lexed.incomplete)
    converter = _Converter(closed)
    stack: list[tuple[str, list[Attr], list]] = []
    result: dict[str, Any] | None = None
    for token in lexed.tokens:
        if result is not None:
            if token.kind == "text" and not token.text.strip():
                continue
            raise MarkupError("content after </ui>")
        if not stack:
            if token.kind == "text" and not token.text.strip():
                continue
            if token.kind != "open" or token.name != "ui":
                raise MarkupError("a block starts with <ui>")
        match token.kind:
            case "text":
                stack[-1][2].append(("text", token.text))
            case "open":
                if token.self_closing:
                    if not stack:
                        result = converter.root(token.attrs, [])
                    else:
                        stack[-1][2].append(
                            _build(converter, token.name, token.attrs, [])
                        )
                else:
                    stack.append((token.name, token.attrs, []))
            case "close":
                if stack[-1][0] != token.name:
                    raise MarkupError(
                        f"mismatched </{token.name}>: <{stack[-1][0]}> is still open"
                    )
                name, attrs, children = stack.pop()
                if stack:
                    stack[-1][2].append(_build(converter, name, attrs, children))
                else:
                    result = converter.root(attrs, children)
    if result is not None:
        return result
    if not stack:
        raise MarkupError("a block starts with <ui>")
    if closed:
        raise MarkupError(f"<{stack[-1][0]}> is never closed")
    while len(stack) > 1:
        name, attrs, children = stack.pop()
        stack[-1][2].append(_build(converter, name, attrs, children))
    return converter.root(stack[0][1], stack[0][2])


def _build(converter: _Converter, name: str, attrs: list[Attr], children: list) -> Any:
    if name in _DECLARATIONS:
        return ("decl", name, attrs)
    return converter.node(name, attrs, children)


def check_block(source: str) -> ValidatedSpec:
    """Convert and validate one block; raises ``MarkupError`` or ``SpecError``."""
    return validate_spec(convert_block(source))


__all__ = ["MarkupError", "check_block", "convert_block", "split_message", "unparse"]
