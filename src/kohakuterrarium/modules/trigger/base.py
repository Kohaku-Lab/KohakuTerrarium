"""Define autonomous trigger protocols and shared lifecycle behavior."""

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Protocol, runtime_checkable

from kohakuterrarium.core.events import TriggerEvent


@runtime_checkable
class TriggerModule(Protocol):
    """Define lifecycle and event delivery for autonomous trigger modules."""

    async def start(self) -> None:
        """Start the trigger."""
        ...

    async def stop(self) -> None:
        """Stop the trigger."""
        ...

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Wait for the next event, returning none after the trigger stops."""
        ...

    def set_context(self, context: dict[str, Any]) -> None:
        """Update state consumed by context-sensitive triggers."""
        ...


class BaseTrigger(ABC):
    """Provide trigger lifecycle, context, setup metadata, and event creation."""

    resumable: bool = False
    # Universal triggers may be installed at runtime through a generated tool.
    universal: ClassVar[bool] = False

    setup_tool_name: ClassVar[str] = ""
    # The adapter marks setup descriptions as long-lived trigger side effects.
    setup_description: ClassVar[str] = ""
    # None denotes a setup tool with no arguments.
    setup_param_schema: ClassVar[dict[str, Any] | None] = None
    # Empty setup documentation falls back to the one-line description.
    setup_full_doc: ClassVar[str] = ""
    # Manual-read gating protects triggers whose constraints exceed their schema.
    setup_require_manual_read: ClassVar[bool] = False

    def __init__(
        self,
        prompt: str | None = None,
        **options: Any,
    ):
        """Initialize the default prompt, options, context, and stopped state."""
        self.prompt = prompt
        self.options = options
        self._running = False
        self._context: dict[str, Any] = {}

    def to_resume_dict(self) -> dict[str, Any]:
        """Serialize constructor data required to resume this trigger."""
        return {"prompt": self.prompt, **self.options}

    @classmethod
    def from_resume_dict(cls, data: dict[str, Any]) -> "BaseTrigger":
        """Recreate a trigger from persisted constructor data."""
        return cls(**data)

    @classmethod
    def from_setup_args(cls, args: dict[str, Any]) -> "BaseTrigger":
        """Build from setup arguments, defaulting to the resume-data contract."""
        return cls.from_resume_dict(args)

    @classmethod
    def post_setup(cls, trigger: "BaseTrigger", context: Any) -> None:
        """Wire context-derived state before a generated trigger is registered."""
        return None

    @property
    def is_running(self) -> bool:
        """Check if trigger is running."""
        return self._running

    async def start(self) -> None:
        """Start the trigger."""
        self._running = True
        await self._on_start()

    async def stop(self) -> None:
        """Stop the trigger."""
        self._running = False
        await self._on_stop()

    async def _on_start(self) -> None:
        """Handle subclass-specific startup."""

    async def _on_stop(self) -> None:
        """Handle subclass-specific shutdown."""

    def set_context(self, context: dict[str, Any]) -> None:
        """Update trigger context."""
        self._context.update(context)
        self._on_context_update(context)

    def _on_context_update(self, context: dict[str, Any]) -> None:
        """React to newly merged trigger context."""

    @abstractmethod
    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Wait for the next trigger event."""
        ...

    def _create_event(
        self,
        event_type: str,
        content: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> TriggerEvent:
        """Create a trigger event with default values."""
        return TriggerEvent(
            type=event_type,
            content=content or self.prompt or "",
            context=context or self._context.copy(),
            prompt_override=self.prompt,
        )
