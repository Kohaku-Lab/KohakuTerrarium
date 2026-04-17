"""
Trigger module protocol and base class.

Triggers produce TriggerEvents without user input - enabling autonomous agents.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Protocol, runtime_checkable

from kohakuterrarium.core.events import TriggerEvent


@runtime_checkable
class TriggerModule(Protocol):
    """
    Protocol for trigger modules.

    Triggers produce TriggerEvents based on various conditions:
    - Timer: Fire at intervals
    - Condition: Fire when state matches
    - Context: Fire when context changes
    - Idle: Fire after inactivity period
    """

    async def start(self) -> None:
        """Start the trigger."""
        ...

    async def stop(self) -> None:
        """Stop the trigger."""
        ...

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """
        Wait for and return the next trigger event.

        Returns:
            TriggerEvent when trigger fires, or None if stopped
        """
        ...

    def set_context(self, context: dict[str, Any]) -> None:
        """
        Update trigger context.

        Used by context-based triggers to receive state updates.

        Args:
            context: Current context dict
        """
        ...


class BaseTrigger(ABC):
    """
    Base class for trigger modules.

    Provides common functionality for trigger handling.
    """

    # Override in subclass to enable resume persistence
    resumable: bool = False
    # Override in subclass to allow the agent to install this trigger at
    # runtime via a tool call. Setup-able triggers get wrapped as tools by
    # ``CallableTriggerTool`` (see ``modules/trigger/callable.py``).
    universal: ClassVar[bool] = False

    # --- Setup-able trigger metadata (only honoured when universal=True) ---
    # Tool name the agent calls to install this trigger (e.g. "add_timer").
    setup_tool_name: ClassVar[str] = ""
    # One-line summary shown in the tool list. The adapter prepends
    # "**Trigger** — " so the LLM knows this call installs a long-lived
    # side-effect rather than returning an immediate result.
    setup_description: ClassVar[str] = ""
    # JSON-schema-like dict describing the args the agent should pass.
    # None means the tool accepts no args.
    setup_param_schema: ClassVar[dict[str, Any] | None] = None
    # Long-form documentation surfaced by the ``info`` framework command.
    # Empty string falls back to setup_description.
    setup_full_doc: ClassVar[str] = ""
    # If True, the adapter requires the agent to call ``info <name>`` before
    # using the tool — for triggers whose correct use depends on subtle
    # details beyond the schema.
    setup_require_manual_read: ClassVar[bool] = False

    def __init__(
        self,
        prompt: str | None = None,
        **options: Any,
    ):
        """
        Initialize trigger.

        Args:
            prompt: Default prompt to include in trigger events
            **options: Additional trigger options
        """
        self.prompt = prompt
        self.options = options
        self._running = False
        self._context: dict[str, Any] = {}

    def to_resume_dict(self) -> dict[str, Any]:
        """Serialize trigger config for session persistence.

        Override in subclass to save constructor args needed for re-creation.
        Only called if resumable=True.
        """
        return {"prompt": self.prompt, **self.options}

    @classmethod
    def from_resume_dict(cls, data: dict[str, Any]) -> "BaseTrigger":
        """Re-create trigger from saved config.

        Override in subclass if constructor signature differs from data keys.
        """
        return cls(**data)

    @classmethod
    def from_setup_args(cls, args: dict[str, Any]) -> "BaseTrigger":
        """Build an instance from the agent-supplied setup tool args.

        Default: forwards to ``from_resume_dict``. Override when the setup
        schema differs from the resume dict shape (e.g. when you accept
        user-facing aliases or need validation beyond dataclass wiring).
        """
        return cls.from_resume_dict(args)

    @classmethod
    def post_setup(cls, trigger: "BaseTrigger", context: Any) -> None:
        """Hook called after an agent-installed trigger is constructed.

        Default: no-op. Triggers that need context-derived state (e.g. a
        channel registry or a creature-name-based ``ignore_sender``) override
        this to wire those fields from the executor ``context`` before the
        trigger is registered with the trigger manager.
        """
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
        """Called when trigger starts. Override in subclass."""
        pass

    async def _on_stop(self) -> None:
        """Called when trigger stops. Override in subclass."""
        pass

    def set_context(self, context: dict[str, Any]) -> None:
        """Update trigger context."""
        self._context.update(context)
        self._on_context_update(context)

    def _on_context_update(self, context: dict[str, Any]) -> None:
        """Called when context is updated. Override in subclass."""
        pass

    @abstractmethod
    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Wait for trigger event. Must be implemented by subclass."""
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
