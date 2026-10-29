"""Value model of the ``kt.ui`` expression language: JSON values, every number a float.

Display text follows JavaScript (``String(number)``) so the backend and the
frontend show the same text.
"""

import json
import math
from typing import Any


class ExprError(ValueError):
    """An expression failed to parse or evaluate."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize(value: Any) -> Any:
    """Convert a JSON value to the evaluator's model (every number a float)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    return str(value)


def is_num(value: Any) -> bool:
    return isinstance(value, float)


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    return "object"


def finite(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        raise ExprError("domain", "result is not a finite number")
    return value


def number_text(value: float) -> str:
    """Format a number the way JavaScript's ``String(number)`` does."""
    if value == 0:
        return "0"
    if value != value or math.isinf(value):
        return "NaN" if value != value else ("Infinity" if value > 0 else "-Infinity")
    sign = "-" if value < 0 else ""
    mantissa, _, exp = repr(abs(value)).partition("e")
    whole, _, frac = mantissa.partition(".")
    raw = whole + frac
    lead = len(raw) - len(raw.lstrip("0"))
    point = len(whole) + (int(exp) if exp else 0) - lead
    digits = raw.strip("0")
    k = len(digits)
    if k <= point <= 21:
        return sign + digits + "0" * (point - k)
    if 0 < point <= 21:
        return sign + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return sign + "0." + "0" * (-point) + digits
    e = point - 1
    head = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{sign}{head}e{'+' if e >= 0 else '-'}{abs(e)}"


def _json_text(value: Any) -> str:
    if isinstance(value, float):
        return number_text(value)
    if isinstance(value, list):
        return "[" + ",".join(_json_text(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(k, ensure_ascii=False) + ":" + _json_text(v)
                for k, v in value.items()
            )
            + "}"
        )
    return json.dumps(value, ensure_ascii=False)


def to_display(value: Any) -> str:
    """Text shown for a value inside a template."""
    value = normalize(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return number_text(value)
    if isinstance(value, str):
        return value
    return _json_text(value)


def truthy(value: Any) -> bool:
    """Falsy values: ``false``, ``null``, ``0``, ``""``, ``[]`` and ``{}``."""
    if value is None or value is False:
        return False
    if isinstance(value, float):
        return value != 0
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return True


def equal(a: Any, b: Any) -> bool:
    if type_name(a) != type_name(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return a == b


def power(a: float, b: float) -> float:
    if a == 0 and b < 0:
        raise ExprError("zero_division", "zero to a negative power")
    if a < 0 and b != math.floor(b):
        raise ExprError("domain", "negative number to a fractional power")
    try:
        return finite(math.pow(a, b))
    except OverflowError as exc:
        raise ExprError("domain", "result is not a finite number") from exc
