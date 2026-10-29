"""The ``kt.ui`` declarative widget format: spec validation, expressions, text fallback.

``show_ui`` validates a spec with :func:`validate_spec`, renders its text form
with :func:`render_fallback`, and opens a ``kt.ui`` widget. The same
expression language is implemented in the web frontend; both run the shared
conformance file ``tests/fixtures/kt_ui_expressions.json``.
"""

from kohakuterrarium.builtins.kt_ui.expr import (
    ExprError,
    evaluate,
    interpolate,
    parse,
    to_display,
)
from kohakuterrarium.builtins.kt_ui.fallback import render_fallback
from kohakuterrarium.builtins.kt_ui.spec import (
    SpecError,
    ValidatedSpec,
    json_schema,
    validate_spec,
)

__all__ = [
    "ExprError",
    "SpecError",
    "ValidatedSpec",
    "evaluate",
    "interpolate",
    "json_schema",
    "parse",
    "render_fallback",
    "to_display",
    "validate_spec",
]
