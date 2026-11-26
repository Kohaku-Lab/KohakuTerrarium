"""Stop a tool call that keeps failing the same way with the same arguments.

A call is keyed by tool name plus its canonical arguments. When the same key
fails ``repeat_failures`` times in a row with the same error and output, the
next identical call is blocked with a message the model reads as the tool
result. Streaks are per plugin instance and end when a new turn opens.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.core.events import EventType
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.modules.plugin.option_validation import validate_plugin_options
from kohakuterrarium.modules.tool.base import ToolResult

DEFAULT_REPEAT_FAILURES = 3

# Call-control arguments that do not change what the tool does.
_CONTROL_KEYS = frozenset({"run_in_background", "allow_concurrent"})
# Events produced by the agent's own work; every other event opens a new turn.
_SAME_TURN_EVENTS = frozenset({EventType.TOOL_COMPLETE, EventType.SUBAGENT_OUTPUT})
_ERROR_PREVIEW_CHARS = 200


def call_key(tool_name: str, args: dict[str, Any] | None) -> str:
    """Return the identity of a call: tool name plus canonical arguments."""
    cleaned = {k: v for k, v in (args or {}).items() if k not in _CONTROL_KEYS}
    canonical = json.dumps(
        cleaned,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{tool_name}\x00{canonical}"


@dataclass
class _Streak:
    """Consecutive identical failures of one call key."""

    count: int
    fingerprint: str
    error: str


class RepeatGuardPlugin(BasePlugin):
    """Blocks the next identical call after K identical failures in one turn."""

    name = "repeat_guard"
    description = (
        "Stops a tool call that keeps failing the same way with the same arguments."
    )
    # After sandbox / budget, before permgate asks the user about a doomed call.
    priority = 10

    @classmethod
    def option_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "repeat_failures": {
                "type": "int",
                "default": DEFAULT_REPEAT_FAILURES,
                "min": 0,
                "doc": (
                    "Block a tool call after it failed this many times in a "
                    "row with the same arguments and the same error. "
                    "0 turns the guard off."
                ),
            },
        }

    def __init__(
        self,
        *,
        repeat_failures: Any = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        opts: dict[str, Any] = dict(options or {})
        if repeat_failures is not None:
            opts.setdefault("repeat_failures", repeat_failures)
        cleaned = validate_plugin_options(self.name, opts, self.option_schema())
        self.options = {"repeat_failures": cleaned.get("repeat_failures")}
        self._streaks: dict[str, _Streak] = {}
        self._pending: dict[str, str] = {}
        self.refresh_options()

    @property
    def limit(self) -> int:
        """Failures that arm the block; 0 when the guard is off."""
        return int(self.options["repeat_failures"])

    def refresh_options(self) -> None:
        """Fill the default and drop all state when the guard is turned off."""
        if self.options.get("repeat_failures") is None:
            self.options["repeat_failures"] = DEFAULT_REPEAT_FAILURES
        if self.limit == 0:
            self._reset()

    def failure_count(self, tool_name: str, args: dict[str, Any] | None) -> int:
        """Current streak length for one call."""
        streak = self._streaks.get(call_key(tool_name, args))
        return streak.count if streak is not None else 0

    def _reset(self) -> None:
        self._streaks.clear()
        self._pending.clear()

    async def on_event(self, event: Any) -> None:
        if event is not None and getattr(event, "type", None) not in _SAME_TURN_EVENTS:
            self._reset()

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> None:
        if self.limit == 0:
            return None
        tool_name = _tool_name(kwargs)
        key = call_key(tool_name, args)
        streak = self._streaks.get(key)
        if streak is not None and streak.count >= self.limit:
            raise PluginBlockError(
                f"This exact call to {tool_name} failed {streak.count} times in a "
                f"row with the same arguments: {streak.error}. Change the "
                "arguments or take another approach; repeating it will fail the "
                "same way."
            )
        job_id = kwargs.get("job_id")
        if job_id:
            self._pending[job_id] = key
        return None

    async def post_tool_execute(self, result: Any, **kwargs: Any) -> None:
        job_id = kwargs.get("job_id")
        key = self._pending.pop(job_id, None) if job_id else None
        if self.limit == 0 or not isinstance(result, ToolResult):
            return None
        if key is None:
            key = call_key(_tool_name(kwargs), kwargs.get("args"))
        if result.success:
            self._streaks.pop(key, None)
            return None
        fingerprint = _failure_fingerprint(result)
        streak = self._streaks.get(key)
        if streak is not None and streak.fingerprint == fingerprint:
            streak.count += 1
        else:
            self._streaks[key] = _Streak(1, fingerprint, _short_error(result))
        return None


def _tool_name(kwargs: dict[str, Any]) -> str:
    name = kwargs.get("tool_name")
    if not name:
        name = getattr(kwargs.get("tool"), "tool_name", "")
    return str(name or "tool")


def _failure_fingerprint(result: ToolResult) -> str:
    payload = json.dumps(
        [result.error, result.exit_code, result.get_text_output()],
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _short_error(result: ToolResult) -> str:
    text = (result.error or "").strip()
    if not text:
        text = result.get_text_output().strip()
    if not text:
        return f"exit code {result.exit_code}"
    line = text.splitlines()[0].strip().rstrip(".")
    if len(line) > _ERROR_PREVIEW_CHARS:
        line = line[:_ERROR_PREVIEW_CHARS] + "..."
    return line
