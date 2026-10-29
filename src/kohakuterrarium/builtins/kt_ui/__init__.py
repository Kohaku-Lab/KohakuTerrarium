"""The ``kt.ui`` declarative format: inline markup, spec validation, expressions, text fallback.

An assistant reply carries ``<ui>`` blocks (:func:`split_message`); each block
converts to a spec (:func:`convert_block`) that :func:`validate_spec` checks
and :func:`render_fallback` turns into text. The web frontend implements the
same markup and expression rules; both run the shared conformance files
``tests/fixtures/kt_ui_markup.json`` and ``tests/fixtures/kt_ui_expressions.json``.
"""

from kohakuterrarium.builtins.kt_ui.expr import (
    ExprError,
    evaluate,
    interpolate,
    parse,
    to_display,
)
from kohakuterrarium.builtins.kt_ui.fallback import render_fallback
from kohakuterrarium.builtins.kt_ui.markup import (
    MarkupError,
    check_block,
    convert_block,
    split_message,
)
from kohakuterrarium.builtins.kt_ui.spec import (
    SpecError,
    ValidatedSpec,
    json_schema,
    validate_spec,
)

__all__ = [
    "ExprError",
    "MarkupError",
    "SpecError",
    "ValidatedSpec",
    "check_block",
    "convert_block",
    "evaluate",
    "interpolate",
    "json_schema",
    "parse",
    "render_fallback",
    "split_message",
    "to_display",
    "validate_spec",
]
