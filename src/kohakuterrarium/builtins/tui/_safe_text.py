"""Safe primitives for inserting runtime text into Textual markup slots."""

from rich.markup import escape
from textual.content import Content


def plain(text: str) -> Content:
    """Return literal Textual content without interpreting markup tags."""
    return Content(text)


def escape_markup(text: str) -> str:
    """Escape a dynamic fragment for a Textual string markup slot."""
    return escape(text)
