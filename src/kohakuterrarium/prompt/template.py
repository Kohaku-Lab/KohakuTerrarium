"""Render Jinja prompts and resolve reusable fragments from packages or files."""

from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, TemplateNotFound, TemplateSyntaxError

from kohakuterrarium.packages.slots import resolve_package_prompt
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class PackagePromptLoader(BaseLoader):
    """Resolve includes lazily from package manifests, then raw file paths."""

    def get_source(self, environment, template):  # noqa: D401 - Jinja API
        path: Path | None = None
        try:
            path = resolve_package_prompt(template)
        except ValueError as exc:
            # Report collisions through Jinja's expected loader exception.
            logger.error("Prompt fragment collision", fragment=template, error=str(exc))
            raise TemplateNotFound(template, message=str(exc)) from exc

        if path is None:
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


_env = Environment(
    loader=PackagePromptLoader(),
    autoescape=False,  # Prompt text must remain literal.
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(template: str, **variables: Any) -> str:
    """Render a Jinja prompt with variables and package-aware includes."""
    try:
        jinja_template = _env.from_string(template)
        result = jinja_template.render(**variables)
        return result
    except TemplateSyntaxError as e:
        # ``message`` is reserved by LogRecord and cannot be an extra field.
        logger.error("Template syntax error", line=e.lineno, error=str(e))
        raise


def render_template_safe(template: str, **variables: Any) -> str:
    """Render a prompt, returning the source unchanged on any failure."""
    try:
        return render_template(template, **variables)
    except Exception as e:
        logger.warning("Template rendering failed, using original", error=str(e))
        return template


class PromptTemplate:
    """Compile a reusable Jinja prompt once for repeated rendering."""

    def __init__(self, template: str):
        """Compile the provided Jinja source."""
        self._source = template
        self._template = _env.from_string(template)

    def render(self, **variables: Any) -> str:
        """Render the compiled template with supplied variables."""
        return self._template.render(**variables)

    @property
    def source(self) -> str:
        """Return the original uncompiled template source."""
        return self._source
