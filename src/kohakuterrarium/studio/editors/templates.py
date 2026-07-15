"""Jinja template rendering for scaffolding.

Provides the shared Jinja environment used by creature scaffolding and per-kind
code-generation modules.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Template data lives beside this module so all editor code uses one loader root.
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates_data"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    keep_trailing_newline=True,
    autoescape=select_autoescape(disabled_extensions=("j2", "py", "yaml")),
)


def render(template_name: str, **context) -> str:
    return _env.get_template(template_name).render(**context)


def render_creature_config(
    *, name: str, base: str | None = None, description: str = ""
) -> str:
    return render(
        "creature_config.yaml.j2",
        name=name,
        base_config=base,
        description=description,
    )


def render_system_prompt(name: str) -> str:
    return render("system_prompt.md.j2", name=name)
