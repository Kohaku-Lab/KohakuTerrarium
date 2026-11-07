"""Smaller stand-ins for a session in a KT server (``remote_proxies.py`` holds the agent and the
engine): the history events message actions read, the trigger list, tool widgets' messages
and state, ``<ui>`` block state, a channel, and the modules routes.
"""

from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.remote import RemoteSession
    from kohakuterrarium.builtins.ktui.remote_proxies import RemoteAgent


class RemoteEvents:
    """``agent.session_store`` as message actions read it: the events of the history."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def get_events(self, name: str) -> list[dict]:
        return list(self.events)


class RemoteTriggers:
    """``agent.trigger_manager.list()`` for the Settings page, from the server's list."""

    def __init__(self) -> None:
        self._infos: list[Any] = []

    def load(self, rows: list[dict]) -> None:
        infos = []
        for row in rows:
            try:
                created = datetime.fromisoformat(str(row.get("created_at")))
            except ValueError:
                created = datetime.now()
            infos.append(
                SimpleNamespace(
                    trigger_id=str(row.get("trigger_id", "")),
                    trigger_type=str(row.get("trigger_type", "")),
                    running=bool(row.get("running")),
                    created_at=created,
                )
            )
        self._infos = infos

    def list(self) -> list[Any]:
        return list(self._infos)


class RemoteWidgets:
    """A tool widget's messages and state, sent to the widget service in the server."""

    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent

    def submit_message(self, msg: Any) -> str:
        self.agent.session.send_frame(
            {
                "type": "widget_msg",
                "event_id": msg.widget_id,
                "name": msg.name,
                "data": dict(msg.data or {}),
                "origin": msg.origin,
                "target": self.agent.config.name,
            }
        )
        return "sent"

    def submit_state(self, widget_id: str, state: dict, source: Any = None) -> str:
        self.agent.session.send_frame(
            {
                "type": "widget_state",
                "event_id": widget_id,
                "state": dict(state),
                "target": self.agent.config.name,
            }
        )
        return "sent"


class RemoteInlineStates:
    """``<ui>`` block state, kept here and saved in the server (as the web does)."""

    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent
        self.saved: dict[tuple, dict] = {}

    def get(self, turn: int, branch: int, block: str) -> dict | None:
        return self.saved.get((turn, branch, block))

    def submit(self, turn: int, branch: int, block: str, state: dict) -> str:
        self.saved[(turn, branch, block)] = dict(state)
        self.agent.session.send_frame(
            {
                "type": "inline_ui_state",
                "ui_turn": turn,
                "ui_branch": branch,
                "block": block,
                "state": dict(state),
                "target": self.agent.config.name,
            }
        )
        return "accepted"


class RemoteChannel:
    def __init__(self, session: "RemoteSession", name: str, history: list):
        self.session = session
        self.name = name
        self.history = [
            SimpleNamespace(
                sender=str(m.get("sender", "")),
                content=m.get("content", ""),
                message_id=str(m.get("message_id", "") or ""),
                timestamp=m.get("timestamp"),
            )
            for m in history
            if isinstance(m, dict)
        ]

    async def send(self, message: Any) -> None:
        await self.session.post(
            f"/api/sessions/topology/{self.session.session_id}/channels/{self.name}/send",
            {"content": message.content, "sender": message.sender},
        )


class RemoteModules:
    """A creature's modules (list, options, toggle) through the server's routes."""

    def __init__(self, session: "RemoteSession"):
        self.session = session

    def _path(self, cid: str, tail: str = "") -> str:
        base = f"/api/sessions/{self.session.session_id}/creatures/{cid}/modules"
        return f"{base}/{tail}" if tail else base

    async def list_modules(self, cid: str) -> list[dict]:
        body = await self.session.get(self._path(cid))
        return list((body or {}).get("modules") or [])

    async def get_module_options(self, cid: str, kind: str, name: str) -> dict:
        return await self.session.get(self._path(cid, f"{kind}/{name}/options")) or {}

    async def set_module_options(
        self, cid: str, kind: str, name: str, values: dict
    ) -> dict:
        path = self._path(cid, f"{kind}/{name}/options")
        return await self.session.put(path, {"values": values}) or {}

    async def toggle_module(self, cid: str, kind: str, name: str) -> dict:
        return await self.session.post(self._path(cid, f"{kind}/{name}/toggle")) or {}
