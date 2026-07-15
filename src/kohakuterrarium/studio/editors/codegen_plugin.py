"""Codegen for ``BasePlugin`` subclasses.

Enabled hooks are rendered as asynchronous methods; omitted hooks inherit
``BasePlugin`` defaults. Parsing collects known hook bodies, while updates rebuild
the regular plugin shape and preserve existing bodies when no replacement is
supplied.
"""

import json

import libcst as cst

from kohakuterrarium.studio.editors.codegen_common import (
    RoundTripError,
    find_class,
    first_class,
    parse,
    read_method_body,
    read_property_string,
)
from kohakuterrarium.studio.editors.plugin_hooks import PLUGIN_HOOKS
from kohakuterrarium.studio.editors.templates import render

# Keep this set aligned with ``PLUGIN_HOOKS`` so parsing and rendering agree.
_HOOK_NAMES = {
    "on_load",
    "on_unload",
    "pre_llm_call",
    "post_llm_call",
    "pre_tool_execute",
    "post_tool_execute",
    "pre_subagent_run",
    "post_subagent_run",
    "on_agent_start",
    "on_agent_stop",
    "on_event",
    "on_interrupt",
    "on_task_promoted",
    "on_compact_start",
    "on_compact_end",
}


def render_new(form: dict) -> str:
    """Scaffold a plugin from identity, priority, hooks, and option metadata."""
    name = form.get("name", "my_plugin")
    class_name = form.get("class_name") or _to_class_name(name)
    priority = int(form.get("priority", 50))
    description = form.get("description", "TODO: describe this plugin")
    enabled_hooks = form.get("enabled_hooks") or []

    hooks = [_hook_context(h) for h in enabled_hooks]

    return render(
        "plugin.py.j2",
        name=name,
        class_name=class_name,
        priority=priority,
        description=description,
        enabled_hooks=hooks,
    )


def sidecar_files(form: dict) -> dict[str, str]:
    """Return the plugin option-schema sidecar when one is declared.

    Plugin constructors conventionally accept an opaque options mapping, so the
    sidecar preserves per-key metadata that cannot be recovered from signatures.
    """
    schema = form.get("options_schema")
    if not isinstance(schema, list) or not schema:
        return {}
    normalized = [_normalize_schema_param(p) for p in schema if isinstance(p, dict)]
    if not normalized:
        return {}
    return {".schema.json": json.dumps(normalized, indent=2) + "\n"}


def _normalize_schema_param(p: dict) -> dict:
    """Normalize an option descriptor and discard unsupported keys."""
    return {
        "name": p.get("name", ""),
        "type_hint": p.get("type_hint") or "",
        "default": p.get("default"),
        "required": bool(p.get("required")),
        "description": p.get("description") or "",
    }


def update_existing(source: str, form: dict, execute_body: str) -> str:
    """Rebuild a plugin with the requested hooks and preserved hook bodies.

    ``execute_body`` is ignored because plugins expose multiple hooks rather than
    one execute method.
    """
    del execute_body

    tree = parse(source)
    class_name = form.get("class_name")
    klass = find_class(tree, class_name) if class_name else first_class(tree)
    if klass is None:
        raise RoundTripError(f"no class found (looking for {class_name!r})")

    # Existing bodies are the fallback for enabled hooks omitted by the form.
    existing_bodies: dict[str, str] = {}
    for node in klass.body.body:
        if isinstance(node, cst.FunctionDef) and node.name.value in _HOOK_NAMES:
            body_src = read_method_body(klass, node.name.value) or ""
            existing_bodies[node.name.value] = body_src

    enabled_hooks = form.get("enabled_hooks") or []

    # An explicitly supplied body wins; otherwise preserve the prior method.
    merged: list[dict] = []
    for h in enabled_hooks:
        hname = h["name"]
        body = h.get("body")
        if body is None or body == "":
            body = existing_bodies.get(hname, "return None")
        merged.append({**h, "body": body})

    # Plugin modules have a regular generated shape, so full template rendering is
    # safer than piecemeal class surgery.
    return render_new(
        {
            **form,
            "class_name": klass.name.value,
            "enabled_hooks": merged,
        }
    )


def parse_back(source: str, sidecar_schema: list | None = None) -> dict:
    """Extract plugin identity, enabled hooks, and optional schema metadata.

    A valid sidecar schema is normalized into form state for subsequent saves.
    """
    warnings: list[dict] = []

    try:
        tree = parse(source)
    except Exception as e:
        return _raw_envelope(f"parse failed: {e}")

    klass = _pick_plugin_class(tree)
    if klass is None:
        return _raw_envelope("no BasePlugin-shaped class found")

    name = read_property_string(klass, "name") or ""
    priority = _read_int_attr(klass, "priority", default=50)

    enabled: list[dict] = []
    for node in klass.body.body:
        if isinstance(node, cst.FunctionDef) and node.name.value in _HOOK_NAMES:
            body_src = read_method_body(klass, node.name.value) or ""
            enabled.append(
                {
                    "name": node.name.value,
                    "body": body_src.rstrip(),
                }
            )

    options_schema: list[dict] = []
    if isinstance(sidecar_schema, list):
        options_schema = [
            _normalize_schema_param(p) for p in sidecar_schema if isinstance(p, dict)
        ]

    return {
        "mode": "simple",
        "form": {
            "class_name": klass.name.value,
            "name": name,
            "priority": priority,
            "description": "",
            "enabled_hooks": enabled,
            "options_schema": options_schema,
        },
        "execute_body": "",
        "warnings": warnings,
    }


def _hook_context(h: dict) -> dict:
    """Attach catalog signature metadata to an enabled hook."""
    spec = next((s for s in PLUGIN_HOOKS if s["name"] == h["name"]), None)
    if spec is None:
        # Preserve unknown hooks with a minimal signature instead of dropping them.
        return {
            "name": h["name"],
            "args_signature": "",
            "return_hint": "",
            "body": h.get("body", "return None"),
        }
    return {
        "name": spec["name"],
        "args_signature": spec["args_signature"],
        "return_hint": spec["return_hint"],
        "body": h.get("body") or "return None",
    }


def _pick_plugin_class(tree: cst.Module) -> cst.ClassDef | None:
    for node in tree.body:
        if isinstance(node, cst.ClassDef):
            for b in node.bases or ():
                v = b.value
                if isinstance(v, cst.Name) and v.value == "BasePlugin":
                    return node
                if (
                    isinstance(v, cst.Attribute)
                    and isinstance(v.attr, cst.Name)
                    and v.attr.value == "BasePlugin"
                ):
                    return node
    return first_class(tree)


def _read_int_attr(klass: cst.ClassDef, attr: str, *, default: int) -> int:
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
            if isinstance(value, cst.Integer):
                try:
                    return int(value.value)
                except ValueError:
                    return default
    return default


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
            "name": "",
            "priority": 50,
            "description": "",
            "enabled_hooks": [],
        },
        "execute_body": "",
        "warnings": [{"code": "ast_roundtrip_unsafe", "message": reason}],
    }


def _to_class_name(name: str) -> str:
    parts = name.replace("-", "_").split("_")
    return "".join(p[:1].upper() + p[1:] for p in parts if p) + "Plugin"
