"""Built-in functions of the ``kt.ui`` expression language (a closed whitelist).

Each function takes the running evaluator (for step accounting) and its
already-evaluated arguments.
"""

import math
import re
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from kohakuterrarium.builtins.kt_ui.values import (
    ExprError,
    finite,
    is_num,
    number_text,
    power,
    to_display,
    type_name,
)

MAX_RANGE = 1000
_NUMBER_TEXT = re.compile(r"\s*-?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*")


def _num_arg(name: str, value: Any) -> float:
    if not is_num(value):
        raise ExprError("type", f"{name}() expects a number, got {type_name(value)}")
    return value


def _int_arg(name: str, value: Any) -> int:
    value = _num_arg(name, value)
    if value != math.floor(value):
        raise ExprError("type", f"{name}() expects a whole number")
    return int(value)


def _list_arg(name: str, value: Any) -> list:
    if not isinstance(value, list):
        raise ExprError("type", f"{name}() expects a list, got {type_name(value)}")
    return value


def _arity(name: str, args: tuple, low: int, high: int) -> None:
    if not low <= len(args) <= high:
        raise ExprError("arity", f"{name}() takes {low}-{high} arguments")


def _numbers(name: str, ev: Any, args: tuple) -> list[float]:
    values = args[0] if len(args) == 1 and isinstance(args[0], list) else list(args)
    ev.step(len(values))
    return [_num_arg(name, v) for v in values]


def _round_half_away(x: float, digits: int) -> float:
    m = 10.0**digits
    return math.copysign(math.floor(abs(x) * m + 0.5) / m, x) if x else 0.0


def _fn_round(ev: Any, *args: Any) -> float:
    _arity("round", args, 1, 2)
    digits = _int_arg("round", args[1]) if len(args) > 1 else 0
    if not 0 <= digits <= 10:
        raise ExprError("domain", "round() digits must be 0-10")
    return _round_half_away(_num_arg("round", args[0]), digits)


def _fn_unary(name: str, fn: Callable[[float], float]) -> Callable[..., float]:
    def call(ev: Any, *args: Any) -> float:
        _arity(name, args, 1, 1)
        return finite(float(fn(_num_arg(name, args[0]))))

    return call


def _fn_sqrt(ev: Any, *args: Any) -> float:
    _arity("sqrt", args, 1, 1)
    x = _num_arg("sqrt", args[0])
    if x < 0:
        raise ExprError("domain", "sqrt() of a negative number")
    return math.sqrt(x)


def _fn_pow(ev: Any, *args: Any) -> float:
    _arity("pow", args, 2, 2)
    return power(_num_arg("pow", args[0]), _num_arg("pow", args[1]))


def _fn_min_max(name: str, pick: Callable[[list[float]], float]) -> Callable:
    def call(ev: Any, *args: Any) -> float:
        if not args:
            raise ExprError("arity", f"{name}() needs at least one argument")
        values = _numbers(name, ev, args)
        if not values:
            raise ExprError("domain", f"{name}() of an empty list")
        return pick(values)

    return call


def _fn_sum(ev: Any, *args: Any) -> float:
    _arity("sum", args, 1, 1)
    values = _numbers("sum", ev, (_list_arg("sum", args[0]),))
    return finite(math.fsum(values)) if values else 0.0


def _fn_avg(ev: Any, *args: Any) -> float | None:
    _arity("avg", args, 1, 1)
    values = _numbers("avg", ev, (_list_arg("avg", args[0]),))
    return finite(math.fsum(values) / len(values)) if values else None


def _fn_len(ev: Any, *args: Any) -> float:
    _arity("len", args, 1, 1)
    value = args[0]
    if not isinstance(value, (str, list, dict)):
        raise ExprError("type", f"len() of {type_name(value)}")
    return float(len(value))


def _fn_clamp(ev: Any, *args: Any) -> float:
    _arity("clamp", args, 3, 3)
    x, low, high = (_num_arg("clamp", a) for a in args)
    return min(max(x, low), high)


def _group_thousands(text: str) -> str:
    sign = "-" if text.startswith("-") else ""
    body = text[1:] if sign else text
    whole, dot, frac = body.partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return sign + ",".join(groups) + dot + frac


def _fn_fmt(ev: Any, *args: Any) -> str:
    _arity("fmt", args, 1, 2)
    x = _num_arg("fmt", args[0])
    digits = _int_arg("fmt", args[1]) if len(args) > 1 else 0
    if not 0 <= digits <= 10:
        raise ExprError("domain", "fmt() digits must be 0-10")
    if abs(x) >= 1e21:
        return number_text(x)
    exact = Decimal(x if x != 0 else 0.0).quantize(
        Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP
    )
    return _group_thousands(format(exact, "f"))


def _fn_pct(ev: Any, *args: Any) -> str:
    _arity("pct", args, 1, 2)
    scaled = finite(_num_arg("pct", args[0]) * 100)
    return _fn_fmt(ev, scaled, *args[1:]) + "%"


def _fn_range(ev: Any, *args: Any) -> list[float]:
    _arity("range", args, 1, 3)
    ints = [_int_arg("range", a) for a in args]
    start, stop, step = (0, ints[0], 1) if len(ints) == 1 else (*ints[:2], 1)
    if len(ints) == 3:
        step = ints[2]
    if step == 0:
        raise ExprError("domain", "range() step must not be 0")
    count = max(0, (stop - start + step - (1 if step > 0 else -1)) // step)
    if count > MAX_RANGE:
        raise ExprError("limit", f"range() longer than {MAX_RANGE}")
    ev.step(count)
    return [float(v) for v in range(start, stop, step)]


def _fn_join(ev: Any, *args: Any) -> str:
    _arity("join", args, 1, 2)
    items = _list_arg("join", args[0])
    sep = args[1] if len(args) > 1 else ", "
    if not isinstance(sep, str):
        raise ExprError("type", "join() separator must be a string")
    ev.step(len(items))
    return sep.join(to_display(v) for v in items)


def _fn_keys(ev: Any, *args: Any) -> list[str]:
    _arity("keys", args, 1, 1)
    if not isinstance(args[0], dict):
        raise ExprError("type", f"keys() of {type_name(args[0])}")
    return list(args[0].keys())


def _fn_values(ev: Any, *args: Any) -> list[Any]:
    _arity("values", args, 1, 1)
    if not isinstance(args[0], dict):
        raise ExprError("type", f"values() of {type_name(args[0])}")
    return list(args[0].values())


def _fn_str(ev: Any, *args: Any) -> str:
    _arity("str", args, 1, 1)
    return to_display(args[0])


def _fn_num(ev: Any, *args: Any) -> float | None:
    _arity("num", args, 1, 1)
    value = args[0]
    if is_num(value):
        return value
    if isinstance(value, str) and _NUMBER_TEXT.fullmatch(value):
        return finite(float(value))
    return None


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": _fn_unary("abs", abs),
    "avg": _fn_avg,
    "ceil": _fn_unary("ceil", math.ceil),
    "clamp": _fn_clamp,
    "floor": _fn_unary("floor", math.floor),
    "fmt": _fn_fmt,
    "join": _fn_join,
    "keys": _fn_keys,
    "len": _fn_len,
    "max": _fn_min_max("max", max),
    "min": _fn_min_max("min", min),
    "num": _fn_num,
    "pct": _fn_pct,
    "pow": _fn_pow,
    "range": _fn_range,
    "round": _fn_round,
    "sqrt": _fn_sqrt,
    "str": _fn_str,
    "sum": _fn_sum,
    "values": _fn_values,
}
