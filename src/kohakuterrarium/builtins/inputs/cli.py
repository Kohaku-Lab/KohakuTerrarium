"""Terminal input modules for blocking and polled agent interaction."""

import asyncio
import select
import sys

from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.modules.user_command.base import UserCommandResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class CLIInput(BaseInputModule):
    """Read terminal lines asynchronously and dispatch slash commands."""

    def __init__(
        self,
        prompt: str = "> ",
        *,
        exit_commands: list[str] | None = None,
    ):
        """Configure the prompt and fallback exit commands."""
        super().__init__()
        self.prompt = prompt
        self.exit_commands = exit_commands or ["/exit", "/quit", "exit", "quit"]
        self._exit_requested = False

    @property
    def exit_requested(self) -> bool:
        """Return whether terminal input requested shutdown."""
        return self._exit_requested

    async def _on_start(self) -> None:
        """Log that terminal input is ready."""
        logger.debug("CLI input started", prompt=self.prompt)

    async def _on_stop(self) -> None:
        """Log that terminal input has stopped."""
        logger.debug("CLI input stopped")

    async def get_input(self) -> TriggerEvent | None:
        """Return the next user event, or ``None`` after exit or EOF."""
        if not self._running or self._exit_requested:
            return None

        try:
            loop = asyncio.get_event_loop()
            line = await loop.run_in_executor(None, self._read_line)

            if line is None:
                self._exit_requested = True
                return None

            line = line.strip()

            # Raw exit words remain available when user commands are not installed.
            if not self._user_commands and line.lower() in self.exit_commands:
                self._exit_requested = True
                return None

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

            return create_user_input_event(line)

        except (KeyboardInterrupt, EOFError):
            self._exit_requested = True
            return None
        except Exception as e:
            logger.error("Error reading input", error=str(e))
            return None

    def _read_line(self) -> str | None:
        """Read one blocking line from stdin."""
        try:
            sys.stdout.write(self.prompt)
            sys.stdout.flush()

            line = sys.stdin.readline()
            if not line:
                return None
            return line
        except (KeyboardInterrupt, EOFError):
            return None

    async def render_command_data(
        self, result: UserCommandResult, command_name: str
    ) -> UserCommandResult | None:
        """Render confirm and select command payloads in a plain terminal."""
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
    """Poll terminal input without holding the agent loop."""

    def __init__(
        self,
        prompt: str = "> ",
        timeout: float = 0.1,
    ):
        """Configure the prompt and polling interval."""
        super().__init__()
        self.prompt = prompt
        self.timeout = timeout
        self._buffer = ""
        self._prompt_shown = False

    async def get_input(self) -> TriggerEvent | None:
        """Return a complete input line when one is available."""
        if not self._running:
            return None

        if not self._prompt_shown:
            sys.stdout.write(self.prompt)
            sys.stdout.flush()
            self._prompt_shown = True

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
        """Read one line if the platform reports stdin readiness."""
        # Windows lacks the same ``select`` support for console handles.
        if sys.platform != "win32":
            ready, _, _ = select.select([sys.stdin], [], [], self.timeout)
            if not ready:
                return None

        try:
            line = sys.stdin.readline()
            return line if line else None
        except Exception as e:
            logger.warning("stdin readline failed", error=str(e), exc_info=True)
            return None
