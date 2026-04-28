"""Codegen for ``BaseTrigger`` subclasses.

Round-trip: find the subclass, replace the body of
``wait_for_trigger`` (the one required method) + universal
metadata class attributes.
"""

import libcst as cst

from kohakuterrarium.studio.editors.codegen_common import (
    RoundTripError,
    find_class,
    first_class,
    parse,
    read_class_attr_bool,
    read_method_body,
    replace_class_in_module,
    replace_method_body,
)
from kohakuterrarium.studio.editors.templates import render


def render_new(form: dict) -> str:
    name = form.get("name", "my_trigger")
    class_name = form.get("class_name") or _to_class_name(name)
    return render(
        "trigger.py.j2",
        class_name=class_name,
        name=name,
        universal=bool(form.get("universal", False)),
        setup_tool_name=form.get("setup_tool_name", ""),
        setup_description=form.get("setup_description", ""),
        wait_for_trigger_body=form.get("wait_for_trigger_body") or "return None",
    )


def update_existing(source: str, form: dict, execute_body: str) -> str:
    tree = parse(source)
    class_name = form.get("class_name")
    klass = find_class(tree, class_name) if class_name else first_class(tree)
    if klass is None:
        raise RoundTripError(f"no class found (looking for {class_name!r})")

    body = execute_body
    if not body and "wait_for_trigger_body" in form:
        body = form["wait_for_trigger_body"]
    if body:
        klass = replace_method_body(klass, "wait_for_trigger", body)

    return replace_class_in_module(tree, klass.name.value, klass).code


def parse_back(source: str) -> dict:
    try:
        tree = parse(source)
    except Exception as e:
        return _raw_envelope(f"parse failed: {e}")

    klass = _pick_trigger_class(tree)
    if klass is None:
        return _raw_envelope("no BaseTrigger subclass found")

    wait_body = read_method_body(klass, "wait_for_trigger")
    warnings: list[dict] = []
    if wait_body is None:
        warnings.append(
            {
                "code": "wait_for_trigger_not_found",
                "message": "wait_for_trigger method not found — use raw mode",
            }
        )

    return {
        "mode": "simple" if wait_body else "raw",
        "form": {
            "class_name": klass.name.value,
            "universal": read_class_attr_bool(klass, "universal"),
            "setup_tool_name": _read_str_classvar(klass, "setup_tool_name") or "",
            "setup_description": _read_str_classvar(klass, "setup_description") or "",
        },
        "execute_body": wait_body or "",
        "warnings": warnings,
    }


# ---- helpers --------------------------------------------------------


def _pick_trigger_class(tree: cst.Module) -> cst.ClassDef | None:
    for node in tree.body:
        if isinstance(node, cst.ClassDef):
            for b in node.bases or ():
                v = b.value
                if isinstance(v, cst.Name) and v.value == "BaseTrigger":
                    return node
                if (
                    isinstance(v, cst.Attribute)
                    and isinstance(v.attr, cst.Name)
                    and v.attr.value == "BaseTrigger"
                ):
                    return node
    return first_class(tree)


def _read_str_classvar(klass: cst.ClassDef, attr: str) -> str | None:
    for node in klass.body.body:
        if not isinstance(node, cst.SimpleStatementLine):
            continue
        for stmt in node.body:
            if not isinstance(stmt, (cst.Assign, cst.AnnAssign)):
                continue
            tgt = _assign_target(stmt)
            if tgt != attr:
                continue
            value = stmt.value
            if isinstance(value, cst.SimpleString):
                try:
                    return value.evaluated_value
                except Exception:
                    pass
    return None


def _assign_target(stmt: cst.Assign | cst.AnnAssign) -> str | None:
    if isinstance(stmt, cst.Assign):
        if len(stmt.targets) != 1:
            return None
        tgt = stmt.targets[0].target
    else:
        tgt = stmt.target
    if isinstance(tgt, cst.Name):
        return tgt.value
    return None


def _raw_envelope(reason: str) -> dict:
    return {
        "mode": "raw",
        "form": {
            "class_name": "",
            "universal": False,
            "setup_tool_name": "",
            "setup_description": "",
        },
        "execute_body": "",
        "warnings": [{"code": "ast_roundtrip_unsafe", "message": reason}],
    }


def _to_class_name(name: str) -> str:
    parts = name.replace("-", "_").split("_")
    return "".join(p[:1].upper() + p[1:] for p in parts if p) + "Trigger"
