"""Shared base of the browser tools: session lookup, argument reading, result shaping.

Every tool reaches its ``BrowserSession`` through ``browser.access`` (the
``browser`` plugin publishes it) and turns the session's ``BrowserResult`` or
raised ``BrowserError`` into a ``ToolResult``.
"""

import json
from pathlib import Path
from typing import Any

from kohakuterrarium.browser.access import browser_access
from kohakuterrarium.browser.actions import is_neutral
from kohakuterrarium.browser.errors import BrowserError
from kohakuterrarium.browser.results import wrap_untrusted
from kohakuterrarium.browser.session.types import BrowserResult
from kohakuterrarium.errors import KTError
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

ERROR_ORIGIN = "browser error detail"
TAB_ARG = "tab"
BODY_ARG = "content"
NO_PLUGIN = (
    "the browser tools need the browser plugin, which is not loaded for this "
    "creature; enable the plugin named 'browser'"
)
FAILED = "the browser call did not succeed; the result below says why"
_TRUE = {"true", "1", "yes", "y", "on"}
_FALSE = {"false", "0", "no", "n", "off", ""}


class ArgumentError(ValueError):
    """A tool argument is missing or malformed; the message names it and the fix."""


def text_arg(args: dict[str, Any], name: str) -> str | None:
    """A stripped non-empty string argument, or ``None`` when absent or blank."""
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ArgumentError(f"{name} must be a string")
    return value.strip() or None


def bool_arg(args: dict[str, Any], name: str, default: bool = False) -> bool:
    """A boolean argument; text formats may send ``"true"`` / ``"false"``."""
    value = args.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return value.strip().lower() in _TRUE
    raise ArgumentError(f"{name} must be true or false")


def int_arg(args: dict[str, Any], name: str, *, minimum: int = 1) -> int | None:
    """A whole-number argument of at least ``minimum``; text formats may send digits."""
    value = args.get(name)
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ArgumentError(f"{name} must be a whole number of at least {minimum}")
    return value


def files_list(value: Any) -> list[str]:
    """Paths from a list, a JSON list string, or one path per line."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except ValueError:
                raise ArgumentError(
                    'files must be a list of paths, e.g. ["report.pdf"]'
                ) from None
        else:
            value = [line.strip() for line in text.splitlines() if line.strip()]
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ArgumentError("files must be a list of non-empty path strings")
    return [item.strip() for item in value]


def guarded_path(raw: str, context: ToolContext | None, name: str) -> Path:
    """``raw`` resolved against the working directory and passed through the path guard."""
    path = resolve_tool_path(raw, context)
    guard = getattr(context, "path_guard", None)
    message = guard.check(str(path)) if guard is not None else None
    if message:
        raise ArgumentError(f"{name}: {message}")
    return path


class BrowserTool(BaseTool):
    """One browser tool: validate arguments, call the session, shape the result.

    Subclasses set ``arguments`` (accepted names besides ``content``) and
    ``neutral`` (each optional argument's "not given" value, which is dropped
    before ``parse``), and implement ``parse`` (arguments to call keywords)
    and ``run`` (the session call). ``failure`` is the error line shown above
    an ``is_error`` result.
    """

    needs_context = True
    is_concurrency_safe = False
    network_access = True
    arguments: tuple[str, ...] = ()
    neutral: dict[str, Any] = {}
    failure: str = FAILED

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        """The validated keywords for ``run``; raises ``ArgumentError``."""
        raise NotImplementedError

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        """Call the session and return its ``BrowserResult`` or a finished ``ToolResult``."""
        raise NotImplementedError

    def call_for(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        """Check names, drop neutral values and parse ``args`` into the keywords for ``run``."""
        self.check_names(args)
        given = {
            key: value
            for key, value in args.items()
            if key not in self.neutral or not is_neutral(value, self.neutral[key])
        }
        return self.parse(given, context)

    def check_names(self, args: dict[str, Any]) -> None:
        """Reject argument names this tool does not take, naming the ones it does."""
        allowed = set(self.arguments) | {BODY_ARG}
        unknown = sorted(k for k in args if k not in allowed and not k.startswith("_"))
        if unknown:
            raise ArgumentError(
                f"unknown argument {unknown[0]!r}; this tool takes "
                + ", ".join(self.arguments)
            )

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None, **kwargs: Any
    ) -> ToolResult:
        service = browser_access(context)
        if service is None:
            return ToolResult(error=NO_PLUGIN)
        session = None
        try:
            call = self.call_for(args, context)
            session = await service.prepare(context)
            result = await self.run(session, call, context)
        except ArgumentError as exc:
            return ToolResult(error=f"{self.tool_name}: {exc}")
        except BrowserError as exc:
            return self.error_result(exc, session or getattr(service, "session", None))
        except KTError as exc:
            return ToolResult(error=f"{self.tool_name}: {exc}")
        except Exception as exc:
            logger.warning(
                "Browser tool failed unexpectedly",
                tool_name=self.tool_name,
                error_type=type(exc).__name__,
            )
            return ToolResult(
                error=(
                    f"{self.tool_name} failed unexpectedly ({type(exc).__name__}); "
                    "take a snapshot to see the browser's state"
                )
            )
        if isinstance(result, ToolResult):
            return result
        return self.to_result(result)

    def error_result(self, exc: BrowserError, session: Any) -> ToolResult:
        """A raised session error: its safe message, its page-derived detail wrapped."""
        text = f"{self.tool_name}: {exc}"
        token = getattr(session, "token", None)
        if exc.detail and token:
            text += "\n" + wrap_untrusted(exc.detail, ERROR_ORIGIN, token)
        meta = {"tool": self.tool_name, "ok": False, "error": type(exc).__name__}
        return ToolResult(error=text, metadata=session_metadata(meta))

    def to_result(self, result: BrowserResult) -> ToolResult:
        """A session result as text output; ``is_error`` adds the failure line."""
        meta = {"tool": self.tool_name, "ok": not result.is_error, **result.meta}
        if result.is_error:
            return ToolResult(
                output=result.text, error=self.failure, metadata=session_metadata(meta)
            )
        return ToolResult(
            output=result.text, exit_code=0, metadata=session_metadata(meta)
        )


def session_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """The ``ToolResult.metadata`` envelope that carries ``meta`` to every surface."""
    return {"session_metadata": {"browser": meta}}


def tab_schema() -> dict[str, str]:
    """The ``tab`` property every browser tool's schema shares."""
    return {"type": "string", "description": 'Tab id; "" = active tab'}


def tab_arg(args: dict[str, Any]) -> str | None:
    """The ``tab`` argument: a tab id such as ``t2``, or ``None`` for the active tab."""
    return text_arg(args, TAB_ARG)
