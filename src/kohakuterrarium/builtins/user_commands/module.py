"""``/module`` slash command — unified runtime configuration of plugins,
provider-native tools, and any future module type.

Mirrors the Vue ``ModulesPanel.vue`` surface as a text command: every
operation the panel supports has an in-chat equivalent. CLI and TUI both
render the result; for ``edit``, the command spawns ``$EDITOR`` with the
module's options as YAML so list/dict values are pleasant to type.

Usage forms (parsed from the slash-command argument string)::

    /module                              # list all modules across types
    /module list [plugin|native_tool]    # list, optionally type-filtered
    /module show <name>                  # description + options table
    /module enable <name>                # toggle plugin on
    /module disable <name>               # toggle plugin off
    /module toggle <name>                # flip enable state
    /module set <name> <key> <value>...  # apply one option
                                         # value parsed as JSON if it
                                         # looks JSON-y (starts with
                                         # [/{/"/digit/+/-, or true/
                                         # false/null), else as string
    /module edit <name>                  # spawn $EDITOR with YAML;
                                         # apply on save, surface
                                         # validation errors
    /module reset <name> [<key>]         # clear one override or all

When a name is ambiguous (matches both a plugin and a native tool, e.g.
both call something ``image_gen``) prefix with the type:
``plugin/permgate`` or ``native_tool/image_gen``.

Backward compatibility: ``/plugin`` continues to work as a plugins-only
shortcut. ``/module`` is the canonical surface.
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from typing import Any

import yaml

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)

# ── Subcommand dispatch ─────────────────────────────────────────────


@register_user_command("module")
class ModuleCommand(BaseUserCommand):
    name = "module"
    aliases = ["modules", "mod"]
    description = (
        "List, inspect, toggle, or edit module options at runtime "
        "(plugins + provider-native tools). "
        "/module enable|disable|set|edit|reset <name> …"
    )
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="No agent context.")

        try:
            tokens = shlex.split(args or "")
        except ValueError as exc:
            return UserCommandResult(error=f"Failed to parse arguments: {exc}")

        sub = tokens[0].lower() if tokens else "list"
        rest = tokens[1:]

        match sub:
            case "list" | "":
                return UserCommandResult(output=_render_list(agent, rest))
            case "show":
                if not rest:
                    return UserCommandResult(error="Usage: /module show <name>")
                m, err = _resolve_or_error(agent, rest[0])
                if err:
                    return UserCommandResult(error=err)
                return UserCommandResult(output=_render_show_module(m))
            case "enable":
                return await _do_toggle(agent, rest, want=True)
            case "disable":
                return await _do_toggle(agent, rest, want=False)
            case "toggle":
                return await _do_toggle(agent, rest, want=None)
            case "set":
                return _do_set(agent, rest)
            case "edit":
                return _do_edit(agent, rest)
            case "reset":
                return _do_reset(agent, rest)
            case _:
                return UserCommandResult(
                    error=(
                        f"Unknown subcommand: {sub!r}. "
                        "Try /module (list / show / enable / disable / "
                        "toggle / set / edit / reset)."
                    )
                )


# ── Inventory + lookup ──────────────────────────────────────────────


def _inventory(agent: Any) -> list[dict[str, Any]]:
    """Collect every configurable module on the agent.

    Mirrors the studio dispatcher in ``creature_modules`` but talks
    directly to the in-process agent helpers — slash commands run with
    the live agent in scope.
    """
    out: list[dict[str, Any]] = []
    out.extend(_inventory_plugins(agent))
    out.extend(_inventory_native_tools(agent))
    return out


def _inventory_plugins(agent: Any) -> list[dict[str, Any]]:
    mgr = getattr(agent, "plugins", None)
    if not mgr:
        return []
    return [
        {
            "type": "plugin",
            "name": entry["name"],
            "description": entry.get("description", "") or "",
            "schema": entry.get("schema", {}) or {},
            "options": entry.get("options", {}) or {},
            "enabled": entry.get("enabled", True),
            "priority": entry.get("priority"),
        }
        for entry in mgr.list_plugins_with_options()
    ]


def _inventory_native_tools(agent: Any) -> list[dict[str, Any]]:
    registry = getattr(agent, "registry", None)
    helper = getattr(agent, "native_tool_options", None)
    if registry is None:
        return []
    out: list[dict[str, Any]] = []
    for name in sorted(registry.list_tools()):
        tool = registry.get_tool(name)
        if tool is None or not getattr(tool, "is_provider_native", False):
            continue
        schema_fn = getattr(type(tool), "provider_native_option_schema", None)
        try:
            schema = schema_fn() if callable(schema_fn) else {}
        except Exception:
            schema = {}
        if not schema:
            continue
        values = helper.get(name) if helper else {}
        out.append(
            {
                "type": "native_tool",
                "name": name,
                "description": getattr(tool, "description", "") or "",
                "schema": schema or {},
                "options": values or {},
                "enabled": None,
                "priority": None,
            }
        )
    return out


def _resolve_or_error(agent: Any, ref: str) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve and return ``(module, error_message)`` — exactly one is set."""
    inv = _inventory(agent)
    if "/" in ref:
        type_part, _, name_part = ref.partition("/")
        for m in inv:
            if m["type"] == type_part and m["name"] == name_part:
                return m, None
        return None, f"Module not found: {ref!r}"
    matches = [m for m in inv if m["name"] == ref]
    if not matches:
        return None, f"Module not found: {ref!r}"
    if len(matches) > 1:
        types = ", ".join(f"{m['type']}/{ref}" for m in matches)
        return None, (
            f"Ambiguous: {ref!r} matches multiple types. " f"Use one of: {types}"
        )
    return matches[0], None


# ── Rendering ───────────────────────────────────────────────────────


def _status_glyph(m: dict[str, Any]) -> str:
    if m["enabled"] is True:
        return "●"  # filled circle
    if m["enabled"] is False:
        return "○"  # empty circle
    return "-"


def _sort_key(m: dict[str, Any]) -> tuple[int, str]:
    p = m.get("priority")
    return (50 if p is None else int(p), m["name"])


def _render_list(agent: Any, rest: list[str]) -> str:
    inv = _inventory(agent)
    if rest:
        type_filter = rest[0]
        inv = [m for m in inv if m["type"] == type_filter]
    if not inv:
        return "No configurable modules."

    # Group by type. Within plugins: enabled-on-top, sorted by priority
    # (lower runs first, per BasePlugin convention). Within native
    # tools: alphabetical.
    out: list[str] = []
    plugins = [m for m in inv if m["type"] == "plugin"]
    native_tools = [m for m in inv if m["type"] == "native_tool"]

    if plugins:
        out.append("Plugins")
        for label, want in (("Enabled", True), ("Disabled", False)):
            group = sorted((m for m in plugins if m["enabled"] is want), key=_sort_key)
            if not group:
                continue
            out.append(f"  {label}:")
            for m in group:
                out.append(_format_row(m))

    if native_tools:
        if plugins:
            out.append("")
        out.append("Native tools")
        for m in sorted(native_tools, key=_sort_key):
            out.append(_format_row(m))

    out.append("")
    out.append("Edit: /module show <name>  ·  /module set <name> <key> <value>")
    return "\n".join(out)


def _format_row(m: dict[str, Any]) -> str:
    glyph = _status_glyph(m)
    name = m["name"]
    pr = m.get("priority")
    pr_part = f"p{pr}" if pr is not None else "  "
    n_opts = len(m.get("schema") or {})
    opts_part = f"{n_opts} opt" if n_opts else "      "
    return f"    {glyph} {name:<24} {pr_part:>4}  {opts_part}"


def _render_show_module(m: dict[str, Any]) -> str:
    schema = m.get("schema") or {}
    options = m.get("options") or {}

    out: list[str] = [f"{m['type']}/{m['name']}"]
    if m.get("priority") is not None:
        out[-1] += f"  (priority {m['priority']})"
    if m["enabled"] is not None:
        out[-1] += f"  [{'enabled' if m['enabled'] else 'disabled'}]"
    if m.get("description"):
        out.append(f"  {m['description']}")

    if not schema:
        out.append("")
        out.append("  No runtime-mutable options.")
        return "\n".join(out)

    out.append("")
    out.append("  Options:")
    for key, spec in schema.items():
        spec = spec or {}
        kind = spec.get("type", "string")
        choices = spec.get("values")
        choice_part = f": {', '.join(map(str, choices))}" if choices else ""
        current = options.get(key, spec.get("default"))
        out.append(f"    {key}  [{kind}{choice_part}]")
        out.append(f"      = {current!r}")
        if spec.get("doc"):
            out.append(f"      {spec['doc']}")

    out.append("")
    out.append(
        f"  Edit: /module set {m['name']} <key> <value>  "
        f"·  /module edit {m['name']}"
    )
    return "\n".join(out)


# ── Mutating subcommands ────────────────────────────────────────────


async def _do_toggle(
    agent: Any, rest: list[str], *, want: bool | None
) -> UserCommandResult:
    if not rest:
        verb = "enable" if want is True else "disable" if want is False else "toggle"
        return UserCommandResult(error=f"Usage: /module {verb} <name>")
    m, err = _resolve_or_error(agent, rest[0])
    if err:
        return UserCommandResult(error=err)
    if m["type"] != "plugin":
        return UserCommandResult(
            error=f"Cannot toggle {m['type']}; only plugins have an enabled state."
        )
    mgr = agent.plugins
    name = m["name"]
    is_on = mgr.is_enabled(name)
    target = (not is_on) if want is None else want
    if target:
        if not mgr.enable(name):
            return UserCommandResult(error=f"Plugin not found: {name}")
        await mgr.load_pending()
        return UserCommandResult(output=f"Plugin {name!r} enabled.")
    if not mgr.disable(name):
        return UserCommandResult(error=f"Plugin not found: {name}")
    return UserCommandResult(output=f"Plugin {name!r} disabled.")


def _do_set(agent: Any, rest: list[str]) -> UserCommandResult:
    if len(rest) < 3:
        return UserCommandResult(error="Usage: /module set <name> <key> <value>")
    name_ref, key, *value_tokens = rest
    raw = " ".join(value_tokens)
    parsed = _parse_value(raw)
    m, err = _resolve_or_error(agent, name_ref)
    if err:
        return UserCommandResult(error=err)
    try:
        applied = _apply_options(agent, m, {key: parsed})
    except (KeyError, ValueError) as exc:
        return UserCommandResult(error=str(exc))
    return UserCommandResult(
        output=f"{m['type']}/{m['name']}.{key} = {applied.get(key)!r}"
    )


def _do_reset(agent: Any, rest: list[str]) -> UserCommandResult:
    if not rest:
        return UserCommandResult(error="Usage: /module reset <name> [<key>]")
    name_ref = rest[0]
    key = rest[1] if len(rest) > 1 else None
    m, err = _resolve_or_error(agent, name_ref)
    if err:
        return UserCommandResult(error=err)
    if key is None:
        # Reset all overrides for this module — for plugins this means
        # repopulating defaults from option_schema; for native tools it
        # clears the override map.
        if m["type"] == "plugin":
            schema = m.get("schema") or {}
            defaults = {k: (s or {}).get("default") for k, s in schema.items()}
            try:
                _apply_options(agent, m, defaults)
            except (KeyError, ValueError) as exc:
                return UserCommandResult(error=str(exc))
            return UserCommandResult(
                output=f"Reset all options on plugin/{m['name']} to defaults."
            )
        helper = getattr(agent, "native_tool_options", None)
        if helper is None:
            return UserCommandResult(error="No native_tool_options helper.")
        helper.set(m["name"], {})
        return UserCommandResult(
            output=f"Cleared overrides on native_tool/{m['name']}."
        )

    schema = m.get("schema") or {}
    if key not in schema:
        return UserCommandResult(
            error=f"Unknown option {key!r} for {m['type']}/{m['name']}."
        )
    default = (schema[key] or {}).get("default")
    try:
        _apply_options(agent, m, {key: default})
    except (KeyError, ValueError) as exc:
        return UserCommandResult(error=str(exc))
    return UserCommandResult(
        output=f"{m['type']}/{m['name']}.{key} = {default!r}  (default)"
    )


def _do_edit(agent: Any, rest: list[str]) -> UserCommandResult:
    if not rest:
        return UserCommandResult(error="Usage: /module edit <name>")
    m, err = _resolve_or_error(agent, rest[0])
    if err:
        return UserCommandResult(error=err)
    schema = m.get("schema") or {}
    if not schema:
        return UserCommandResult(
            output=f"{m['type']}/{m['name']} has no runtime-mutable options."
        )
    editor = _resolve_editor()
    if not editor:
        return UserCommandResult(
            error=(
                "No $EDITOR / $VISUAL set. "
                "Use /module set <name> <key> <value> for individual edits."
            )
        )
    try:
        edited = _spawn_editor_with_yaml(editor, m, schema)
    except RuntimeError as exc:
        return UserCommandResult(error=str(exc))
    if edited is None:
        return UserCommandResult(output="Edit cancelled (file unchanged).")
    try:
        applied = _apply_options(agent, m, edited)
    except (KeyError, ValueError) as exc:
        return UserCommandResult(
            error=f"Validation failed: {exc}. (No changes applied.)"
        )
    return UserCommandResult(
        output=(
            f"Saved {m['type']}/{m['name']} options. "
            f"({len(applied)} key{'s' if len(applied) != 1 else ''} active.)"
        )
    )


# ── Helpers (apply + parse + edit) ──────────────────────────────────


def _apply_options(
    agent: Any, m: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    """Route the option write to the right helper for the module type.

    Returns the post-merge options dict the helper applied, so the
    caller can echo it back to the user.
    """
    if m["type"] == "plugin":
        helper = getattr(agent, "plugin_options", None)
        if helper is None:
            raise RuntimeError("agent has no plugin_options helper")
        return helper.set(m["name"], values)
    if m["type"] == "native_tool":
        helper = getattr(agent, "native_tool_options", None)
        if helper is None:
            raise RuntimeError("agent has no native_tool_options helper")
        # native_tool_options.set replaces the override map entirely;
        # we want a merge (set one key, leave others). Read current
        # then merge.
        current = dict(helper.get(m["name"]))
        current.update(values)
        return helper.set(m["name"], current)
    raise ValueError(f"Unsupported module type: {m['type']!r}")


def _parse_value(raw: str) -> Any:
    """Parse a value string permissively.

    JSON-detection: if the trimmed text starts with ``[`` ``{`` ``"``,
    a digit, ``+``, or ``-``, or equals one of the JSON literals
    ``true`` / ``false`` / ``null``, attempt ``json.loads``. On failure
    or non-match, return as a bare string. Empty string maps to None.
    """
    s = (raw or "").strip()
    if not s:
        return None
    if s in ("true", "false", "null"):
        return json.loads(s)
    if s[:1] in '[{"' or s[:1].isdigit() or s[:1] in "+-":
        try:
            return json.loads(s)
        except (TypeError, ValueError):
            return s
    return s


def _resolve_editor() -> str:
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""


def _spawn_editor_with_yaml(
    editor: str, m: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any] | None:
    """Open ``$EDITOR`` with the module's options as YAML.

    Returns the parsed dict on save, or ``None`` if the user closed the
    editor without modifying the file (we treat that as "cancel").
    Raises RuntimeError on YAML parse / I/O failures.
    """
    content = _build_yaml_template(m, schema)
    fd, path = tempfile.mkstemp(prefix=f"kt-module-{m['name']}-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        before_mtime = os.stat(path).st_mtime_ns

        argv = shlex.split(editor) + [path]
        rc = subprocess.call(
            argv, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr
        )
        if rc != 0:
            raise RuntimeError(f"Editor exited non-zero: {rc}")

        after_mtime = os.stat(path).st_mtime_ns
        if after_mtime == before_mtime:
            return None  # cancelled

        with open(path, encoding="utf-8") as f:
            edited_text = f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    try:
        parsed = yaml.safe_load(edited_text) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"YAML parse error: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Edited file did not contain a YAML mapping.")
    return parsed


def _build_yaml_template(m: dict[str, Any], schema: dict[str, Any]) -> str:
    """Build the editable YAML buffer with schema docs as comments."""
    options = m.get("options") or {}
    lines: list[str] = []
    lines.append(f"# {m['type']}/{m['name']}")
    if m.get("description"):
        lines.append(f"# {m['description']}")
    lines.append("# Edit values below. Save and exit to apply; close")
    lines.append("# without saving to cancel.")
    lines.append("")
    for key, spec in schema.items():
        spec = spec or {}
        kind = spec.get("type", "string")
        choices = spec.get("values")
        choice_part = f"  ({', '.join(map(str, choices))})" if choices else ""
        if spec.get("doc"):
            lines.append(f"# {spec['doc']}")
        lines.append(f"# type: {kind}{choice_part}")
        current = options.get(key, spec.get("default"))
        try:
            rendered = yaml.safe_dump(
                {key: current}, default_flow_style=False, sort_keys=False
            ).rstrip("\n")
        except yaml.YAMLError:
            rendered = f"{key}: {current!r}"
        lines.append(rendered)
        lines.append("")
    return "\n".join(lines)
