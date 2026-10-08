"""A tool's call arguments, between its source and the editor form.

In source they are the JSON schema ``get_parameters_schema`` returns; in the
form, one row per argument: ``{name, type_hint, description, required,
default}`` with ``type_hint`` a JSON-schema type. Only a method that returns a
single literal is editable; anything else is left untouched.
"""

import ast

import libcst as cst

from kohakuterrarium.studio.editors.templates import py_literal

METHOD = "get_parameters_schema"
JSON_TYPES = ("string", "integer", "number", "boolean", "array", "object")
_BODY_INDENT = 8


def schema_to_rows(schema: dict) -> list[dict]:
    """Form rows for each property of ``schema``, in declaration order."""
    required = set(schema.get("required") or [])
    rows = []
    for name, prop in (schema.get("properties") or {}).items():
        prop = prop if isinstance(prop, dict) else {}
        rows.append(
            {
                "name": name,
                "type_hint": prop.get("type", "string"),
                "description": prop.get("description", ""),
                "required": name in required,
                "default": prop.get("default"),
            }
        )
    return rows


def rows_to_schema(rows: list[dict]) -> dict | None:
    """The JSON schema the form rows describe; None when there are none."""
    properties: dict = {}
    required = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        kind = row.get("type_hint") if row.get("type_hint") in JSON_TYPES else "string"
        prop: dict = {"type": kind}
        if row.get("description"):
            prop["description"] = str(row["description"])
        if row.get("required"):
            required.append(name)
        elif row.get("default") is not None:
            prop["default"] = row["default"]
        properties[name] = prop
    if not properties:
        return None
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _method(klass: cst.ClassDef) -> cst.FunctionDef | None:
    for node in klass.body.body:
        if isinstance(node, cst.FunctionDef) and node.name.value == METHOD:
            return node
    return None


def read_schema(klass: cst.ClassDef) -> tuple[dict | None, bool]:
    """``(schema, editable)``: the literal the method returns, if it is one.

    No method reads as ``(None, True)``; a method computing its schema reads
    as ``(None, False)``.
    """
    node = _method(klass)
    if node is None:
        return None, True
    body = node.body.body if isinstance(node.body, cst.IndentedBlock) else []
    if len(body) != 1 or not isinstance(body[0], cst.SimpleStatementLine):
        return None, False
    stmt = body[0].body[0]
    if not isinstance(stmt, cst.Return) or stmt.value is None:
        return None, False
    try:
        value = ast.literal_eval(cst.Module(body=[]).code_for_node(stmt.value))
    except (ValueError, SyntaxError):
        return None, False
    return (value, True) if isinstance(value, dict) else (None, False)


def write_schema(klass: cst.ClassDef, schema: dict | None) -> cst.ClassDef:
    """``klass`` returning ``schema``; None removes the method."""
    body = list(klass.body.body)
    index = next(
        (
            i
            for i, n in enumerate(body)
            if isinstance(n, cst.FunctionDef) and n.name.value == METHOD
        ),
        None,
    )
    if schema is None:
        if index is not None:
            del body[index]
        return klass.with_changes(body=klass.body.with_changes(body=body))
    literal = py_literal(schema, _BODY_INDENT, len("return "))
    method = cst.parse_statement(
        f"def {METHOD}(self) -> dict[str, Any]:\n    return {literal}\n"
    )
    if index is not None:
        body[index] = body[index].with_changes(body=method.body)
    else:
        at = next(
            (
                i
                for i, n in enumerate(body)
                if isinstance(n, cst.FunctionDef) and n.name.value == "_execute"
            ),
            len(body),
        )
        body.insert(at, method)
    return klass.with_changes(body=klass.body.with_changes(body=body))
