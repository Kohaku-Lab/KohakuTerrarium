"""Assistant message block — accumulates streaming text in the live region."""

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.markdown import Markdown
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.theme import COLOR_AI, ICON_AI

_MARKDOWN_HINTS = ("```", "**", "__", "##", "- ", "* ", "1. ", "> ", "[", "`")


def _looks_like_markdown(text: str) -> bool:
    return any(hint in text for hint in _MARKDOWN_HINTS)


class PrefixedRenderable:
    """Prefix the first rendered line and align continuation lines beneath it."""

    def __init__(
        self,
        icon: str,
        icon_style: str,
        body: RenderableType,
        indent_width: int = 2,
    ):
        self.icon = icon
        self.icon_style = icon_style
        self.body = body
        self.indent_width = indent_width

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        sub_options = options.update_width(
            max(1, options.max_width - self.indent_width)
        )
        lines = list(console.render_lines(self.body, sub_options, pad=False))
        icon_seg = Segment(self.icon, Style.parse(self.icon_style))
        indent_seg = Segment(" " * self.indent_width)
        for i, line in enumerate(lines):
            yield icon_seg if i == 0 else indent_seg
            yield from line
            yield Segment.line()


class AssistantMessageBlock:
    """Accumulate streamed assistant text and render Markdown on commit."""

    def __init__(self):
        self._buffer: str = ""
        self._finished: bool = False

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        self._buffer += chunk

    def finish(self) -> None:
        self._finished = True

    @property
    def text(self) -> str:
        return self._buffer

    @property
    def is_empty(self) -> bool:
        return not self._buffer.strip()

    def __rich__(self) -> RenderableType:
        if self.is_empty:
            return Text("")
        header = Text(f"{ICON_AI} ", style=COLOR_AI)
        body = Text(self._buffer)
        return Group(Text.assemble(header, body))

    def to_committed(self) -> RenderableType:
        """Render completed text with Markdown detection and aligned prefixing."""
        if self.is_empty:
            return Text("")
        if _looks_like_markdown(self._buffer):
            body: RenderableType = Markdown(self._buffer, code_theme="monokai")
        else:
            body = Text(self._buffer)
        return PrefixedRenderable(f"{ICON_AI} ", COLOR_AI, body)
