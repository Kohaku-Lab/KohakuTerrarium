"""Shared libcst helpers used by per-kind codegen modules.

Thin wrappers over libcst's transformer API for the patterns we
need across tool / plugin / trigger / subagent / input-output
modules. Phase 3 hardens these with more helpers as the codegen
modules require.
"""

import textwrap
from typing import Protocol

import libcst as cst


class RoundTripError(ValueError):
    """Raised when an AST-based round-trip cannot preserve the file."""


class Codegen(Protocol):
    """Protocol implemented by each per-kind codegen module."""

    def render_new(self, form: dict) -> str: ...
    def update_existing(self, source: str, form: dict, execute_body: str) -> str: ...
    def parse_back(self, source: str) -> dict: ...


def parse(source: str) -> cst.Module:
    """Parse Python source into a libcst Module."""
    return cst.parse_module(source)


def find_class(tree: cst.Module, name: str) -> cst.ClassDef | None:
    """Return the module-level ClassDef named *name* or None."""
    for node in tree.body:
        if isinstance(node, cst.ClassDef) and node.name.value == name:
            return node
    return None


def first_class(tree: cst.Module) -> cst.ClassDef | None:
    """Return the first module-level ClassDef (any name) or None."""
    for node in tree.body:
        if isinstance(node, cst.ClassDef):
            return node
    return None


def replace_string_property(klass: cst.ClassDef, prop: str, value: str) -> cst.ClassDef:
    """Rewrite ``@property def prop(self): return "..."`` to the new value.

    Also rewrites a plain ``prop = "..."`` class assignment if no
    property function is found — covers both styles used across
    the builtin tools.
    """

    new_return = cst.SimpleString(_py_string_literal(value))

    class _PropReplacer(cst.CSTTransformer):
        touched: bool = False

        def leave_FunctionDef(self, orig, updated):
            if updated.name.value != prop:
                return updated
            self.touched = True
            return updated.with_changes(
                body=cst.IndentedBlock(
                    body=[
                        cst.SimpleStatementLine(
                            body=[
                                cst.Return(value=new_return),
                            ]
                        ),
                    ]
                )
            )

        def leave_Assign(self, orig, updated):
            if len(updated.targets) != 1:
                return updated
            tgt = updated.targets[0].target
            if isinstance(tgt, cst.Name) and tgt.value == prop:
                self.touched = True
                return updated.with_changes(value=new_return)
            return updated

    transformer = _PropReplacer()
    new_klass = klass.visit(transformer)
    return new_klass


def replace_method_body(
    klass: cst.ClassDef, method: str, body_source: str
) -> cst.ClassDef:
    """Replace the body of ``def method(...)`` with *body_source*.

    *body_source* is raw Python text for the new body (one or
    more statements). Indentation is normalized — callers pass
    unindented source; libcst re-indents on serialize.
    """
    body_source = _dedent_body(body_source)
    if not body_source.strip():
        body_source = "return None"

    parsed = cst.parse_module(body_source).body
    if not parsed:
        parsed = [
            cst.SimpleStatementLine(
                body=[
                    cst.Return(value=cst.Name(value="None")),
                ]
            )
        ]
    indented = cst.IndentedBlock(body=list(parsed))

    class _MethodReplacer(cst.CSTTransformer):
        touched: bool = False

        def leave_FunctionDef(self, orig, updated):
            if updated.name.value != method:
                return updated
            self.touched = True
            return updated.with_changes(body=indented)

    return klass.visit(_MethodReplacer())


def read_property_string(klass: cst.ClassDef, prop: str) -> str | None:
    """Extract the string returned by ``@property def prop``.

    Falls back to a class-level assignment ``prop = "..."`` if no
    property function is present. Returns ``None`` if neither
    shape is found.
    """
    # @property def form
    for node in klass.body.body:
        if (
            isinstance(node, cst.FunctionDef)
            and node.name.value == prop
            and isinstance(node.body, cst.IndentedBlock)
        ):
            for stmt in node.body.body:
                if isinstance(stmt, cst.SimpleStatementLine):
                    for s in stmt.body:
                        if isinstance(s, cst.Return) and isinstance(
                            s.value, cst.SimpleString
                        ):
                            return s.value.evaluated_value
                        if isinstance(s, cst.Return) and isinstance(
                            s.value, cst.ConcatenatedString
                        ):
                            # Not worth the bookkeeping — tell caller.
                            return None

    # attr assignment form
    for node in klass.body.body:
        if isinstance(node, cst.SimpleStatementLine):
            for stmt in node.body:
                if isinstance(stmt, cst.Assign) and len(stmt.targets) == 1:
                    tgt = stmt.targets[0].target
                    if isinstance(tgt, cst.Name) and tgt.value == prop:
                        if isinstance(stmt.value, cst.SimpleString):
                            return stmt.value.evaluated_value
    return None


def read_class_attr_bool(klass: cst.ClassDef, attr: str) -> bool:
    """Read a ``attr = True/False`` class-level assignment. Defaults False."""
    for node in klass.body.body:
        if isinstance(node, cst.SimpleStatementLine):
            for stmt in node.body:
                if (
                    isinstance(stmt, (cst.Assign, cst.AnnAssign))
                    and _assign_target_name(stmt) == attr
                ):
                    value = stmt.value if isinstance(stmt, cst.Assign) else stmt.value
                    if isinstance(value, cst.Name):
                        if value.value == "True":
                            return True
                        if value.value == "False":
                            return False
    return False


def read_method_body(klass: cst.ClassDef, method: str) -> str | None:
    """Extract the source text for *method*'s body (un-dedented).

    Returns None if the method is not found. Caller typically
    wants to show this verbatim to the user; dedent on their
    side if required.
    """
    for node in klass.body.body:
        if (
            isinstance(node, cst.FunctionDef)
            and node.name.value == method
            and isinstance(node.body, cst.IndentedBlock)
        ):
            module = cst.Module(body=list(node.body.body))
            return module.code
    return None


def replace_class_in_module(
    tree: cst.Module, class_name: str, new_klass: cst.ClassDef
) -> cst.Module:
    """Return *tree* with the class named *class_name* swapped for *new_klass*."""
    body = tuple(
        new_klass if isinstance(n, cst.ClassDef) and n.name.value == class_name else n
        for n in tree.body
    )
    return tree.with_changes(body=body)


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _assign_target_name(stmt: cst.Assign | cst.AnnAssign) -> str | None:
    if isinstance(stmt, cst.Assign):
        if len(stmt.targets) != 1:
            return None
        tgt = stmt.targets[0].target
    else:
        tgt = stmt.target
    if isinstance(tgt, cst.Name):
        return tgt.value
    return None


def _py_string_literal(value: str) -> str:
    """Quote *value* so it round-trips through ``cst.SimpleString``."""
    # ``repr`` gives a valid Python literal; wrap single quotes in
    # triple quotes for multi-line to avoid escape soup.
    if "\n" in value:
        escaped = value.replace('"""', r"\"\"\"")
        return f'"""{escaped}"""'
    return repr(value)


def _dedent_body(source: str) -> str:
    """Strip common leading whitespace so parse_module accepts it."""
    # Drop leading blank lines
    lines = source.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""
    return textwrap.dedent("\n".join(lines))
