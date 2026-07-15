"""Turn-end event delivery between creatures through configured wiring.

Wiring bypasses graph channels and enters the target through its normal event
path. Standalone agents use a no-op resolver because no target graph exists.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.prompt.template import render_template_safe
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# The root target is resolved outside the terrarium's ordinary creature set.
ROOT_TARGET = "root"

PROMPT_FORMAT_SIMPLE = "simple"
PROMPT_FORMAT_JINJA = "jinja"

# The tag distinguishes wiring deliveries from channel and direct messages.
DEFAULT_PROMPT_WITH_CONTENT = "[output-wire from {source}] {content}"

DEFAULT_PROMPT_WITHOUT_CONTENT = (
    "[output-wire from {source}] (turn-end signal, no content)"
)


@dataclass
class OutputWiringEntry:
    """Configure one turn-end delivery target and its receiver prompt."""

    to: str
    with_content: bool = True
    prompt: str | None = None
    prompt_format: str = PROMPT_FORMAT_SIMPLE
    allow_self_trigger: bool = False

    def __post_init__(self) -> None:
        if not self.to or not isinstance(self.to, str):
            raise ValueError(
                f"OutputWiringEntry.to must be a non-empty string, got {self.to!r}"
            )
        if self.prompt_format not in (PROMPT_FORMAT_SIMPLE, PROMPT_FORMAT_JINJA):
            raise ValueError(
                f"OutputWiringEntry.prompt_format must be "
                f"'{PROMPT_FORMAT_SIMPLE}' or '{PROMPT_FORMAT_JINJA}', "
                f"got {self.prompt_format!r}"
            )


def parse_wiring_entry(raw: Any) -> OutputWiringEntry:
    """Parse a wiring entry, accepting a target string as shorthand."""
    if isinstance(raw, str):
        return OutputWiringEntry(to=raw)
    if not isinstance(raw, dict):
        raise ValueError(
            f"output_wiring entry must be a string or mapping, got {type(raw).__name__}"
        )
    if "to" not in raw:
        raise ValueError("output_wiring entry missing required 'to' field")
    return OutputWiringEntry(
        to=raw["to"],
        with_content=bool(raw.get("with_content", True)),
        prompt=raw.get("prompt"),
        prompt_format=raw.get("prompt_format", PROMPT_FORMAT_SIMPLE),
        allow_self_trigger=bool(raw.get("allow_self_trigger", False)),
    )


def parse_wiring_list(raw: Any) -> list[OutputWiringEntry]:
    """Parse the full ``output_wiring:`` list. Missing / None becomes ``[]``."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"output_wiring must be a list, got {type(raw).__name__}")
    return [parse_wiring_entry(x) for x in raw]


def render_prompt(
    entry: OutputWiringEntry,
    *,
    source: str,
    target: str,
    content: str,
    turn_index: int,
    source_event_type: str,
) -> str:
    """Render a receiver prompt, falling back safely on template errors."""
    template = entry.prompt
    if template is None:
        template = (
            DEFAULT_PROMPT_WITH_CONTENT
            if entry.with_content
            else DEFAULT_PROMPT_WITHOUT_CONTENT
        )

    variables = {
        "source": source,
        "target": target,
        "content": content,
        "turn_index": turn_index,
        "source_event_type": source_event_type,
        "with_content": entry.with_content,
    }

    if entry.prompt_format == PROMPT_FORMAT_JINJA:
        return render_template_safe(template, **variables)

    # Missing simple-format variables render empty to keep wiring delivery alive.
    try:
        return template.format_map(_SafeFormatDict(variables))
    except Exception as exc:  # pragma: no cover - delivery must survive templates
        logger.warning(
            "output_wiring simple-format render failed, using fallback",
            template_error=str(exc),
            source=source,
            target=target,
        )
        fallback = (
            DEFAULT_PROMPT_WITH_CONTENT
            if entry.with_content
            else DEFAULT_PROMPT_WITHOUT_CONTENT
        )
        return fallback.format_map(_SafeFormatDict(variables))


class _SafeFormatDict(dict):
    """Dict subclass that renders missing keys as empty string."""

    def __missing__(self, key: str) -> str:  # noqa: D401 - mapping protocol
        return ""


@runtime_checkable
class OutputWiringResolver(Protocol):
    """Dispatch turn-end events without propagating delivery failures."""

    async def emit(
        self,
        *,
        source: str,
        content: str,
        source_event_type: str,
        turn_index: int,
        entries: list[OutputWiringEntry],
    ) -> None: ...


class NoopOutputWiringResolver:
    """Drop standalone wiring emissions, logging once per source."""

    def __init__(self) -> None:
        self._logged_sources: set[str] = set()

    async def emit(
        self,
        *,
        source: str,
        content: str,
        source_event_type: str,
        turn_index: int,
        entries: list[OutputWiringEntry],
    ) -> None:
        if source in self._logged_sources:
            return
        self._logged_sources.add(source)
        logger.info(
            "output_wiring declared but no resolver attached - dropping emissions",
            source=source,
            entries=[e.to for e in entries],
        )


def wiring_targets(entries: list[OutputWiringEntry]) -> list[str]:
    """Return target names in declaration order."""
    return [e.to for e in entries]


@dataclass
class _EmissionContext:
    """Bundle resolver inputs for internal callers and tests."""

    source: str
    content: str
    source_event_type: str
    turn_index: int
    entries: list[OutputWiringEntry] = field(default_factory=list)
