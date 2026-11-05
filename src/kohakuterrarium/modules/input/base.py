"""Convert external input to events and dispatch commands before skill fallbacks."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.user_command.base import (
    UserCommandResult,
    parse_slash_command,
)
from kohakuterrarium.skills.user_slash import build_user_skill_turn

_deferred_command = ContextVar("deferred_input_command", default=False)


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

    supports_busy_commands = False
    # On resume the agent replays the saved history to its output when its input loop starts.
    # Inputs that stand in for a front end attached later (the engine's NoneInput, ktui's
    # placeholder) set this False: the history is left in ``_pending_resume_events`` for that
    # front end, instead of going to whatever output happens to be attached first.
    replay_resume_history = True

    def __init__(self):
        self.command_idle_waiter: Callable[[], Awaitable[None]] | None = None
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

    def defer_command(self, text: str) -> bool:
        """Identify commands that must retain foreground input ordering."""
        name, _ = parse_slash_command(text)
        canonical = self._command_alias_map.get(name, name)
        command = self._user_commands.get(canonical)
        return self.command_idle_waiter is not None and not getattr(
            command, "safe_while_busy", False
        )

    async def run_deferred_command(self, text: str) -> UserCommandResult | None:
        """Execute a command already admitted by the serial input worker."""
        token = _deferred_command.set(True)
        try:
            result = await self.try_user_command(text)
            if result is not None and result.consumed:
                await self.present_command_result(result, parse_slash_command(text)[0])
            return result
        finally:
            _deferred_command.reset(token)

    async def present_command_result(
        self, result: UserCommandResult, name: str
    ) -> None:
        """Present a consumed command result on the input surface."""

    async def wait_for_command_slot(self, text: str) -> None:
        """Wait for foreground work unless this registered command is busy-safe."""
        if self.command_idle_waiter is None or _deferred_command.get():
            return
        name, _ = parse_slash_command(text)
        canonical = self._command_alias_map.get(name, name)
        command = self._user_commands.get(canonical)
        if not getattr(command, "safe_while_busy", False):
            await self.command_idle_waiter()

    async def try_user_command(self, text: str) -> UserCommandResult | None:
        """Execute a registered slash command or fall back to an enabled skill."""
        if not text.startswith("/"):
            return None

        await self.wait_for_command_slot(text)
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
