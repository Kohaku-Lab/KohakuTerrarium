"""Shared libcst helpers used by per-kind codegen modules.

Provides the shared LibCST parsing, extraction, and replacement operations used
by tool, plugin, trigger, sub-agent, and I/O editors.
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
    """Parse Python source into a LibCST module."""
    return cst.parse_module(source)


def find_class(tree: cst.Module, name: str) -> cst.ClassDef | None:
    """Return the named module-level class, or ``None`` when absent."""
    for node in tree.body:
        if isinstance(node, cst.ClassDef) and node.name.value == name:
            return node
    return None


def first_class(tree: cst.Module) -> cst.ClassDef | None:
    """Return the first module-level class, or ``None`` when absent."""
    for node in tree.body:
        if isinstance(node, cst.ClassDef):
            return node
    return None


def replace_string_property(klass: cst.ClassDef, prop: str, value: str) -> cst.ClassDef:
    """Replace a string property or equivalent class assignment.

    Both property-return and class-attribute forms are supported because built-in
    modules use both representations.
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


def replace_class_attr_bool(
    klass: cst.ClassDef, attr: str, value: bool
) -> cst.ClassDef:
    """Set or insert a class-level boolean attribute.

    Missing attributes are inserted as the first class statement so editor
    metadata toggles have a deterministic source representation.
    """
    new_value = cst.Name(value="True" if value else "False")

    class _BoolReplacer(cst.CSTTransformer):
        touched: bool = False

        def leave_Assign(self, orig, updated):
            if len(updated.targets) != 1:
                return updated
            tgt = updated.targets[0].target
            if isinstance(tgt, cst.Name) and tgt.value == attr:
                self.touched = True
                return updated.with_changes(value=new_value)
            return updated

    transformer = _BoolReplacer()
    new_klass = klass.visit(transformer)
    if transformer.touched:
        return new_klass
    # First-statement insertion keeps generated metadata placement deterministic.
    assign = cst.SimpleStatementLine(
        body=[
            cst.Assign(
                targets=[cst.AssignTarget(target=cst.Name(value=attr))],
                value=new_value,
            )
        ]
    )
    old_body = new_klass.body
    return new_klass.with_changes(
        body=old_body.with_changes(body=[assign, *old_body.body])
    )


def replace_method_body(
    klass: cst.ClassDef, method: str, body_source: str
) -> cst.ClassDef:
    """Replace a method body from unindented Python source.

    Empty input becomes ``return None``. LibCST restores class indentation during
    serialization.
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
    """Read a literal string property or equivalent class assignment.

    Unsupported expressions and missing values return ``None``.
    """
    # Property-return form takes precedence over the class-attribute fallback.
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
                            # Concatenated expressions cannot be safely round-tripped here.
                            return None

    # Fall back to a literal class assignment.
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
    """Read a literal class-level boolean, defaulting to ``False``."""
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
    """Return a method body's source text, or ``None`` when absent.

    The returned text preserves LibCST formatting and may require caller-side
    dedentation for presentation.
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
    """Quote a string for lossless ``cst.SimpleString`` construction.

    Multiline values use triple quotes to avoid excessive escaping.
    """
    if "\n" in value:
        escaped = value.replace('"""', r"\"\"\"")
        return f'"""{escaped}"""'
    return repr(value)


def _dedent_body(source: str) -> str:
    """Remove leading blank lines and common indentation for parsing."""
    lines = source.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""
    return textwrap.dedent("\n".join(lines))
