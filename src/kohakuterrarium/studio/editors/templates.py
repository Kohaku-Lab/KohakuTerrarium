"""Jinja template rendering for scaffolding.

Provides the shared Jinja environment used by creature scaffolding and per-kind
code-generation modules, plus the import-block and literal helpers templates use.
"""

import ast
import json
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

# Template data lives beside this module so all editor code uses one loader root.
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates_data"
_WIDTH = 88


def _pystr(value) -> str:
    """A double-quoted Python string literal."""
    return json.dumps(str(value), ensure_ascii=False)


def _flat(value) -> str:
    if isinstance(value, str):
        return _pystr(value)
    if isinstance(value, dict):
        return (
            "{" + ", ".join(f"{_flat(k)}: {_flat(v)}" for k, v in value.items()) + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_flat(v) for v in value) + "]"
    return repr(value)


def py_literal(value, indent: int = 0, lead: int = 0, tail: int = 0) -> str:
    """A black-style Python literal for ``value``.

    The line holding it is indented by ``indent`` and has ``lead`` characters
    before the literal and ``tail`` after it.
    """
    flat = _flat(value)
    fits = indent + lead + len(flat) + tail <= _WIDTH
    if fits or not isinstance(value, (dict, list, tuple)):
        return flat
    inner = " " * (indent + 4)
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            key = f"{_flat(k)}: "
            items.append(key + py_literal(v, indent + 4, len(key), 1))
        open_, close = "{", "}"
    else:
        items = [py_literal(v, indent + 4, 0, 1) for v in value]
        open_, close = "[", "]"
    body = "".join(f"\n{inner}{item}," for item in items)
    return f"{open_}{body}\n{' ' * indent}{close}"


def _yaml_list(items: list) -> str:
    """A YAML block sequence indented under its key, or `` []`` when empty."""
    if not items:
        return " []"
    text = yaml.safe_dump(list(items), default_flow_style=False, sort_keys=False)
    return "\n" + "\n".join(f"  {line}" for line in text.rstrip().splitlines())


_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=select_autoescape(disabled_extensions=("j2", "py", "yaml")),
)
_env.filters["pyrepr"] = py_literal
_env.filters["pystr"] = _pystr
_env.filters["yaml_list"] = _yaml_list


def render(template_name: str, **context) -> str:
    return _env.get_template(template_name).render(**context)


def render_string(source: str, **context) -> str:
    """Render template text that is not a file (a starter's prompt seed)."""
    return _env.from_string(source).render(**context)


def _import_group(module: str) -> int:
    """0 = standard library, 1 = third party, 2 = KohakuTerrarium."""
    top = module.split(".")[0]
    if top == "kohakuterrarium":
        return 2
    return 0 if top in sys.stdlib_module_names else 1


def _alias(a: ast.alias) -> str:
    return f"{a.name} as {a.asname}" if a.asname else a.name


def _from_line(module: str, names: list[str]) -> str:
    line = f"from {module} import {', '.join(names)}"
    if len(line) <= _WIDTH:
        return line
    body = "".join(f"    {n},\n" for n in names)
    return f"from {module} import (\n{body})"


def import_block(base: list[str], extra: list[str] | None = None) -> str:
    """Group, merge and order import statements as the project does.

    Groups are standard library, third party, KohakuTerrarium; inside a group
    ``import`` lines come before ``from`` lines, then shorter dotted paths.
    ``from`` imports of one module merge into one statement.
    """
    plain: dict[str, None] = {}
    froms: dict[str, list[str]] = {}
    for line in [*base, *(extra or [])]:
        for node in ast.parse(line.strip()).body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    plain[_alias(a)] = None
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                names = froms.setdefault(module, [])
                names.extend(n for n in map(_alias, node.names) if n not in names)
    groups: list[list[tuple]] = [[], [], []]
    for name in plain:
        key = (0, name.count("."), name)
        groups[_import_group(name)].append((key, f"import {name}"))
    for module, names in froms.items():
        key = (1, module.count("."), module)
        groups[_import_group(module)].append((key, _from_line(module, sorted(names))))
    return "\n\n".join("\n".join(line for _, line in sorted(g)) for g in groups if g)


def render_creature_config(
    *,
    name: str,
    base: str | None = None,
    description: str = "",
    model: str = "",
    tools: list | None = None,
    subagents: list | None = None,
) -> str:
    return render(
        "creature_config.yaml.j2",
        name=name,
        base_config=base,
        description=description,
        model=model,
        tools=tools or [],
        subagents=subagents or [],
    )


def render_system_prompt(name: str, purpose: str = "") -> str:
    return render("system_prompt.md.j2", name=name, purpose=purpose)
