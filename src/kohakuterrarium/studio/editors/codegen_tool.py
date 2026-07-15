"""Codegen for ``BaseTool`` subclasses.

Scaffolds tool modules, updates managed properties and ``_execute`` bodies with
LibCST, and extracts form state. Unsupported source shapes fall back to raw mode
instead of risking a lossy rewrite.
"""

import libcst as cst

from kohakuterrarium.studio.editors.codegen_common import (
    RoundTripError,
    find_class,
    first_class,
    parse,
    read_class_attr_bool,
    read_method_body,
    read_property_string,
    replace_class_in_module,
    replace_method_body,
    replace_string_property,
)
from kohakuterrarium.studio.editors.templates import render


def render_new(form: dict) -> str:
    """Render a new tool from identity, execution metadata, and method body."""
    name = form.get("name") or form.get("tool_name") or "my_tool"
    class_name = form.get("class_name") or _to_class_name(name)
    ctx = {
        "name": name,
        "tool_name": form.get("tool_name", name),
        "class_name": class_name,
        "description": form.get("description", "TODO: describe this tool"),
        "execution_mode": (form.get("execution_mode") or "direct").lower(),
        "needs_context": bool(form.get("needs_context", False)),
        "execute_body": form.get("execute_body") or 'return ToolResult(output="TODO")',
    }
    return render("tool.py.j2", **ctx)


def update_existing(source: str, form: dict, execute_body: str) -> str:
    """Update managed tool properties and the ``_execute`` body in place."""
    tree = parse(source)
    class_name = form.get("class_name")
    if class_name:
        klass = find_class(tree, class_name)
    else:
        klass = first_class(tree)
        class_name = klass.name.value if klass else None
    if klass is None:
        raise RoundTripError(f"no class found in source (looking for {class_name!r})")

    if "tool_name" in form:
        klass = replace_string_property(klass, "tool_name", form["tool_name"])
    if "description" in form:
        klass = replace_string_property(klass, "description", form["description"])
    if execute_body is not None:
        klass = replace_method_body(klass, "_execute", execute_body)

    return replace_class_in_module(tree, class_name, klass).code


def parse_back(source: str) -> dict:
    """Extract editable tool state, falling back to raw mode when unsafe.

    The returned envelope includes form values, the execute body, and warnings.
    """
    warnings: list[dict] = []

    try:
        tree = parse(source)
    except Exception as e:
        return _raw_mode_envelope(source, f"parse failed: {e}")

    klass = _pick_tool_class(tree)
    if klass is None:
        return _raw_mode_envelope(source, "no class matching BaseTool shape")

    tool_name = read_property_string(klass, "tool_name") or ""
    description = read_property_string(klass, "description") or ""
    execution_mode = _read_execution_mode(klass)
    needs_context = read_class_attr_bool(klass, "needs_context")
    require_manual_read = read_class_attr_bool(klass, "require_manual_read")

    exec_body = read_method_body(klass, "_execute")
    if exec_body is None:
        warnings.append(
            {
                "code": "execute_not_found",
                "message": "_execute method not found — use raw mode",
            }
        )
        exec_body = ""

    if _has_decorators_on_execute(klass):
        warnings.append(
            {
                "code": "ast_roundtrip_unsafe",
                "message": "_execute has decorators — form mode will lose them; use raw",
            }
        )

    mode = (
        "raw"
        if any(
            w["code"] in ("execute_not_found", "ast_roundtrip_unsafe") for w in warnings
        )
        else "simple"
    )

    return {
        "mode": mode,
        "form": {
            "class_name": klass.name.value,
            "tool_name": tool_name,
            "description": description,
            "execution_mode": execution_mode or "direct",
            "needs_context": needs_context,
            "require_manual_read": require_manual_read,
            # BaseTool source does not expose a canonical parameter declaration.
            "params": [],
        },
        "execute_body": exec_body,
        "warnings": warnings,
    }


def _raw_mode_envelope(source: str, reason: str) -> dict:
    return {
        "mode": "raw",
        "form": {
            "class_name": "",
            "tool_name": "",
            "description": "",
            "execution_mode": "direct",
            "needs_context": False,
            "require_manual_read": False,
            "params": [],
        },
        "execute_body": "",
        "warnings": [{"code": "ast_roundtrip_unsafe", "message": reason}],
    }


def _pick_tool_class(tree: cst.Module) -> cst.ClassDef | None:
    """Return an explicit ``BaseTool`` subclass or the first class fallback."""
    for node in tree.body:
        if isinstance(node, cst.ClassDef) and _has_base(node, "BaseTool"):
            return node
    return first_class(tree)


def _has_base(klass: cst.ClassDef, name: str) -> bool:
    for arg in klass.bases or ():
        v = arg.value
        if isinstance(v, cst.Name) and v.value == name:
            return True
        if (
            isinstance(v, cst.Attribute)
            and isinstance(v.attr, cst.Name)
            and v.attr.value == name
        ):
            return True
    return False


def _read_execution_mode(klass: cst.ClassDef) -> str | None:
    """Read a literal ``ExecutionMode`` member returned by the property."""
    for node in klass.body.body:
        if not (
            isinstance(node, cst.FunctionDef) and node.name.value == "execution_mode"
        ):
            continue
        if not isinstance(node.body, cst.IndentedBlock):
            continue
        for stmt in node.body.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for s in stmt.body:
                if isinstance(s, cst.Return) and isinstance(s.value, cst.Attribute):
                    attr = s.value.attr
                    if isinstance(attr, cst.Name):
                        return attr.value.lower()
    return None


def _has_decorators_on_execute(klass: cst.ClassDef) -> bool:
    for node in klass.body.body:
        if (
            isinstance(node, cst.FunctionDef)
            and node.name.value == "_execute"
            and node.decorators
        ):
            return True
    return False


def _to_class_name(name: str) -> str:
    """Convert a module-style name to its tool class name."""
    parts = name.replace("-", "_").split("_")
    return "".join(p[:1].upper() + p[1:] for p in parts if p) + "Tool"
