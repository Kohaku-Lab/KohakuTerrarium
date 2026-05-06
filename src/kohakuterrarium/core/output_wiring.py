"""Output wiring — framework hook that fires at creature turn-end.

When a creature's controller returns to idle at the end of a turn (one
trigger event → one or more LLM rounds + tools → controller exits its
loop), the framework emits a ``TriggerEvent(type="creature_output", ...)``
and delivers it directly into one or more target creatures' event queues.

This is strictly event-level. No channels, no tools, no triggers — the
event goes straight through the same ``agent._process_event`` path that
any other ``TriggerEvent`` already uses.

Components in this module (leaf; only imports stdlib + events):

- ``OutputWiringEntry`` — one wiring directive, declared in creature config.
- ``parse_wiring_entry`` — YAML-shape → dataclass.
- ``render_prompt`` — render the receiver-side prompt template
  (``simple`` string-format or ``jinja`` via ``prompt.template``).
- ``OutputWiringResolver`` — protocol the runtime implements.
- ``NoopOutputWiringResolver`` — default used by standalone agents;
  logs once per source and drops emissions.

The terrarium-specific resolver lives in ``terrarium/output_wiring.py``.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.prompt.template import render_template_safe
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Magic-string target: the root agent (which sits *outside* the terrarium).
#: Matches existing root conventions (``report_to_root`` auto-channel, root
#: awareness prompt, etc.).
ROOT_TARGET = "root"

PROMPT_FORMAT_SIMPLE = "simple"
PROMPT_FORMAT_JINJA = "jinja"

#: Default prompt template when ``with_content=True`` and no explicit
#: ``prompt`` is set on the entry. The ``[output-wire from X] ...`` tag
#: distinguishes wire deliveries from channel messages and group_send
#: directs in the receiver's turn context.
DEFAULT_PROMPT_WITH_CONTENT = "[output-wire from {source}] {content}"

#: Default prompt template when ``with_content=False`` (metadata-only ping).
DEFAULT_PROMPT_WITHOUT_CONTENT = (
    "[output-wire from {source}] (turn-end signal, no content)"
)


# ---------------------------------------------------------------------------
# Entry dataclass + parser
# ---------------------------------------------------------------------------


@dataclass
class OutputWiringEntry:
    """One output-wiring directive on a creature.

    Attributes:
        to: Target creature name, or the magic string ``"root"``.
        with_content: If True, the receiver event carries the source's
            last-round assistant text. If False, content is stripped to
            an empty string and only metadata reaches the receiver.
        prompt: Optional prompt template. When None, a default template
            is used (see ``DEFAULT_PROMPT_*``). Available variables:
            ``source``, ``target``, ``content``, ``turn_index``,
            ``source_event_type``, ``with_content``.
        prompt_format: ``"simple"`` (default, ``str.format_map``) or
            ``"jinja"`` (uses ``prompt.template.render_template_safe``).
        allow_self_trigger: Explicit opt-in for source → same-target
            delivery. Defaults to False so accidental self-loops are
            dropped by terrarium resolvers.
    """

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
    """Parse a single YAML/dict/string entry into an ``OutputWiringEntry``.

    Shorthand: a bare string is sugar for ``{to: <str>, with_content: true}``.

    Args:
        raw: A string (shorthand) or a mapping (full form).

    Returns:
        Parsed entry.

    Raises:
        ValueError: If the shape is invalid.
    """
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


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_prompt(
    entry: OutputWiringEntry,
    *,
    source: str,
    target: str,
    content: str,
    turn_index: int,
    source_event_type: str,
) -> str:
    """Render the receiver-side prompt for a single wiring entry.

    Chooses a template: explicit ``entry.prompt`` wins; otherwise the
    default depends on ``entry.with_content``. Then renders with the
    entry's ``prompt_format``.

    Never raises. On template errors, falls back to the default template
    for the current ``with_content`` setting and logs a warning.
    """
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

    # Simple mode: str.format_map with a defaulting mapping so missing
    # keys render as empty string instead of raising.
    try:
        return template.format_map(_SafeFormatDict(variables))
    except Exception as exc:  # pragma: no cover - defensive
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


# ---------------------------------------------------------------------------
# Resolver protocol + default no-op
# ---------------------------------------------------------------------------


@runtime_checkable
class OutputWiringResolver(Protocol):
    """Dispatches ``creature_output`` events to targets named in entries.

    The framework calls ``emit`` once at each creature's turn boundary
    (from ``AgentHandlersMixin._finalize_processing``). Implementations
    must never raise back into the caller — log and skip on any failure.
    """

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
    """Default resolver used when no real one is attached.

    A standalone creature can declare ``output_wiring`` in its config
    (creatures are portable — the same config runs inside a terrarium
    or standalone). When no terrarium is present, there are no targets
    to resolve, so we log the first drop per source and stay silent
    after that.
    """

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


# ---------------------------------------------------------------------------
# Small helpers for tests / callers
# ---------------------------------------------------------------------------


def wiring_targets(entries: list[OutputWiringEntry]) -> list[str]:
    """Return just the target names, preserving order.

    Handy for building status displays / debug dumps.
    """
    return [e.to for e in entries]


@dataclass
class _EmissionContext:
    """Context passed to resolvers. Used by tests; not part of the
    public surface for callers.
    """

    source: str
    content: str
    source_event_type: str
    turn_index: int
    entries: list[OutputWiringEntry] = field(default_factory=list)
