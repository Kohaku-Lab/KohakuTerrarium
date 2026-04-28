"""Codegen for ``BasePlugin`` subclasses.

Plugins are hook-driven: the user enables a set of hooks; each
enabled hook gets its own ``async def`` method. Disabled hooks
are simply omitted (BasePlugin's defaults take over).

Round-trip strategy:
 * On parse: walk the class, collect every method whose name
   matches a known hook, record its body text.
 * On update: rebuild the class with only the enabled hooks,
   preserving existing bodies verbatim where the caller didn't
   supply a new body, and replacing where they did.
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

# Hook names must match catalog.PLUGIN_HOOKS.
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


# ----------------------------------------------------------------------
# Scaffold
# ----------------------------------------------------------------------


def render_new(form: dict) -> str:
    """Scaffold a plugin module.

    Expected form keys: ``name``, ``class_name`` (optional),
    ``priority`` (int, default 50), ``description``,
    ``enabled_hooks`` (list of ``{name, body}`` dicts),
    ``options_schema`` (optional list of ``{name, type_hint,
    default, required, description}`` param descriptors — written
    alongside the .py as a sidecar; see :func:`sidecar_files`).
    """
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
    """Return ``{relative_filename: content}`` for sidecars to write
    alongside the plugin's .py file.

    Plugins use an ``options: dict`` constructor by convention, so
    their author-declared option keys can't be recovered from the
    ``__init__`` signature alone. When the form carries an
    ``options_schema``, we persist it as a sibling
    ``<plugin>.schema.json`` so ``introspect.py`` can surface per-key
    docs back to the creature editor's pool.

    Returns an empty dict when no schema is declared — caller writes
    nothing extra.
    """
    schema = form.get("options_schema")
    if not isinstance(schema, list) or not schema:
        return {}
    normalized = [_normalize_schema_param(p) for p in schema if isinstance(p, dict)]
    if not normalized:
        return {}
    return {".schema.json": json.dumps(normalized, indent=2) + "\n"}


def _normalize_schema_param(p: dict) -> dict:
    """Coerce an incoming param dict into the canonical shape we
    persist. Unknown keys are dropped to keep the sidecar tight."""
    return {
        "name": p.get("name", ""),
        "type_hint": p.get("type_hint") or "",
        "default": p.get("default"),
        "required": bool(p.get("required")),
        "description": p.get("description") or "",
    }


# ----------------------------------------------------------------------
# Round-trip update
# ----------------------------------------------------------------------


def update_existing(source: str, form: dict, execute_body: str) -> str:
    """Rewrite the plugin class with the requested hook set.

    *execute_body* is unused for plugins (they have multiple
    hooks, not one ``_execute``). Kept in the signature for
    Codegen Protocol compatibility.
    """
    del execute_body  # not used

    tree = parse(source)
    class_name = form.get("class_name")
    klass = find_class(tree, class_name) if class_name else first_class(tree)
    if klass is None:
        raise RoundTripError(f"no class found (looking for {class_name!r})")

    # Gather existing hook bodies so we can preserve them if the
    # caller doesn't supply a new body for a hook.
    existing_bodies: dict[str, str] = {}
    for node in klass.body.body:
        if isinstance(node, cst.FunctionDef) and node.name.value in _HOOK_NAMES:
            body_src = read_method_body(klass, node.name.value) or ""
            existing_bodies[node.name.value] = body_src

    enabled_hooks = form.get("enabled_hooks") or []

    # Merge: for each enabled hook, use incoming body if provided,
    # else fall back to existing.
    merged: list[dict] = []
    for h in enabled_hooks:
        hname = h["name"]
        body = h.get("body")
        if body is None or body == "":
            body = existing_bodies.get(hname, "return None")
        merged.append({**h, "body": body})

    # Re-render the whole file from the template. We keep the
    # user's custom ``name``/``priority`` fields via the form,
    # and preserve the module docstring + imports via the
    # template's fixed header. This is simpler than AST-surgery
    # for plugins whose shape is fairly regular.
    return render_new(
        {
            **form,
            "class_name": klass.name.value,
            "enabled_hooks": merged,
        }
    )


# ----------------------------------------------------------------------
# Parse back
# ----------------------------------------------------------------------


def parse_back(source: str, sidecar_schema: list | None = None) -> dict:
    """Extract form state from a plugin source file.

    *sidecar_schema* — optional list loaded from ``<name>.schema.json``.
    When present, it's round-tripped into ``form.options_schema`` so the
    OptionsSchemaEditor can re-render the author's previous choices.
    """
    warnings: list[dict] = []

    try:
        tree = parse(source)
    except Exception as e:
        return _raw_envelope(f"parse failed: {e}")

    klass = _pick_plugin_class(tree)
    if klass is None:
        return _raw_envelope("no BasePlugin-shaped class found")

    # Extract identity
    name = read_property_string(klass, "name") or ""
    priority = _read_int_attr(klass, "priority", default=50)

    # Enumerate enabled hooks
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


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _hook_context(h: dict) -> dict:
    """Enrich an incoming {name, body} with signature info."""
    spec = next((s for s in PLUGIN_HOOKS if s["name"] == h["name"]), None)
    if spec is None:
        # Unknown hook — keep going with minimal signature
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
