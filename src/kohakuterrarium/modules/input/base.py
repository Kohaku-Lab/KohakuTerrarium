"""Convert external input to events and dispatch commands before skill fallbacks."""

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.user_command.base import (
    UserCommandResult,
    parse_slash_command,
)
from kohakuterrarium.skills.user_slash import build_user_skill_turn


@runtime_checkable
class InputModule(Protocol):
    """Define lifecycle and event delivery for external input adapters."""

    async def start(self) -> None:
        """Start the input module."""
        ...

    async def stop(self) -> None:
        """Stop the input module."""
        ...

    async def get_input(self) -> TriggerEvent | None:
        """Wait for the next input event or return none when unavailable."""
        ...


class BaseInputModule(ABC):
    """Provide lifecycle and slash-command dispatch for input adapters."""

    def __init__(self):
        self._running = False
        self._user_commands: dict[str, Any] = {}
        self._user_command_context: Any = None
        self._command_alias_map: dict[str, str] = {}

    def set_user_commands(self, commands: dict[str, Any], context: Any) -> None:
        """Register commands, aliases, and execution context for slash dispatch."""
        self._user_commands = commands
        self._user_command_context = context
        self._command_alias_map = {}
        for name, cmd in commands.items():
            for alias in getattr(cmd, "aliases", []):
                self._command_alias_map[alias] = name

    async def try_user_command(self, text: str) -> UserCommandResult | None:
        """Execute a registered slash command or fall back to an enabled skill."""
        if not text.startswith("/"):
            return None

        name, args = parse_slash_command(text)

        if self._user_commands:
            canonical = self._command_alias_map.get(name, name)
            cmd = self._user_commands.get(canonical)
            if cmd is not None:
                ctx = self._user_command_context
                ctx.extra["command_registry"] = self._user_commands
                result = await cmd.execute(args, ctx)

                if result.data and not result.error:
                    followup = await self.render_command_data(result, canonical)
                    if followup is not None:
                        return followup

                return result

        # Unknown slash names remain available to legacy caller handling.
        return self._dispatch_skill_slash(name, args)

    def _dispatch_skill_slash(self, name: str, args: str) -> UserCommandResult | None:
        """Convert an enabled skill slash into a non-consuming injected user turn."""
        ctx = self._user_command_context
        if ctx is None or getattr(ctx, "agent", None) is None:
            return None
        registry = getattr(ctx.agent, "skills", None)
        if registry is None:
            return None
        skill = registry.get(name)
        if skill is None:
            return None
        if not skill.enabled:
            return UserCommandResult(
                error=(
                    f"Skill '{name}' is disabled. Enable with " f"/skill enable {name}."
                )
            )

        # Replace slash syntax with the skill's explicit user-turn preamble.
        injected = build_user_skill_turn(skill, args)
        return UserCommandResult(
            output=injected,
            consumed=False,
        )

    async def render_command_data(
        self, result: UserCommandResult, command_name: str
    ) -> UserCommandResult | None:
        """Render interactive command data and optionally return a follow-up result."""
        return None

    async def _execute_followup(
        self, action: str, args: str
    ) -> UserCommandResult | None:
        """Execute a follow-up command by canonical name or alias."""
        canonical = self._command_alias_map.get(action, action)
        cmd = self._user_commands.get(canonical)
        if cmd:
            ctx = self._user_command_context
            return await cmd.execute(args, ctx)
        return None

    @property
    def is_running(self) -> bool:
        """Check if module is running."""
        return self._running

    async def start(self) -> None:
        """Start the input module."""
        self._running = True
        await self._on_start()

    async def stop(self) -> None:
        """Stop the input module."""
        self._running = False
        await self._on_stop()

    async def _on_start(self) -> None:
        """Handle subclass-specific startup."""
        pass

    async def _on_stop(self) -> None:
        """Handle subclass-specific shutdown."""
        pass

    @abstractmethod
    async def get_input(self) -> TriggerEvent | None:
        """Return the next input event."""
        ...
