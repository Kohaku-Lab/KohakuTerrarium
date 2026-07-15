"""Codegen for input / output modules.

Input and output modules expose a small editable surface: class identity and the
body of one protocol method. LibCST updates the canonical method while preserving
other source; unsupported shapes fall back to raw mode.
"""

from kohakuterrarium.studio.editors.codegen_common import (
    RoundTripError,
    find_class,
    first_class,
    parse,
    read_method_body,
    replace_class_in_module,
    replace_method_body,
)
from kohakuterrarium.studio.editors.templates import render


def render_new(form: dict) -> str:
    kind = form.get("kind", "input")
    name = form.get("name", f"my_{kind}")
    class_name = form.get("class_name") or _to_class_name(name, kind)
    template = "input.py.j2" if kind == "input" else "output.py.j2"
    return render(
        template,
        class_name=class_name,
        name=name,
        description=form.get("description", f"TODO: describe this {kind}"),
        body=form.get("body") or "raise NotImplementedError",
    )


def update_existing(source: str, form: dict, execute_body: str) -> str:
    """Replace the first recognized input or output protocol method body.

    Canonical methods take precedence over legacy scaffold names.
    """
    body = execute_body or form.get("body") or ""
    if not body:
        return source

    tree = parse(source)
    class_name = form.get("class_name")
    klass = find_class(tree, class_name) if class_name else first_class(tree)
    if klass is None:
        raise RoundTripError("no class found in source")

    # Legacy method names remain readable after the canonical protocol names.
    for method in ("get_input", "write", "read_input", "write_output"):
        if read_method_body(klass, method) is not None:
            klass = replace_method_body(klass, method, body)
            return replace_class_in_module(tree, klass.name.value, klass).code

    raise RoundTripError(
        "no get_input / write method found — use raw mode",
    )


def parse_back(source: str) -> dict:
    try:
        tree = parse(source)
    except Exception as e:
        return _raw_envelope(f"parse failed: {e}")

    klass = first_class(tree)
    if klass is None:
        return _raw_envelope("no class found")

    body = None
    method_name = ""
    for candidate in ("get_input", "write", "read_input", "write_output"):
        b = read_method_body(klass, candidate)
        if b is not None:
            body = b
            method_name = candidate
            break

    if body is None:
        return _raw_envelope("no protocol method found")

    return {
        "mode": "simple",
        "form": {
            "class_name": klass.name.value,
            "description": "",
            "method_name": method_name,
        },
        "execute_body": body,
        "warnings": [],
    }


def _to_class_name(name: str, kind: str) -> str:
    parts = name.replace("-", "_").split("_")
    suffix = "Input" if kind == "input" else "Output"
    return "".join(p[:1].upper() + p[1:] for p in parts if p) + suffix


def _raw_envelope(reason: str) -> dict:
    return {
        "mode": "raw",
        "form": {"class_name": "", "description": ""},
        "execute_body": "",
        "warnings": [{"code": "ast_roundtrip_unsafe", "message": reason}],
    }
