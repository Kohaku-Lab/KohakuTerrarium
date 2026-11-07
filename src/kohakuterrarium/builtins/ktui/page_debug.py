"""The Debug page, as the web's Debug tab, with four views picked at the top:

* **Logs**: KT's log, warnings and stray output (the log tail), newest at the bottom; attached
  to a server on this machine, the server's log file too.
* **Events**: the focused creature's recorded session events, newest last.
* **Prompt**: its system prompt as the model sees it now.
* **Trace**: its tool calls with their arguments and results.
"""

import json
import time
from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.builtins.ktui.logtail import LogTail
from kohakuterrarium.builtins.ktui.servers import server_log, tail
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

VIEWS = ("logs", "events", "prompt", "trace")
LOG_SHOWN = 400
EVENTS_SHOWN = 200
TRACE_SHOWN = 80


def _events(shell: "KtuiShell") -> list[dict]:
    agent = shell.focused.agent
    store = getattr(agent, "session_store", None)
    if store is None:
        return []
    try:
        return list(store.get_events(agent.config.name))
    except Exception as exc:
        logger.debug("events not read", error=str(exc))
        return []


def _summary(event: dict) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    for key in ("content", "name", "output", "error", "summary", "text"):
        value = data.get(key)
        if value:
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False, default=str)
            return one_line(value, 90)
    return ""


def _stamp(event: dict) -> str:
    ts = event.get("ts") or event.get("timestamp")
    if isinstance(ts, (int, float)):
        return time.strftime("%H:%M:%S", time.localtime(ts))
    return str(ts or "")[11:19]


def _mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class DebugPage:
    def __init__(self, shell: "KtuiShell", logs: LogTail):
        self.shell = shell
        self.logs = logs
        self.view = "logs"
        self.seen = -1
        self.server_seen = 0.0

    def _server_log(self):
        """Attached to a server on this machine: its log file."""
        if not getattr(self.shell.engine, "keeps_running", False):
            return None
        return server_log(self.shell.engine.session.base_url)

    def changed(self) -> bool:
        """Whether the shown view has something new (the logs and events views tick)."""
        if self.view == "logs":
            path = self._server_log()
            grown = path is not None and _mtime(path) != self.server_seen
            return grown or self.logs.count != self.seen
        return self.view == "events"

    def sections(self) -> list[tuple[str, str | None, str, Any]]:
        field = {
            "name": "debug_view",
            "label": "View",
            "options": list(VIEWS),
            "value": self.view,
        }
        out: list = [("view", None, "fields", [field])]
        match self.view:
            case "logs":
                self.seen = self.logs.count
                self.logs.unseen = 0
                body = "\n".join(self.logs.snapshot(LOG_SHOWN)) or "no log lines yet"
                out.append(
                    ("logs", "Log · KT, warnings and stray output", "text", body)
                )
                path = self._server_log()
                if path is not None:
                    self.server_seen = _mtime(path)
                    lines = tail(path, LOG_SHOWN)
                    out.append(
                        (
                            "server_log",
                            f"Log · the KT server ({path})",
                            "text",
                            "\n".join(lines) or "empty",
                        )
                    )
            case "events":
                events = _events(self.shell)[-EVENTS_SHOWN:]
                rows = [
                    f"{_stamp(e)}  {e.get('type') or e.get('etype') or '?':<18} {_summary(e)}"
                    for e in events
                ]
                title = f"Events · {self.shell.focused.view.name} · last {len(rows)}"
                out.append(
                    ("events", title, "text", "\n".join(rows) or "no events recorded")
                )
            case "prompt":
                try:
                    prompt = self.shell.focused.agent.get_system_prompt()
                except Exception as exc:
                    prompt = f"unavailable: {exc}"
                title = f"System prompt · {self.shell.focused.view.name} · {len(prompt)} chars"
                out.append(("prompt", title, "text", prompt))
            case "trace":
                out.append(("trace", "Tool calls", "text", self._trace()))
        return out

    def _trace(self) -> str:
        lines: list[str] = []
        calls: dict[str, str] = {}
        for event in _events(self.shell):
            kind = event.get("type") or ""
            data = event.get("data") if isinstance(event.get("data"), dict) else event
            if kind == "tool_call":
                args = json.dumps(
                    data.get("args") or {}, ensure_ascii=False, default=str
                )
                calls[str(data.get("call_id", ""))] = (
                    f"{_stamp(event)}  ▸ {data.get('name', '?')}  {one_line(args, 120)}"
                )
            elif kind == "tool_result":
                head = calls.pop(str(data.get("call_id", "")), f"{_stamp(event)}  ▸ ?")
                error = data.get("error")
                result = one_line(str(error or data.get("output") or ""), 120)
                lines.append(head)
                lines.append(f"            {'✗' if error else '✓'} {result}")
        lines = lines[-TRACE_SHOWN * 2 :]
        return "\n".join(lines) or "no tool calls yet"
