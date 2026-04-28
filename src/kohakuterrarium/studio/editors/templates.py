"""Jinja template rendering for scaffolding.

Kept tiny — Phase 1 only needs the creature-config + system-prompt
templates. Per-kind module templates (``tool.py.j2`` etc.) are
driven from ``codegen_<kind>.py`` in Phase 3.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Templates are kept inside the legacy api/studio/templates/ directory
# until a future phase relocates them. studio/editors/templates.py is the
# read-side here and only points to that directory.
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
