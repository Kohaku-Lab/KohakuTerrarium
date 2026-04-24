"""
CLI input module.

Provides terminal-based input for agents.
"""

import asyncio
import select
import sys

from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.modules.user_command.base import UserCommandResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class CLIInput(BaseInputModule):
    """
    Command-line interface input module.

    Reads user input from terminal with async support.
    """

    def __init__(
        self,
        prompt: str = "> ",
        *,
        exit_commands: list[str] | None = None,
    ):
        """
        Initialize CLI input.

        Args:
            prompt: Input prompt to display
            exit_commands: Commands that signal exit (default: /exit, /quit, exit, quit)
        """
        super().__init__()
        self.prompt = prompt
        self.exit_commands = exit_commands or ["/exit", "/quit", "exit", "quit"]
        self._exit_requested = False

    @property
    def exit_requested(self) -> bool:
        """Check if exit was requested."""
        return self._exit_requested

    async def _on_start(self) -> None:
        """Initialize CLI input."""
        logger.debug("CLI input started", prompt=self.prompt)

    async def _on_stop(self) -> None:
        """Cleanup CLI input."""
        logger.debug("CLI input stopped")

    async def get_input(self) -> TriggerEvent | None:
        """
        Get input from terminal.

        Returns:
            TriggerEvent with user input, or None if exit requested
        """
        if not self._running or self._exit_requested:
            return None

        try:
            # Run blocking input in thread pool
            loop = asyncio.get_event_loop()
            line = await loop.run_in_executor(None, self._read_line)

            if line is None:
                # EOF (Ctrl+D)
                self._exit_requested = True
                return None

            line = line.strip()

            # Legacy exit check (fallback if command system not wired)
            if not self._user_commands and line.lower() in self.exit_commands:
                self._exit_requested = True
                return None

            # Try slash command
            if line.startswith("/"):
                result = await self.try_user_command(line)
                if result is not None:
                    if result.error:
                        print(f"Error: {result.error}")
                    elif result.output and result.consumed:
                        print(result.output)
                    if self._exit_requested:
                        return None
                    if result.consumed:
                        return await self.get_input()
                    if result.output:
                        line = result.output

            # Return as trigger event
            return create_user_input_event(line)

        except (KeyboardInterrupt, EOFError):
            self._exit_requested = True
            return None
        except Exception as e:
            logger.error("Error reading input", error=str(e))
            return None

    def _read_line(self) -> str | None:
        """Read a line from stdin (blocking)."""
        try:
            # Print prompt
            sys.stdout.write(self.prompt)
            sys.stdout.flush()

            # Read line
            line = sys.stdin.readline()
            if not line:
                return None
            return line
        except (KeyboardInterrupt, EOFError):
            return None

    async def render_command_data(
        self, result: UserCommandResult, command_name: str
    ) -> UserCommandResult | None:
        """CLI rendering: print/input for select and confirm."""
        data = result.data
        data_type = data.get("type", "")
        loop = asyncio.get_event_loop()

        if data_type == "confirm":
            print(data.get("message", "Confirm?"))
            answer = await loop.run_in_executor(None, lambda: input("[y/N]: ").strip())
            if answer.lower() in ("y", "yes"):
                action = data.get("action", "")
                args = data.get("action_args", "")
                if action:
                    return await self._execute_followup(action, args)
            return UserCommandResult(output="Cancelled.", consumed=True)

        if data_type == "select":
            options = data.get("options", [])
            if not options:
                return None
            print(data.get("title", "Select:"))
            for i, opt in enumerate(options, 1):
                marker = " *" if opt.get("selected") else ""
                label = opt.get("label", opt.get("value", ""))
                extra = opt.get("provider", "")
                extra_str = f"  ({extra})" if extra else ""
                print(f"  {i:>3}. {label}{extra_str}{marker}")
            print(f"  Enter number (1-{len(options)}) or name, empty to cancel:")
            choice = await loop.run_in_executor(None, lambda: input("> ").strip())
            if not choice:
                return UserCommandResult(output="Cancelled.", consumed=True)
            selected = None
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    selected = options[idx]["value"]
            else:
                selected = choice
            if selected:
                action = data.get("action", "")
                if action:
                    return await self._execute_followup(action, selected)
            return UserCommandResult(output="Cancelled.", consumed=True)

        return None


class NonBlockingCLIInput(BaseInputModule):
    """
    Non-blocking CLI input using select/poll.

    Useful when you need to check for input without blocking.
    """

    def __init__(
        self,
        prompt: str = "> ",
        timeout: float = 0.1,
    ):
        """
        Initialize non-blocking CLI input.

        Args:
            prompt: Input prompt
            timeout: Poll timeout in seconds
        """
        super().__init__()
        self.prompt = prompt
        self.timeout = timeout
        self._buffer = ""
        self._prompt_shown = False

    async def get_input(self) -> TriggerEvent | None:
        """
        Check for input without blocking.

        Returns:
            TriggerEvent if complete line available, None otherwise
        """
        if not self._running:
            return None

        # Show prompt if needed
        if not self._prompt_shown:
            sys.stdout.write(self.prompt)
            sys.stdout.flush()
            self._prompt_shown = True

        # Check for available input
        loop = asyncio.get_event_loop()
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, self._try_read),
                timeout=self.timeout,
            )
            if line is not None:
                self._prompt_shown = False
                return create_user_input_event(line.strip())
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error("Error in non-blocking read", error=str(e))

        return None

    def _try_read(self) -> str | None:
        """Try to read a line (may block briefly)."""
        # Check if input available (Unix only)
        if sys.platform != "win32":
            ready, _, _ = select.select([sys.stdin], [], [], self.timeout)
            if not ready:
                return None

        try:
            line = sys.stdin.readline()
            return line if line else None
        except Exception as e:
            logger.debug("stdin readline failed", error=str(e))
            return None
