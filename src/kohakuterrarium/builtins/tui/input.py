"""TUI input module - reads input from Textual app."""

import asyncio
import shlex
from typing import Any

from kohakuterrarium.builtins.tui.session import TUISession
from kohakuterrarium.builtins.tui.widgets import ChatInput
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.core.session import get_session
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.modules.user_command.base import (
    UserCommandResult,
    parse_slash_command,
)
from kohakuterrarium.utils.logging import get_logger, restore_logging, suppress_logging

logger = get_logger(__name__)


class TUIInput(BaseInputModule):
    """Read user input from a shared full-screen Textual session."""

    # Textual exclusively owns terminal input/output while this module is active.
    _supports_raw_io = False

    def __init__(
        self,
        session_key: str | None = None,
        prompt: str = "You: ",
        **options: Any,
    ):
        super().__init__()
        self._session_key = session_key
        self._prompt = prompt
        self._tui: TUISession | None = None
        self._app_task: asyncio.Task | None = None
        self._exit_requested = False

    def set_user_commands(self, commands: dict, context: Any) -> None:
        """Register commands and refresh live completion hints."""
        super().set_user_commands(commands, context)
        all_names: list[str] = list(commands.keys())
        for cmd in commands.values():
            all_names.extend(getattr(cmd, "aliases", []))
        self._command_hint_names = sorted(set(all_names))
        # Native modal screens require direct access to the live agent.
        host_agent = getattr(context, "agent", None) if context else None
        if self._tui is not None and host_agent is not None:
            self._tui.host_agent = host_agent
        # Runtime plugin changes must update the mounted input immediately.
        self._apply_command_hints()

    def _apply_command_hints(self) -> None:
        """Apply current command names to the mounted chat input."""
        tui = self._tui
        app = getattr(tui, "_app", None) if tui is not None else None
        hint_names = getattr(self, "_command_hint_names", [])
        if app is None:
            return
        try:
            inp = app.query_one("#input-box", ChatInput)
            inp.command_names = hint_names
        except Exception as e:
            logger.debug("Deferred command-hint refresh skipped", error=str(e))

    async def try_user_command(self, text: str) -> UserCommandResult | None:
        """Route terminal-conflicting or picker-oriented commands to TUI modals."""
        if text.startswith("/") and self._user_commands:
            name, args = parse_slash_command(text)
            canonical = self._command_alias_map.get(name, name)
            if canonical == "module":
                handled = await self._intercept_module(args)
                if handled is not None:
                    return handled
            elif canonical == "model":
                handled = await self._intercept_model(args)
                if handled is not None:
                    return handled
        return await super().try_user_command(text)

    async def _intercept_module(self, args: str) -> UserCommandResult | None:
        """Open the module list or editor for supported subcommands."""
        if self._tui is None or self._user_command_context is None:
            return None
        agent = getattr(self._user_command_context, "agent", None)
        if agent is None:
            return None
        try:
            tokens = shlex.split(args or "")
        except ValueError:
            return None
        sub = tokens[0].lower() if tokens else "list"
        if sub in ("", "list"):
            await self._tui.show_modules_modal(agent)
            return UserCommandResult(output="", consumed=True)
        if sub == "edit" and len(tokens) > 1:
            opened = await self._tui.show_module_edit_modal(agent, tokens[1])
            if opened:
                return UserCommandResult(output="", consumed=True)
            # Text dispatch retains detailed unresolved-name errors.
        return None

    async def _intercept_model(self, args: str) -> UserCommandResult | None:
        """Open the model picker when no explicit model is supplied."""
        if (args or "").strip():
            return None
        if self._tui is None or self._user_command_context is None:
            return None
        agent = getattr(self._user_command_context, "agent", None)
        if agent is None:
            return None
        await self._tui.show_model_picker_modal(agent)
        return UserCommandResult(output="", consumed=True)

    async def render_command_data(
        self, result: UserCommandResult, command_name: str
    ) -> UserCommandResult | None:
        """Render selection and confirmation command data as native modals."""
        data = result.data
        data_type = data.get("type", "")

        if not self._tui:
            return None

        if data_type == "select":
            options = data.get("options", [])
            if not options:
                return None
            selected = await self._tui.show_selection_modal(
                title=data.get("title", "Select"),
                options=options,
                current=data.get("current", ""),
            )
            if selected:
                action = data.get("action", "")
                if action:
                    return await self._execute_followup(action, selected)
            return UserCommandResult(output="", consumed=True)

        if data_type == "confirm":
            confirmed = await self._tui.show_confirm_modal(
                data.get("message", "Confirm?")
            )
            if confirmed:
                action = data.get("action", "")
                args = data.get("action_args", "")
                if action:
                    return await self._execute_followup(action, args)
            return UserCommandResult(output="", consumed=True)

        return None

    @property
    def exit_requested(self) -> bool:
        """Return whether the user requested exit."""
        return self._exit_requested

    async def _on_start(self) -> None:
        """Initialize the session and launch its Textual application."""
        session = get_session(self._session_key)
        if session.tui is None:
            session.tui = TUISession(
                agent_name=session.key if session.key != "__default__" else "agent",
            )
        self._tui = session.tui

        terrarium_tabs = session.extra.get("terrarium_tui_tabs")
        if terrarium_tabs:
            self._tui.set_terrarium_tabs(terrarium_tabs)

        # The TUI owns the terminal while session output retains framework logs.
        suppress_logging()

        await self._tui.start(self._prompt)
        self._app_task = asyncio.create_task(self._tui.run_app())
        await self._tui.wait_ready()

        self._apply_command_hints()

        logger.debug("TUI input started", session_key=self._session_key)

    async def _on_stop(self) -> None:
        """Stop the Textual application and restore stderr logging."""
        if self._tui:
            self._tui.stop()
        if self._app_task and not self._app_task.done():
            self._app_task.cancel()
            try:
                await self._app_task
            except (asyncio.CancelledError, Exception):
                pass

        restore_logging()
        logger.debug("TUI input stopped")

    async def get_input(self) -> TriggerEvent | None:
        """Wait for TUI input and convert it to a trigger event."""
        if not self._running or not self._tui:
            return None

        try:
            text = await self._tui.get_input(self._prompt)

            # Empty input is the app/session shutdown sentinel.
            if not text:
                self._exit_requested = True
                return None

            # Plain exit commands remain available when user commands are absent.
            if not self._user_commands and text.lower() in (
                "exit",
                "quit",
                "/exit",
                "/quit",
            ):
                self._exit_requested = True
                return None

            if text.startswith("/"):
                cmd_name = (
                    text.lstrip("/").split()[0].lower() if text.strip("/") else ""
                )
                result = await self.try_user_command(text)
                if result is not None:
                    if result.error:
                        self._tui.add_system_notice(
                            result.error, command=cmd_name, error=True
                        )
                    elif result.output and result.consumed:
                        self._tui.add_system_notice(result.output, command=cmd_name)
                    if self._exit_requested:
                        return None
                    if result.consumed:
                        return await self.get_input()
                    if result.output:
                        text = result.output

            return create_user_input_event(text, source="tui")

        except (EOFError, asyncio.CancelledError):
            self._exit_requested = True
            return None
        except Exception as e:
            logger.error("Error reading TUI input", error=str(e))
            return None
