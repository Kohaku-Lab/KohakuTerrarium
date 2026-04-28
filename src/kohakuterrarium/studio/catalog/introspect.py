"""Module introspection for the creature editor's option forms.

Given a creature-config entry (``{kind, name, type, module?, class_name?}``)
return a list of parameter descriptors the frontend can render as a
form. Two sources:

* **Builtin** modules — we return a curated schema per kind. Builtin
  tools don't expose their own option schemas at runtime; they read
  arbitrary ``config.extra`` keys. We surface the common ToolConfig /
  SubAgentConfig fields and leave rarer knobs to the advanced JSON
  escape hatch on the frontend.

* **Custom / package** modules — we AST-parse the user's Python file
  and pull the target class's ``__init__`` signature. This is safe
  (no code execution) and picks up keyword arguments with their type
  hints + literal defaults.

``config`` is never surfaced — it's a framework-injected kwarg on
BaseTool subclasses and has nothing the user should be editing here.
"""

import ast
import importlib.util
from pathlib import Path

from kohakuterrarium.packages.resolve import (
    ensure_package_importable,
    resolve_package_path,
)
from kohakuterrarium.packages.walk import list_packages

# Parameters that we intentionally hide from the form — they're
# framework-level plumbing, not user-facing options.
_HIDDEN_PARAMS = {"config", "self"}


def builtin_schema(kind: str) -> dict:
    """Curated schema for a builtin module of the given kind.

    ``kind`` is the plural form ("tools", "subagents", …). Returns
    a dict ``{params, warnings}`` where ``params`` is a list of
    ``{name, type_hint, default, required, description}`` entries.
    """
    if kind == "tools":
        return {
            "params": [
                {
                    "name": "timeout",
                    "type_hint": "float",
                    "default": 60.0,
                    "required": False,
                    "description": (
                        "Max seconds before the tool is cancelled. "
                        "0 disables the timeout."
                    ),
                },
                {
                    "name": "max_output",
                    "type_hint": "int",
                    "default": 65536,
                    "required": False,
                    "description": (
                        "Central UTF-8 byte cap for tool text output/text parts. "
                        "0 = unlimited."
                    ),
                },
                {
                    "name": "notify_controller_on_background_complete",
                    "type_hint": "bool",
                    "default": True,
                    "required": False,
                    "description": (
                        "Push an event back into the controller loop "
                        "when a backgrounded run of this tool finishes."
                    ),
                },
            ],
            "warnings": [],
        }
    if kind == "subagents":
        return {
            "params": [
                {
                    "name": "max_turns",
                    "type_hint": "int",
                    "default": 0,
                    "required": False,
                    "description": "Max conversation turns (0 = unlimited).",
                },
                {
                    "name": "timeout",
                    "type_hint": "float",
                    "default": 0,
                    "required": False,
                    "description": "Max execution seconds (0 = none).",
                },
                {
                    "name": "interactive",
                    "type_hint": "bool",
                    "default": False,
                    "required": False,
                    "description": (
                        "Keep the sub-agent alive between turns so it "
                        "can receive context updates from the parent."
                    ),
                },
                {
                    "name": "can_modify",
                    "type_hint": "bool",
                    "default": False,
                    "required": False,
                    "description": (
                        "Allow this sub-agent to use file-modifying "
                        "tools (write / edit)."
                    ),
                },
                {
                    "name": "return_as_context",
                    "type_hint": "bool",
                    "default": False,
                    "required": False,
                    "description": (
                        "Return the sub-agent's output text to the "
                        "parent as additional context."
                    ),
                },
            ],
            "warnings": [],
        }
    if kind == "plugins":
        return {
            "params": [
                {
                    "name": "priority",
                    "type_hint": "int",
                    "default": 50,
                    "required": False,
                    "description": (
                        "Hook execution order. Lower priorities run "
                        "first in pre-hooks, last in post-hooks."
                    ),
                },
            ],
            "warnings": [],
        }
    if kind == "triggers":
        # Universal setup-tool triggers have their own per-tool schemas
        # reachable via /api/studio/catalog/triggers. We return an empty
        # schema here so the accordion still renders (identity fields).
        return {"params": [], "warnings": []}
    return {"params": [], "warnings": []}


def custom_schema(
    source: str,
    class_name: str | None,
    sidecar_schema: list | None = None,
) -> dict:
    """AST-parse a source file and extract the target class's
    ``__init__`` signature. Pass ``class_name=None`` to use the first
    class in the module.

    *sidecar_schema* — optional per-key descriptor list (loaded from
    a sibling ``.schema.json``). When the class's ``__init__`` takes a
    single ``options: dict`` parameter, the plugin's configurable
    surface is the sidecar's keys rather than that one anonymous
    dict — so we return the sidecar list directly in that case. For
    classes with a richer ``__init__``, the sidecar is ignored.

    Returns ``{params, warnings}``. Warnings surface problems without
    blocking — the user still sees whatever params we did manage to
    parse, plus a hint about what we couldn't recover.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {
            "params": [],
            "warnings": [
                {
                    "code": "syntax_error",
                    "message": f"line {e.lineno}: {e.msg}",
                }
            ],
        }

    target: ast.ClassDef | None = None
    if class_name:
        # Specific class requested — no fallback if missing.
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                target = node
                break
        if target is None:
            return {
                "params": [],
                "warnings": [
                    {
                        "code": "class_not_found",
                        "message": f"class {class_name!r} not found in source",
                    }
                ],
            }
    else:
        # No class name given — fall back to the first top-level class.
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                target = node
                break
        if target is None:
            return {
                "params": [],
                "warnings": [
                    {
                        "code": "class_not_found",
                        "message": "no class found in source",
                    }
                ],
            }

    init: ast.FunctionDef | None = None
    for item in target.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            init = item
            break
    if init is None:
        return {"params": [], "warnings": []}

    params, warnings = _extract_init_params(init)

    # Options-dict plugins: replace the anonymous ``options: dict`` with
    # the sidecar's per-key descriptors so consumers see a real form.
    if sidecar_schema is not None and _is_options_dict_init(params):
        return {
            "params": [_normalize_sidecar_param(p) for p in sidecar_schema],
            "warnings": warnings,
        }

    return {"params": params, "warnings": warnings}


def _is_options_dict_init(params: list[dict]) -> bool:
    """True when the class's editable surface is a single
    ``options: dict`` parameter (the plugin-options convention)."""
    if len(params) != 1:
        return False
    p = params[0]
    if p.get("name") != "options":
        return False
    hint = (p.get("type_hint") or "").lower()
    return hint.startswith("dict") or "dict" in hint or hint == "any"


def _normalize_sidecar_param(raw: dict) -> dict:
    """Coerce a sidecar entry into the same shape ``_extract_init_params``
    produces so the frontend's ``SchemaFormField`` renders uniformly."""
    if not isinstance(raw, dict):
        return {
            "name": "",
            "type_hint": "",
            "default": None,
            "required": False,
            "description": "",
        }
    return {
        "name": raw.get("name", ""),
        "type_hint": raw.get("type_hint") or "",
        "default": raw.get("default"),
        "required": bool(raw.get("required")),
        "description": raw.get("description") or "",
    }


def _extract_init_params(
    func: ast.FunctionDef,
) -> tuple[list[dict], list[dict]]:
    args = func.args
    warnings: list[dict] = []

    positional: list[ast.arg] = list(args.args or [])
    if positional and positional[0].arg == "self":
        positional = positional[1:]

    defaults = list(args.defaults or [])
    defaults_start = len(positional) - len(defaults)

    params: list[dict] = []
    for i, arg in enumerate(positional):
        if arg.arg in _HIDDEN_PARAMS:
            continue
        has_default = i >= defaults_start
        default_value = None
        if has_default:
            default_node = defaults[i - defaults_start]
            try:
                default_value = ast.literal_eval(default_node)
            except (ValueError, SyntaxError):
                default_value = None

        type_hint: str | None = None
        if arg.annotation is not None:
            try:
                type_hint = ast.unparse(arg.annotation)
            except Exception:
                type_hint = None

        params.append(
            {
                "name": arg.arg,
                "type_hint": type_hint,
                "default": default_value,
                "required": not has_default,
                "description": "",
            }
        )

    # Keyword-only args (after *)
    kw_only: list[ast.arg] = list(args.kwonlyargs or [])
    kw_defaults: list = list(args.kw_defaults or [])
    for i, arg in enumerate(kw_only):
        if arg.arg in _HIDDEN_PARAMS:
            continue
        default_node = kw_defaults[i] if i < len(kw_defaults) else None
        has_default = default_node is not None
        default_value = None
        if has_default:
            try:
                default_value = ast.literal_eval(default_node)
            except (ValueError, SyntaxError):
                default_value = None
        type_hint = None
        if arg.annotation is not None:
            try:
                type_hint = ast.unparse(arg.annotation)
            except Exception:
                type_hint = None
        params.append(
            {
                "name": arg.arg,
                "type_hint": type_hint,
                "default": default_value,
                "required": not has_default,
                "description": "",
            }
        )

    if args.vararg or args.kwarg:
        warnings.append(
            {
                "code": "variadic_ignored",
                "message": (
                    "This class accepts *args or **kwargs — only named "
                    "parameters are editable through the form."
                ),
            }
        )

    return params, warnings


def resolve_module_source(workspace_root: Path, module: str) -> str | None:
    """Resolve a ``module:`` reference to its on-disk source.

    Accepts:
      * relative paths (``./custom/send_discord.py``, ``custom/tool.py``)
      * absolute paths
      * dotted module paths (``modules.tools.my_tool``)
      * package references (``@pkg/path``) — resolved via packages.py

    Returns the file's text or None if we couldn't find it.
    """
    if not module:
        return None

    # @package references
    if module.startswith("@"):
        try:
            p = resolve_package_path(module)
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except Exception:
            return None
        return None

    # Relative / absolute file path
    p = Path(module).expanduser()
    if not p.is_absolute():
        p = (workspace_root / p).resolve()
    if p.suffix == ".py" and p.is_file():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    # Dotted module path — try the workspace tree first, then fall
    # back to installed packages via importlib.find_spec.
    if "." in module and not module.endswith(".py"):
        candidate = workspace_root / (module.replace(".", "/") + ".py")
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except Exception:
                return None

        # Make all installed kt packages importable and look for the
        # dotted path there. This covers kt-biome's
        # ``kt_biome.plugins.cost_tracker`` and similar.
        try:
            for pkg in list_packages():
                ensure_package_importable(pkg["name"])

            spec = importlib.util.find_spec(module)
        except Exception:
            spec = None

        if spec is not None and spec.origin and spec.origin.endswith(".py"):
            try:
                return Path(spec.origin).read_text(encoding="utf-8")
            except Exception:
                return None

    return None
