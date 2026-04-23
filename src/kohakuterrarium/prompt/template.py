"""
Prompt templating using Jinja2.

Provides simple variable substitution and control flow for prompts,
plus shared prompt-fragment discovery through the package manifest
``prompts:`` / ``templates:`` slots (Cluster 1 / A.5). A creature
system prompt containing ``{% include "git-safety" %}`` triggers a
search across every installed package for a fragment with that name,
letting packages ship reusable prompt bundles.
"""

from pathlib import Path
from typing import Any

from jinja2 import (
    BaseLoader,
    Environment,
    TemplateNotFound,
    TemplateSyntaxError,
)

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class PackagePromptLoader(BaseLoader):
    """Resolve ``{% include "<name>" %}`` via the package manifest.

    The loader queries :func:`resolve_package_prompt` for every include
    target, falling back to absolute / relative file paths. The
    manifest lookup is lazy so every render picks up newly-installed
    packages without reloading the module.
    """

    def get_source(self, environment, template):  # noqa: D401 — Jinja API
        # Local import — ``packages`` imports ``packages_manifest`` which
        # imports ``packages`` in turn, so we defer to avoid a cold-boot
        # circular import through this module's top-level.
        from kohakuterrarium.packages import resolve_package_prompt

        path: Path | None = None
        try:
            path = resolve_package_prompt(template)
        except ValueError as exc:
            # Collision across packages — surface as TemplateNotFound
            # with a clearer message so the Jinja traceback points at
            # the manifest rather than the include line.
            logger.error("Prompt fragment collision", fragment=template, error=str(exc))
            raise TemplateNotFound(template, message=str(exc)) from exc

        if path is None:
            # Try raw file path as a second resort — keeps parity with
            # ``Environment(loader=FileSystemLoader(...))`` flows.
            candidate = Path(template)
            if candidate.exists() and candidate.is_file():
                path = candidate.resolve()

        if path is None or not path.exists():
            raise TemplateNotFound(template)

        source = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime

        def uptodate() -> bool:
            try:
                return path.stat().st_mtime == mtime
            except OSError:
                return False

        return source, str(path), uptodate


# Create Jinja2 environment with safe defaults
_env = Environment(
    loader=PackagePromptLoader(),
    autoescape=False,  # Prompts are not HTML
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(template: str, **variables: Any) -> str:
    """
    Render a prompt template with variables.

    Supports Jinja2 syntax:
    - Variables: {{ variable }}
    - Conditionals: {% if condition %}...{% endif %}
    - Loops: {% for item in items %}...{% endfor %}
    - Includes: {% include "git-safety" %} resolves via the package
      manifest ``prompts:`` slot, then falls back to a raw file path.

    Args:
        template: Template string with Jinja2 syntax
        **variables: Variables to substitute

    Returns:
        Rendered template string

    Raises:
        TemplateSyntaxError: If template syntax is invalid
    """
    try:
        jinja_template = _env.from_string(template)
        result = jinja_template.render(**variables)
        return result
    except TemplateSyntaxError as e:
        logger.error("Template syntax error", line=e.lineno, message=str(e))
        raise


def render_template_safe(template: str, **variables: Any) -> str:
    """
    Render template, returning original on error.

    Args:
        template: Template string
        **variables: Variables to substitute

    Returns:
        Rendered template or original on error
    """
    try:
        return render_template(template, **variables)
    except Exception as e:
        logger.warning("Template rendering failed, using original", error=str(e))
        return template


class PromptTemplate:
    """
    Reusable prompt template.

    Compiles template once for efficient repeated rendering.
    """

    def __init__(self, template: str):
        """
        Create a prompt template.

        Args:
            template: Jinja2 template string
        """
        self._source = template
        self._template = _env.from_string(template)

    def render(self, **variables: Any) -> str:
        """
        Render the template with variables.

        Args:
            **variables: Variables to substitute

        Returns:
            Rendered string
        """
        return self._template.render(**variables)

    @property
    def source(self) -> str:
        """Get original template source."""
        return self._source
