"""
Input module protocol and base class.

Input modules receive external input and produce TriggerEvents.
Integrates with the user command system for slash commands.

Slash-command dispatch order:

1. Exact match in the registered ``_user_commands`` dict — /model,
   /plugin, /skill, ...
2. Wildcard fallback to the :class:`SkillRegistry` (Qd triple-
   invocation): ``/<skill-name> [args]`` injects a user-turn
   preamble that asks the model to follow the named skill. This
   only fires when the slash name does not shadow a real user
   command, so existing commands keep working.
"""

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
    """
    Protocol for input modules.

    Input modules receive external input (CLI, API, ASR, etc.)
    and convert it to TriggerEvents for the controller.
    """

    async def start(self) -> None:
        """Start the input module."""
        ...

    async def stop(self) -> None:
        """Stop the input module."""
        ...

    async def get_input(self) -> TriggerEvent | None:
        """
        Wait for and return the next input event.

        Returns:
            TriggerEvent with type="user_input", or None if no input
        """
        ...


class BaseInputModule(ABC):
    """
    Base class for input modules.

    Provides common functionality for input handling and
    user command dispatch (slash commands).

    Subclasses must override ``render_command_data()`` to handle
    interactive data payloads (select, confirm, etc.) natively
    in their UI framework.
    """

    def __init__(self):
        self._running = False
        # User command system (set by agent after construction)
        self._user_commands: dict[str, Any] = {}  # name → UserCommand
        self._user_command_context: Any = None
        self._command_alias_map: dict[str, str] = {}  # alias → canonical

    def set_user_commands(self, commands: dict[str, Any], context: Any) -> None:
        """Register user commands and context for slash command dispatch.

        Called by Agent during initialization.
        """
        self._user_commands = commands
        self._user_command_context = context
        # Build alias map
        self._command_alias_map = {}
        for name, cmd in commands.items():
            for alias in getattr(cmd, "aliases", []):
                self._command_alias_map[alias] = name

    async def try_user_command(self, text: str) -> UserCommandResult | None:
        """Execute a slash command. Returns result or None if not a command.

        After executing the command, calls ``render_command_data()`` if
        the result has a ``data`` payload. The subclass renders interactive
        UI (select, confirm, etc.) and may return a follow-up result.

        When the slash name does not match any registered user command,
        falls through to the skill registry so ``/<skill-name>`` acts
        as the user-invoke path of the triple-invocation spec (Qd).
        """
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

                # Let the subclass handle interactive data payloads
                if result.data and not result.error:
                    followup = await self.render_command_data(result, canonical)
                    if followup is not None:
                        return followup

                return result

        # Slash-to-skill fallback (Qd). Returns ``None`` if the name
        # doesn't match a registered skill either, so the caller can
        # fall back to its legacy "unknown command" path (e.g. sending
        # the text to the LLM as-is or raising in tests).
        return self._dispatch_skill_slash(name, args)

    def _dispatch_skill_slash(self, name: str, args: str) -> UserCommandResult | None:
        """Handle ``/<skill-name> [args]`` — returns an injection turn.

        The returned :class:`UserCommandResult` is non-consuming so the
        injected text flows through to the LLM as a user turn; callers
        that want strict consumption behaviour can re-wrap the output.
        """
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

        # Build the preamble that becomes a user-turn message. The
        # framework rewrites the user's original ``/<skill-name> args``
        # line into this preamble so the model sees a clean request.
        injected = build_user_skill_turn(skill, args)
        return UserCommandResult(
            output=injected,
            consumed=False,
        )

    async def render_command_data(
        self, result: UserCommandResult, command_name: str
    ) -> UserCommandResult | None:
        """Render a command's interactive data payload.

        Subclasses override this to handle ``result.data`` natively:
        - CLI: print numbered list, prompt with input()
        - TUI: show selection widget in Textual
        - Web: return data as-is (frontend renders modal)

        If the user makes a selection, execute the follow-up command
        and return the new result. Return None to use the original result.
        """
        return None

    async def _execute_followup(
        self, action: str, args: str
    ) -> UserCommandResult | None:
        """Helper: execute a follow-up command by name (for render_command_data)."""
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
        """Called when module starts. Override in subclass."""
        pass

    async def _on_stop(self) -> None:
        """Called when module stops. Override in subclass."""
        pass

    @abstractmethod
    async def get_input(self) -> TriggerEvent | None:
        """Get next input event. Must be implemented by subclass."""
        ...
