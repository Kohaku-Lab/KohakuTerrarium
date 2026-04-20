"""Slash command dispatch for RichCLIApp.

Pulls user commands from the global builtin registry, runs them with a
``UserCommandContext``, and routes structured UI payloads (``select`` /
``confirm``) through the ``SelectorOverlay``. Lives in its own module
so the main app file stays under the 600-line cap.

Holds no state beyond the command registry; everything else is read
off the ``RichCLIApp`` reference at dispatch time.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    UserCommandResult,
    parse_slash_command,
)

if TYPE_CHECKING:
    from kohakuterrarium.builtins.cli_rich.app import RichCLIApp


class SlashHandler:
    """Dispatcher for ``/command`` input typed in the rich CLI."""

    def __init__(self, app: "RichCLIApp") -> None:
        self._app = app
        self._registry: dict[str, Any] = {}

    @property
    def registry(self) -> dict[str, Any]:
        """Read-only view of the wired command registry."""
        return self._registry

    def wire_builtins(self) -> dict[str, Any]:
        """Populate the registry with every registered builtin command."""
        registry: dict[str, Any] = {}
        for name in list_builtin_user_commands():
            cmd = get_builtin_user_command(name)
            if cmd:
                registry[name] = cmd
        self._registry = registry
        return registry

    async def handle(self, text: str) -> None:
        """Parse and execute a slash-command line, rendering any UI payload."""
        name, args = parse_slash_command(text)
        cmd = self._registry.get(name) or get_builtin_user_command(name)
        if cmd is None:
            self._app._commit_text(f"[red]Unknown command:[/red] /{name}")
            return

        try:
            result = await cmd.execute(args, self._build_context())
        except Exception as e:
            self._app._commit_text(f"[red]Command error:[/red] {e}")
            return

        # Interactive rendering: if the command returned a structured
        # UI payload (select/confirm) and no error, open an overlay.
        # The follow-up command's result replaces the original.
        if result.data and not result.error:
            followup = await self._render_data(result, name)
            if followup is not None:
                result = followup

        if result.error:
            self._app._commit_text(f"[red]{result.error}[/red]")
        elif result.output:
            self._app._commit_text(result.output)

        if name in ("exit", "quit"):
            self._app._exit_requested = True
            if self._app.app is not None:
                self._app.app.exit()

    # ── Internals ──

    def _build_context(self) -> UserCommandContext:
        agent = self._app.agent
        return UserCommandContext(
            agent=agent,
            session=getattr(agent, "session", None),
            input_module=getattr(agent, "input", None),
            extra={"command_registry": self._registry},
        )

    async def _render_data(
        self, result: UserCommandResult, command_name: str
    ) -> UserCommandResult | None:
        """Open an interactive overlay for a command's UI payload.

        Mirrors ``BaseInputModule.render_command_data`` for the rich
        CLI: ``select`` → arrow-key picker, ``confirm`` → y/n dialog.
        If the user chooses a value that wires to an ``action``, we
        execute that command and return its result.
        """
        data = result.data or {}
        data_type = data.get("type", "")
        selector = self._app.selector
        pt_app = self._app.app

        if data_type == "select":
            options = data.get("options", [])
            if not options:
                return None
            selected = await selector.show_select(
                title=data.get("title", "Select"),
                options=options,
                current=data.get("current", ""),
                app=pt_app,
            )
            if selected:
                action = data.get("action", "")
                if action:
                    return await self._execute_followup(action, selected)
            return UserCommandResult(output="", consumed=True)

        if data_type == "confirm":
            confirmed = await selector.show_confirm(
                data.get("message", "Confirm?"),
                app=pt_app,
            )
            if confirmed:
                action = data.get("action", "")
                args = data.get("action_args", "")
                if action:
                    return await self._execute_followup(action, args)
            return UserCommandResult(output="Cancelled.", consumed=True)

        return None

    async def _execute_followup(
        self, action: str, args: str
    ) -> UserCommandResult | None:
        """Run a follow-up command resolved from a UI payload action."""
        cmd = self._registry.get(action) or get_builtin_user_command(action)
        if cmd is None:
            return None
        return await cmd.execute(args, self._build_context())
