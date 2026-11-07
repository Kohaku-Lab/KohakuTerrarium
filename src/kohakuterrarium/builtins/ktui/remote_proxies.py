"""Stand-ins for the engine, creatures and agents of a session that runs in a KT server, so
the shell drives a remote session as it drives a local one (``remote.py`` carries the
connection). Each stand-in has what ktui reads and calls; actions become the server's REST
routes or chat-socket frames: messages, replies, commands, interrupt / stop / background,
model and folder, modules, re-runs and versions of past messages, channels and graph edits,
drives (``remote_drives.py``).
"""

import asyncio
import os
from pathlib import Path
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.ktui.remote_drives import RemoteDrives
from kohakuterrarium.builtins.ktui.remote_parts import (
    RemoteChannel,
    RemoteEvents,
    RemoteInlineStates,
    RemoteModules,
    RemoteTriggers,
    RemoteWidgets,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.remote import RemoteSession

logger = get_logger(__name__)

NOT_HERE = "not available while attached to a server (use the web UI)"


class NotAttachedLocally(RuntimeError):
    """An action only a local engine can do."""

    def __init__(self) -> None:
        super().__init__(NOT_HERE)


@dataclass
class RemoteGraph:
    """The shape ktui reads from a graph: channels, members and their edges."""

    graph_id: str
    creature_ids: list[str] = field(default_factory=list)
    channels: dict[str, Any] = field(default_factory=dict)
    listen_edges: dict[str, set[str]] = field(default_factory=dict)
    send_edges: dict[str, set[str]] = field(default_factory=dict)
    parent_links: dict[str, str] = field(default_factory=dict)


@dataclass
class CommandResult:
    output: str = ""
    error: str | None = None
    data: Any = None
    consumed: bool = True
    success: bool = True


class _Running:
    """``agent._processing_task`` as ktui reads it: done unless a turn is running."""

    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent

    def done(self) -> bool:
        return not self.agent.processing


class RemoteRouter:
    """The agent's output router: ktui's output sits in ``default_output``; replies to
    questions and approvals go back over the chat socket."""

    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent
        self._output: Any = None
        self.inline_ui = RemoteInlineStates(agent)
        self.widgets = RemoteWidgets(agent)

    @property
    def default_output(self) -> Any:
        return self._output

    @default_output.setter
    def default_output(self, output: Any) -> None:
        # as KT's router does: the output answers questions through this router
        self._output = output
        if output is not None:
            object.__setattr__(output, "_router", self)

    def submit_reply_with_status(self, reply: Any) -> tuple[bool, str]:
        self.agent.session.send_frame(
            {
                "type": "ui_reply",
                "event_id": reply.event_id,
                "action_id": reply.action_id,
                "values": dict(reply.values or {}),
                "target": self.agent.config.name,
            }
        )
        return True, "sent"


class RemoteWorkspace:
    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent

    def get(self) -> str:
        return self.agent.pwd

    def set(self, path: str) -> str:
        agent = self.agent
        agent.session.spawn(agent.set_working_dir(path))
        return path


class RemoteAgent:
    def __init__(self, session: "RemoteSession", info: dict):
        self.session = session
        self.creature_id = str(info.get("creature_id") or info.get("agent_id") or "")
        self.config = SimpleNamespace(
            name=str(info.get("name", "")), model=str(info.get("model", ""))
        )
        self.llm_name = str(info.get("llm_name", "") or "")
        self.pwd = str(info.get("pwd", "") or "")
        self._running = bool(info.get("running", True))
        self.processing = bool(info.get("is_processing", False))
        self.output_router = RemoteRouter(self)
        self.workspace = RemoteWorkspace(self)
        self.executor = SimpleNamespace(_working_dir=self.pwd)
        # the history last read from the server, for message actions and versions
        self.session_store = RemoteEvents()
        # the version chosen per turn (``/branch``), as the agent keeps it
        self._branch_view: dict[int, int] = {}
        self.input = SimpleNamespace(_user_commands={})
        self.plugins = None
        self.trigger_manager = RemoteTriggers()
        # read from the server with the creature list: its env, prompt
        self.remote_env: dict[str, str] | None = None
        self.system_prompt = ""
        self._processing_task = _Running(self)
        # the turn the server is on (from its frames), for <ui> block state
        self._turn_index: int | None = None
        self._branch_id: int | None = None
        self._commands: dict[str, Any] = {}

    def update(self, info: dict) -> None:
        self.config.model = str(info.get("model", "") or self.config.model)
        self.llm_name = str(info.get("llm_name", "") or self.llm_name)
        self.pwd = str(info.get("pwd", "") or self.pwd)
        self.executor._working_dir = self.pwd
        self._running = bool(info.get("running", self._running))

    def _path(self, tail: str) -> str:
        return f"/api/sessions/{self.session.session_id}/creatures/{self.creature_id}/{tail}"

    def llm_identifier(self) -> str:
        return self.llm_name or self.config.model

    # -- talking --------------------------------------------------------

    async def inject_input(
        self, content: Any, source: str = "cli", pending_id: str | None = None
    ) -> None:
        if isinstance(content, list):
            # attachments travel as content-part dicts
            content = [p.to_dict() if hasattr(p, "to_dict") else p for p in content]
        frame = {"type": "input", "content": content, "target": self.config.name}
        if pending_id:
            frame["event_id"] = pending_id
        self.session.send_frame(frame)

    def interrupt(self) -> None:
        self.session.spawn(self.session.post(self._path("interrupt")))

    def cancel_pending(self, pending_id: str) -> bool:
        self.session.send_frame({"type": "input_cancel", "event_id": pending_id})
        return True

    def _cancel_job(self, job_id: str, name: str = "") -> None:
        self.session.spawn(self.session.post(self._path(f"tasks/{job_id}/stop")))

    def _promote_handle(self, job_id: str) -> bool:
        self.session.spawn(self.session.post(self._path(f"promote/{job_id}")))
        return True

    async def set_working_dir(self, path: str) -> None:
        body = await self.session.put(self._path("working-dir"), {"path": path})
        if body:
            self.pwd = str(body.get("pwd") or path)
            self.executor._working_dir = self.pwd

    # -- re-runs ------------------------------------------------------------

    def _branch_body(self, turn_index: int | None, branch_view: dict | None) -> dict:
        body: dict[str, Any] = {}
        if turn_index is not None:
            body["turn_index"] = turn_index
        if branch_view:
            body["branch_view"] = {str(k): v for k, v in branch_view.items()}
        return body

    async def regenerate_last_response(
        self, turn_index: int | None = None, branch_view: dict | None = None
    ) -> None:
        body = self._branch_body(turn_index, branch_view)
        _raise_failed(
            await self.session.post(self._path("regenerate"), body, timeout=None)
        )

    async def edit_and_rerun(
        self,
        msg_idx: int,
        content: Any,
        turn_index: int | None = None,
        branch_view: dict | None = None,
    ) -> None:
        body = {"content": content, **self._branch_body(turn_index, branch_view)}
        _raise_failed(
            await self.session.post(
                self._path(f"messages/{msg_idx}/edit"), body, timeout=None
            )
        )

    async def refresh_events(self) -> None:
        """The history again (after a re-run or a version switch)."""
        for creature in self.session.creatures.values():
            if creature.agent is self:
                await self.session.history(creature)

    def get_system_prompt(self) -> str:
        if not self.system_prompt:
            raise NotAttachedLocally()
        return self.system_prompt

    async def load_state(self) -> None:
        """The server's view of this creature: env, triggers, system prompt."""
        session = self.session
        env, triggers, prompt = await asyncio.gather(
            session.get(self._path("env")),
            session.get(self._path("triggers")),
            session.get(self._path("system-prompt")),
            return_exceptions=True,
        )
        if isinstance(env, dict):
            self.remote_env = dict(env.get("env") or {})
        if isinstance(triggers, list):
            self.trigger_manager.load(triggers)
        if isinstance(prompt, dict):
            self.system_prompt = str(prompt.get("text") or "")

    # -- commands -------------------------------------------------------

    async def load_commands(self) -> None:
        body = await self.session.get(self._path("command-inventory"))
        commands = (body or {}).get("commands") or []
        self._commands = {
            str(c.get("name")): SimpleNamespace(
                description=str(c.get("description", "") or ""),
                aliases=list(c.get("aliases") or []),
                needs_engine=False,
            )
            for c in commands
            if c.get("name")
        }

    def list_user_commands(self) -> dict[str, Any]:
        return dict(self._commands)

    async def _try_slash_command_text(self, text: str) -> CommandResult | None:
        name, _, args = text.strip().lstrip("/").partition(" ")
        if not name:
            return None
        try:
            body = await self.session.post(
                self._path("command"), {"command": name, "args": args.strip()}
            )
        except Exception as exc:
            return CommandResult(error=str(exc), success=False)
        body = body or {}
        # a command may have changed the model or the folder
        await self.session.refresh_creatures()
        if name == "branch" and not body.get("error"):
            # the version shown per turn is kept here, as a local agent keeps it
            turn, _, branch = args.strip().partition(" ")
            if turn.isdigit() and branch.strip().isdigit():
                self._branch_view[int(turn)] = int(branch)
        return CommandResult(
            output=str(body.get("output") or ""),
            error=body.get("error") or None,
            data=body.get("data"),
            success=bool(body.get("success", True)),
        )


class RemoteCreature:
    def __init__(self, session: "RemoteSession", info: dict):
        self.creature_id = str(info.get("creature_id") or info.get("agent_id") or "")
        self.name = str(info.get("name", ""))
        self.graph_id = str(info.get("graph_id") or session.session_id)
        self.agent = RemoteAgent(session, info)
        self.listen_channels = list(info.get("listen_channels") or [])
        self.send_channels = list(info.get("send_channels") or [])

    def update(self, info: dict) -> None:
        self.agent.update(info)
        self.listen_channels = list(info.get("listen_channels") or [])
        self.send_channels = list(info.get("send_channels") or [])


class RemoteEngine:
    """What the shell asks of an engine, for a session in a server."""

    # drives go through the server's routes (remote_drives.py); not None: they are there
    drives = "remote"
    # leaving ktui detaches: the session goes on in the server
    keeps_running = True

    def __init__(self, session: "RemoteSession"):
        self.session = session

    @property
    def remote_drives(self) -> RemoteDrives:
        return RemoteDrives(self.session)

    @property
    def remote_service(self) -> "RemoteModules":
        return RemoteModules(self.session)

    def list_creatures(self) -> list[RemoteCreature]:
        return list(self.session.creatures.values())

    def get_creature(self, cid: str) -> RemoteCreature:
        return self.session.creatures[cid]

    def get_graph(self, graph_id: str) -> RemoteGraph:
        session = self.session
        if graph_id != session.session_id:
            raise KeyError(graph_id)
        graph = RemoteGraph(graph_id, list(session.creatures))
        graph.channels = {
            c["name"]: SimpleNamespace(
                name=c["name"], description=c.get("description", "")
            )
            for c in session.channels
        }
        for cid, creature in session.creatures.items():
            graph.listen_edges[cid] = set(creature.listen_channels)
            graph.send_edges[cid] = set(creature.send_channels)
        return graph

    def channel(self, graph_id: str, name: str) -> RemoteChannel:
        history = self.session.channel_history.get(name)
        if history is None:
            raise KeyError(name)
        return RemoteChannel(self.session, name, history)

    def subscribe(self, event_filter: Any = None) -> Any:
        return self.session.events(event_filter)

    async def stop(self, cid: str) -> None:
        await self.session.post(
            f"/api/sessions/{self.session.session_id}/creatures/{cid}/stop"
        )
        await self.session.refresh_creatures()

    async def start(self, cid: str) -> None:
        await self.session.post(
            f"/api/sessions/{self.session.session_id}/creatures/{cid}/start"
        )
        await self.session.refresh_creatures()

    async def add_creature(
        self, config: Any, *, name: str | None = None, **_: Any
    ) -> SimpleNamespace:
        """A creature from a config path or ``@pkg/...`` ref, added in the server."""
        path = str(config)
        label = name or Path(path).stem
        body = await self.session.post(
            f"/api/sessions/active/{self.session.session_id}/creatures",
            {"name": label, "config_path": path},
        )
        await self.session.refresh_creatures()
        return SimpleNamespace(name=label, creature_id=(body or {}).get("creature_id"))

    async def remove_creature(self, cid: str, **_: Any) -> None:
        await self.session.delete(
            f"/api/sessions/active/{self.session.session_id}/creatures/{cid}"
        )
        await self.session.refresh_creatures()

    async def add_channel(
        self, graph_id: str, name: str, description: str = ""
    ) -> None:
        await self.session.post(
            f"/api/sessions/topology/{self.session.session_id}/channels",
            {"name": name, "description": description},
        )
        await self.session.refresh_creatures()

    async def _wire(self, verb: str, sender: str, receiver: str, channel: Any) -> None:
        body = {"sender": sender, "receiver": receiver, "channel": channel}
        await self.session.post(
            f"/api/sessions/topology/{self.session.session_id}/{verb}", body
        )
        await self.session.refresh_creatures()

    async def connect(
        self, sender: str, receiver: str, *, channel: str | None = None, **_: Any
    ) -> None:
        await self._wire("connect", sender, receiver, channel)

    async def disconnect(
        self, sender: str, receiver: str, *, channel: str | None = None, **_: Any
    ) -> None:
        await self._wire("disconnect", sender, receiver, channel)


def _raise_failed(body: Any) -> None:
    """A re-run the server answered but could not finish fails here as it would locally."""
    error = body.get("execution_error") if isinstance(body, dict) else None
    if error:
        raise RuntimeError(str(error))


def server_token() -> str | None:
    """A host token for a server that asks for one (``KT_HOST_TOKEN``)."""
    return os.environ.get("KT_HOST_TOKEN") or None
