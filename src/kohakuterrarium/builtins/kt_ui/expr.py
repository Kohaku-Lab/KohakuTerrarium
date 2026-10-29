"""The ``kt.ui`` expression language: a small pure language, parsed without ``eval``.

Values are JSON values; every number is a float, as in JavaScript, so this
module and ``ktui/expr.js`` in the frontend give the same results. Errors carry
a ``code`` (``syntax``, ``unknown_name``, ``unknown_function``, ``type``,
``zero_division``, ``domain``, ``arity``, ``limit``) shared by both sides.
"""

import math
import re
from typing import Any

from kohakuterrarium.builtins.kt_ui.functions import FUNCTIONS, MAX_RANGE
from kohakuterrarium.builtins.kt_ui.values import (
    ExprError,
    equal,
    finite,
    is_num,
    normalize,
    number_text,
    power,
    to_display,
    truthy,
    type_name,
)

MAX_EXPR_CHARS = 2048
MAX_STEPS = 10_000
MAX_DEPTH = 64

KEYWORDS = frozenset({"true", "false", "null", "and", "or", "not"})
_TWO_CHAR_OPS = ("**", "==", "!=", "<=", ">=")
_ONE_CHAR_OPS = "+-*/%<>?:()[]{},."
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}
_CMP_OPS = ("==", "!=", "<", "<=", ">", ">=")
_INTERP = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)


# ── tokens ──────────────────────────────────────────────────────────────


def _digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _tokenize(text: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif _digit(ch) or (ch == "." and i + 1 < n and _digit(text[i + 1])):
            j = i
            while j < n and _digit(text[j]):
                j += 1
            if j < n and text[j] == "." and j + 1 < n and _digit(text[j + 1]):
                j += 1
                while j < n and _digit(text[j]):
                    j += 1
            if j < n and text[j] in "eE":
                k = j + 1
                if k < n and text[k] in "+-":
                    k += 1
                if k < n and _digit(text[k]):
                    j = k
                    while j < n and _digit(text[j]):
                        j += 1
            tokens.append(("num", float(text[i:j])))
            i = j
        elif ch in "\"'":
            j, out = i + 1, []
            while True:
                if j >= n:
                    raise ExprError("syntax", "unterminated string")
                c = text[j]
                if c == ch:
                    break
                if c == "\\":
                    if j + 1 >= n or text[j + 1] not in _ESCAPES:
                        raise ExprError("syntax", "bad escape in string")
                    out.append(_ESCAPES[text[j + 1]])
                    j += 2
                    continue
                out.append(c)
                j += 1
            tokens.append(("str", "".join(out)))
            i = j + 1
        elif (ch.isascii() and ch.isalpha()) or ch == "_":
            j = i
            while j < n and text[j].isascii() and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            tokens.append(("kw" if word in KEYWORDS else "name", word))
            i = j
        elif text[i : i + 2] in _TWO_CHAR_OPS:
            tokens.append(("op", text[i : i + 2]))
            i += 2
        elif ch in _ONE_CHAR_OPS:
            tokens.append(("op", ch))
            i += 1
        else:
            raise ExprError("syntax", f"unexpected character {ch!r}")
    tokens.append(("end", None))
    return tokens


# ── parser ──────────────────────────────────────────────────────────────


class _Parser:
    def __init__(self, tokens: list[tuple[str, Any]]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    def peek(self) -> tuple[str, Any]:
        return self.tokens[self.pos]

    def take(self) -> tuple[str, Any]:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def at(self, kind: str, value: Any = None) -> bool:
        tk, tv = self.peek()
        return tk == kind and (value is None or tv == value)

    def expect(self, kind: str, value: Any = None) -> tuple[str, Any]:
        if not self.at(kind, value):
            raise ExprError("syntax", f"expected {value or kind}")
        return self.take()

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError("limit", "expression nests too deeply")

    def ternary(self) -> tuple:
        self.enter()
        cond = self.or_()
        if self.at("op", "?"):
            self.take()
            yes = self.ternary()
            self.expect("op", ":")
            no = self.ternary()
            cond = ("cond", cond, yes, no)
        self.depth -= 1
        return cond

    def or_(self) -> tuple:
        left = self.and_()
        while self.at("kw", "or"):
            self.take()
            left = ("or", left, self.and_())
        return left

    def and_(self) -> tuple:
        left = self.not_()
        while self.at("kw", "and"):
            self.take()
            left = ("and", left, self.not_())
        return left

    def not_(self) -> tuple:
        if self.at("kw", "not"):
            self.take()
            self.enter()
            node = ("not", self.not_())
            self.depth -= 1
            return node
        return self.cmp()

    def cmp(self) -> tuple:
        left = self.add()
        tk, tv = self.peek()
        if tk == "op" and tv in _CMP_OPS:
            self.take()
            left = ("bin", tv, left, self.add())
        return left

    def add(self) -> tuple:
        left = self.mul()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            left = ("bin", op, left, self.mul())
        return left

    def mul(self) -> tuple:
        left = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/", "%"):
            op = self.take()[1]
            left = ("bin", op, left, self.unary())
        return left

    def unary(self) -> tuple:
        if self.peek()[0] == "op" and self.peek()[1] in ("-", "+"):
            op = self.take()[1]
            self.enter()
            node = ("neg" if op == "-" else "pos", self.unary())
            self.depth -= 1
            return node
        return self.power()

    def power(self) -> tuple:
        base = self.postfix()
        if self.at("op", "**"):
            self.take()
            self.enter()
            base = ("bin", "**", base, self.unary())
            self.depth -= 1
        return base

    def postfix(self) -> tuple:
        node = self.primary()
        while True:
            if self.at("op", "."):
                self.take()
                tk, tv = self.take()
                if tk not in ("name", "kw"):
                    raise ExprError("syntax", "expected a key after '.'")
                node = ("member", node, tv)
            elif self.at("op", "["):
                self.take()
                index = self.ternary()
                self.expect("op", "]")
                node = ("index", node, index)
            else:
                return node

    def primary(self) -> tuple:
        tk, tv = self.take()
        match tk:
            case "num":
                return ("lit", tv)
            case "str":
                return ("lit", tv)
            case "kw" if tv in ("true", "false", "null"):
                return ("lit", {"true": True, "false": False, "null": None}[tv])
            case "name":
                if self.at("op", "("):
                    if tv not in FUNCTIONS:
                        raise ExprError("unknown_function", f"unknown function {tv}")
                    self.take()
                    args = self.items(")")
                    return ("call", tv, args)
                return ("name", tv)
            case "op" if tv == "(":
                inner = self.ternary()
                self.expect("op", ")")
                return inner
            case "op" if tv == "[":
                return ("list", self.items("]"))
            case "op" if tv == "{":
                return ("obj", self.entries())
        raise ExprError("syntax", "unexpected token")

    def entries(self) -> list[tuple[str, tuple]]:
        entries: list[tuple[str, tuple]] = []
        seen: set[str] = set()
        while not self.at("op", "}"):
            tk, key = self.take()
            if tk not in ("name", "kw", "str"):
                raise ExprError("syntax", "an object key is a name or a string")
            if key in seen:
                raise ExprError("syntax", f"duplicate object key {key!r}")
            seen.add(key)
            self.expect("op", ":")
            entries.append((key, self.ternary()))
            if self.at("op", ","):
                self.take()
            elif not self.at("op", "}"):
                raise ExprError("syntax", "expected ',' or '}'")
        self.take()
        return entries

    def items(self, close: str) -> list[tuple]:
        items: list[tuple] = []
        while not self.at("op", close):
            items.append(self.ternary())
            if self.at("op", ","):
                self.take()
            elif not self.at("op", close):
                raise ExprError("syntax", f"expected ',' or '{close}'")
        self.take()
        return items


def parse(text: str) -> tuple:
    """Parse an expression into an AST; raise :class:`ExprError` on bad input."""
    if not isinstance(text, str):
        raise ExprError("syntax", "expression must be a string")
    if len(text) > MAX_EXPR_CHARS:
        raise ExprError("limit", f"expression longer than {MAX_EXPR_CHARS} chars")
    parser = _Parser(_tokenize(text))
    node = parser.ternary()
    if not parser.at("end"):
        raise ExprError("syntax", "unexpected token after the expression")
    return node


def free_names(node: tuple) -> set[str]:
    """Names an AST reads from its scope (function names excluded)."""
    match node[0]:
        case "name":
            return {node[1]}
        case "lit":
            return set()
        case "call":
            return set().union(*(free_names(a) for a in node[2])) if node[2] else set()
        case "list":
            return set().union(*(free_names(a) for a in node[1])) if node[1] else set()
        case "obj":
            return (
                set().union(*(free_names(v) for _, v in node[1])) if node[1] else set()
            )
        case "member":
            return free_names(node[1])
    return set().union(*(free_names(c) for c in node[1:] if isinstance(c, tuple)))


# ── evaluation ──────────────────────────────────────────────────────────


class _Eval:
    def __init__(self, scope: dict[str, Any], max_steps: int) -> None:
        self.scope = scope
        self.steps = 0
        self.max_steps = max_steps

    def step(self, count: int = 1) -> None:
        self.steps += count
        if self.steps > self.max_steps:
            raise ExprError("limit", "expression takes too many steps")

    def run(self, node: tuple) -> Any:
        self.step()
        match node[0]:
            case "lit":
                return node[1]
            case "name":
                if node[1] not in self.scope:
                    raise ExprError("unknown_name", f"unknown name {node[1]}")
                return self.scope[node[1]]
            case "list":
                return [self.run(item) for item in node[1]]
            case "obj":
                return {key: self.run(value) for key, value in node[1]}
            case "neg" | "pos":
                value = self.run(node[1])
                if not is_num(value):
                    raise ExprError("type", f"expected number, got {type_name(value)}")
                return -value if node[0] == "neg" else value
            case "not":
                return not truthy(self.run(node[1]))
            case "and":
                left = self.run(node[1])
                return self.run(node[2]) if truthy(left) else left
            case "or":
                left = self.run(node[1])
                return left if truthy(left) else self.run(node[2])
            case "cond":
                return (
                    self.run(node[2])
                    if truthy(self.run(node[1]))
                    else self.run(node[3])
                )
            case "member":
                return _member(self.run(node[1]), node[2])
            case "index":
                return _index(self.run(node[1]), self.run(node[2]))
            case "call":
                args = [self.run(arg) for arg in node[2]]
                return FUNCTIONS[node[1]](self, *args)
            case "bin":
                return _binary(node[1], self.run(node[2]), self.run(node[3]))
        raise ExprError("syntax", "bad expression node")


def _member(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    raise ExprError("type", f"cannot read .{key} of {type_name(obj)}")


def _index(obj: Any, index: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (list, str)):
        if not is_num(index) or index != math.floor(index):
            raise ExprError("type", "list index must be a whole number")
        i = int(index)
        if i < 0:
            i += len(obj)
        return obj[i] if 0 <= i < len(obj) else None
    if isinstance(obj, dict):
        if not isinstance(index, str):
            raise ExprError("type", "object key must be a string")
        return obj.get(index)
    raise ExprError("type", f"cannot index {type_name(obj)}")


def _binary(op: str, a: Any, b: Any) -> Any:
    match op:
        case "==":
            return equal(a, b)
        case "!=":
            return not equal(a, b)
        case "<" | "<=" | ">" | ">=":
            if not (
                (is_num(a) and is_num(b)) or (isinstance(a, str) and isinstance(b, str))
            ):
                raise ExprError(
                    "type", f"cannot compare {type_name(a)} and {type_name(b)}"
                )
            return {
                "<": a < b,
                "<=": a <= b,
                ">": a > b,
                ">=": a >= b,
            }[op]
        case "+":
            if is_num(a) and is_num(b):
                return finite(a + b)
            if isinstance(a, list) and isinstance(b, list):
                return a + b
            if isinstance(a, str) or isinstance(b, str):
                if all(isinstance(v, str) or is_num(v) for v in (a, b)):
                    return to_display(a) + to_display(b)
            raise ExprError("type", f"cannot add {type_name(a)} and {type_name(b)}")
    if not (is_num(a) and is_num(b)):
        raise ExprError("type", f"'{op}' needs numbers")
    match op:
        case "-":
            return finite(a - b)
        case "*":
            return finite(a * b)
        case "/":
            if b == 0:
                raise ExprError("zero_division", "division by zero")
            return finite(a / b)
        case "%":
            if b == 0:
                raise ExprError("zero_division", "modulo by zero")
            return finite(a - b * math.floor(a / b))
    return power(a, b)


def evaluate(
    expr: "str | tuple", scope: dict[str, Any] | None = None, max_steps: int = MAX_STEPS
) -> Any:
    """Evaluate an expression (text or parsed AST) against ``scope``."""
    node = parse(expr) if isinstance(expr, str) else expr
    return _Eval(normalize(dict(scope or {})), max_steps).run(node)


def template_expression(text: str) -> str | None:
    """The inner expression when ``text`` is exactly one ``{{ expr }}``, else ``None``."""
    match = re.fullmatch(r"\s*\{\{(.*?)\}\}\s*", text, re.DOTALL)
    if match is None or "}}" in match.group(1) or "{{" in match.group(1):
        return None
    return match.group(1)


def template_expressions(text: str) -> list[str]:
    """Every ``{{ expr }}`` inside ``text``, in order."""
    return [m.group(1) for m in _INTERP.finditer(text)]


def interpolate(text: str, scope: dict[str, Any] | None = None) -> Any:
    """Resolve ``{{ expr }}`` in ``text``.

    A string that is exactly one ``{{ expr }}`` keeps the value's type;
    otherwise each part is replaced by its display text. Errors propagate.
    """
    whole = template_expression(text)
    if whole is not None:
        return evaluate(whole, scope)
    if "{{" not in text:
        return text
    return _INTERP.sub(lambda m: to_display(evaluate(m.group(1), scope)), text)


__all__ = [
    "ExprError",
    "FUNCTIONS",
    "KEYWORDS",
    "MAX_EXPR_CHARS",
    "MAX_RANGE",
    "MAX_STEPS",
    "evaluate",
    "free_names",
    "interpolate",
    "normalize",
    "number_text",
    "parse",
    "template_expression",
    "template_expressions",
    "to_display",
    "truthy",
]
