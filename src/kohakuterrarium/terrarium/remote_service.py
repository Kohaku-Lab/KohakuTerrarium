"""TerrariumService implementation backed by Lab APP calls.

A :class:`RemoteTerrariumService` is the controller's handle to a
worker node's Terrarium engine.  Every method translates to an APP
request against namespace ``terrarium.runtime`` (one-shot) or opens a
streamed reply via :class:`~kohakuterrarium.laboratory.streams.RemoteStream`
against ``terrarium.events`` (``chat``, ``subscribe``).

Construction requires three things from the caller:

- ``sender`` — anything with ``async request(...)``; in production this
  is the controller-side :class:`HostEngine`.
- ``target_node`` — the worker's lab client_id.
- ``demux`` — a process-wide :class:`StreamDemux` installed on the
  controller (typically owned by :class:`MultiNodeTerrariumService`).

Errors raised by the worker's engine come back as structured bodies
(``{"error": {"kind": "...", "message": "..."}}``); this service
translates them back into Python exceptions:

- ``not_found`` → :class:`KeyError`
- ``invalid`` → :class:`ValueError`
- anything else → :class:`RemoteEngineError`

``shutdown`` is intentionally a local no-op: tearing down a worker's
engine over the wire is a destructive operation that belongs in
explicit admin tooling, not the standard service surface.
"""

from collections.abc import AsyncIterator
from typing import Any

from kohakuterrarium.laboratory.protocols import LabSender
from kohakuterrarium.laboratory.streams import RemoteStream, StreamDemux
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
)
from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
)
from kohakuterrarium.terrarium.wire import (
    pack_content,
    pack_creature_build_input,
    pack_event_filter,
    unpack_channel_info,
    unpack_connection_result,
    unpack_creature_info,
    unpack_disconnection_result,
    unpack_engine_event,
    unpack_graph_topology,
    unpack_topology_delta,
)


class RemoteEngineError(RuntimeError):
    """Raised for engine-side failures that aren't KeyError / ValueError."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class CreatureNotHostedHere(KeyError):
    """Raised when the targeted worker doesn't host the named creature.

    Distinct from a generic ``KeyError`` (``not_found`` wire kind) so
    :class:`MultiNodeTerrariumService` can specifically retry stale-
    routing without re-running ops whose KeyError originated from
    *inside* a successful engine call.
    """


def _maybe_raise(body: Any) -> dict[str, Any]:
    # Only an ``{"error": {...}}`` envelope is a failure. A successful
    # response can legitimately carry ``error: None`` (e.g. a slash-command
    # result whose dataclass has an ``error`` field) — that is NOT a
    # failure and must not be treated as an error envelope.
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        kind = err.get("kind", "unknown")
        message = err.get("message", "")
        if kind == "creature_not_hosted":
            # Specific "wrong worker" signal — MultiNodeTerrariumService
            # uses this to drive stale-routing retries.
            raise CreatureNotHostedHere(message)
        if kind == "not_found":
            raise KeyError(message)
        if kind == "invalid":
            raise ValueError(message)
        raise RemoteEngineError(kind, message)
    return body


class RemoteTerrariumService:
    """Controller-side proxy for a worker node's Terrarium engine.

    Implements the :class:`TerrariumService` Protocol over Lab APP
    requests.  Construct one per remote node the controller knows
    about; typically managed by :class:`MultiNodeTerrariumService`.
    """

    def __init__(
        self,
        sender: LabSender,
        target_node: str,
        *,
        demux: StreamDemux,
        request_timeout: float = 30.0,
    ) -> None:
        self._sender = sender
        self._target_node = target_node
        self._demux = demux
        self._timeout = request_timeout

    @property
    def node_id(self) -> str:
        return self._target_node

    @property
    def engine(self) -> Any:
        """Local escape hatch — undefined for remote services.

        Code that reaches for ``service.engine`` is bound to single-host
        mode by definition; surfacing a clear error here makes the
        coupling obvious.
        """
        raise NotImplementedError(
            "RemoteTerrariumService has no local engine; this service "
            "speaks to a worker node over Lab.  Use the Protocol surface."
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_creatures(self) -> tuple[CreatureInfo, ...]:
        body = _maybe_raise(await self._req("list_creatures", {}))
        return tuple(unpack_creature_info(c) for c in body["creatures"])

    async def get_creature_info(self, creature_id: str) -> CreatureInfo | None:
        body = _maybe_raise(
            await self._req("get_creature_info", {"creature_id": creature_id})
        )
        info = body.get("creature_info")
        return unpack_creature_info(info) if info is not None else None

    async def list_graphs(self) -> tuple[GraphTopology, ...]:
        body = _maybe_raise(await self._req("list_graphs", {}))
        return tuple(unpack_graph_topology(g) for g in body["graphs"])

    async def get_graph(self, graph_id: str) -> GraphTopology | None:
        body = _maybe_raise(await self._req("get_graph", {"graph_id": graph_id}))
        g = body.get("graph")
        return unpack_graph_topology(g) if g is not None else None

    async def list_channels(self, graph_id: str) -> tuple[ChannelInfo, ...]:
        body = _maybe_raise(await self._req("list_channels", {"graph_id": graph_id}))
        return tuple(unpack_channel_info(c) for c in body["channels"])

    async def creature_status(self, creature_id: str) -> dict[str, Any] | None:
        body = _maybe_raise(
            await self._req("creature_status", {"creature_id": creature_id})
        )
        return body.get("status")

    async def status_snapshot(self) -> dict[str, Any]:
        body = _maybe_raise(await self._req("status_snapshot", {}))
        return body["status"]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def add_creature(
        self,
        config: Any,
        *,
        graph_id: str | None = None,
        creature_id: str | None = None,
        is_privileged: bool = False,
        parent_creature_id: str | None = None,
        start: bool = True,
        pwd: str | None = None,
        llm_override: Any = None,
        on_node: str | None = None,
        name: str | None = None,
    ) -> CreatureInfo:
        if isinstance(config, Creature):
            raise TypeError(
                "RemoteTerrariumService.add_creature does not accept "
                "pre-built Creature objects (they hold local references)"
            )
        if on_node is not None and on_node != self._target_node:
            raise ValueError(
                f"on_node={on_node!r} mismatches "
                f"RemoteTerrariumService.target_node={self._target_node!r}; "
                "this service only routes to its own target worker"
            )
        # Path-form is allowed — bytes must have been pushed via
        # studio.deploy first.  In-memory AgentConfig works without
        # any prior deploy.  CreatureConfig still rejected.
        packed = pack_creature_build_input(config)
        body = _maybe_raise(
            await self._req(
                "add_creature",
                {
                    "config": packed,
                    "graph_id": graph_id,
                    "creature_id": creature_id,
                    "is_privileged": is_privileged,
                    "parent_creature_id": parent_creature_id,
                    "start": start,
                    "pwd": pwd if isinstance(pwd, str) or pwd is None else str(pwd),
                    "llm_override": llm_override,
                    "name": name,
                },
            )
        )
        return unpack_creature_info(body["creature_info"])

    async def remove_creature(self, creature_id: str) -> None:
        _maybe_raise(await self._req("remove_creature", {"creature_id": creature_id}))

    async def start_creature(self, creature_id: str) -> None:
        _maybe_raise(await self._req("start_creature", {"creature_id": creature_id}))

    async def stop_creature(self, creature_id: str) -> None:
        _maybe_raise(await self._req("stop_creature", {"creature_id": creature_id}))

    async def shutdown(self) -> None:
        """Local no-op — does not tear down the remote engine.

        Closing a worker's engine over the wire is destructive enough
        that it belongs in explicit admin tooling, not a default
        :meth:`TerrariumService.shutdown` call from Studio teardown.
        """
        return None

    # ------------------------------------------------------------------
    # Per-creature control (per ``api-lab-design.md`` §2)
    # ------------------------------------------------------------------

    async def interrupt(self, creature_id: str) -> None:
        _maybe_raise(await self._req("interrupt", {"creature_id": creature_id}))

    async def list_jobs(self, creature_id: str) -> list[dict[str, Any]]:
        body = _maybe_raise(await self._req("list_jobs", {"creature_id": creature_id}))
        return list(body.get("jobs", []))

    async def stop_job(self, creature_id: str, job_id: str) -> bool:
        body = _maybe_raise(
            await self._req("stop_job", {"creature_id": creature_id, "job_id": job_id})
        )
        return bool(body.get("cancelled", False))

    async def promote_job(self, creature_id: str, job_id: str) -> bool:
        body = _maybe_raise(
            await self._req(
                "promote_job", {"creature_id": creature_id, "job_id": job_id}
            )
        )
        return bool(body.get("promoted", False))

    # ------------------------------------------------------------------
    # Per-creature chat ops
    # ------------------------------------------------------------------

    async def chat_history(self, creature_id: str) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req("chat_history", {"creature_id": creature_id})
        )
        return body.get("history", {})

    async def chat_branches(self, creature_id: str) -> list[dict[str, Any]]:
        body = _maybe_raise(
            await self._req("chat_branches", {"creature_id": creature_id})
        )
        return list(body.get("branches", []))

    async def regenerate(
        self,
        creature_id: str,
        *,
        turn_index: int | None = None,
        branch_view: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req(
                "regenerate",
                {
                    "creature_id": creature_id,
                    "turn_index": turn_index,
                    "branch_view": branch_view,
                },
            )
        )
        return body

    async def edit_message(
        self,
        creature_id: str,
        msg_idx: int,
        content: str | list[dict[str, Any]],
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
    ) -> bool:
        body = _maybe_raise(
            await self._req(
                "edit_message",
                {
                    "creature_id": creature_id,
                    "msg_idx": msg_idx,
                    "content": pack_content(content),
                    "turn_index": turn_index,
                    "user_position": user_position,
                    "branch_view": branch_view,
                },
            )
        )
        return bool(body.get("edited", False))

    async def rewind(self, creature_id: str, msg_idx: int) -> None:
        _maybe_raise(
            await self._req("rewind", {"creature_id": creature_id, "msg_idx": msg_idx})
        )

    # ------------------------------------------------------------------
    # Per-creature state ops
    # ------------------------------------------------------------------

    async def get_scratchpad(self, creature_id: str) -> dict[str, str]:
        body = _maybe_raise(
            await self._req("get_scratchpad", {"creature_id": creature_id})
        )
        return dict(body.get("scratchpad", {}))

    async def patch_scratchpad(
        self,
        creature_id: str,
        updates: dict[str, str | None],
    ) -> dict[str, str]:
        body = _maybe_raise(
            await self._req(
                "patch_scratchpad",
                {"creature_id": creature_id, "updates": updates},
            )
        )
        return dict(body.get("scratchpad", {}))

    async def list_triggers(self, creature_id: str) -> list[dict[str, Any]]:
        body = _maybe_raise(
            await self._req("list_triggers", {"creature_id": creature_id})
        )
        return list(body.get("triggers", []))

    async def get_env(self, creature_id: str) -> dict[str, Any]:
        body = _maybe_raise(await self._req("get_env", {"creature_id": creature_id}))
        return dict(body.get("env", {}))

    async def get_system_prompt(self, creature_id: str) -> dict[str, str]:
        body = _maybe_raise(
            await self._req("get_system_prompt", {"creature_id": creature_id})
        )
        return {"text": str(body.get("text", ""))}

    async def get_working_dir(self, creature_id: str) -> str:
        body = _maybe_raise(
            await self._req("get_working_dir", {"creature_id": creature_id})
        )
        return str(body.get("working_dir", ""))

    async def set_working_dir(self, creature_id: str, new_path: str) -> str:
        body = _maybe_raise(
            await self._req(
                "set_working_dir",
                {"creature_id": creature_id, "new_path": new_path},
            )
        )
        return str(body.get("working_dir", new_path))

    async def native_tool_inventory(self, creature_id: str) -> list[dict[str, Any]]:
        body = _maybe_raise(
            await self._req("native_tool_inventory", {"creature_id": creature_id})
        )
        return list(body.get("inventory", []))

    async def get_native_tool_options(
        self, creature_id: str
    ) -> dict[str, dict[str, Any]]:
        body = _maybe_raise(
            await self._req("get_native_tool_options", {"creature_id": creature_id})
        )
        return dict(body.get("options", {}))

    async def set_native_tool_options(
        self,
        creature_id: str,
        tool: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req(
                "set_native_tool_options",
                {"creature_id": creature_id, "tool": tool, "values": values},
            )
        )
        return dict(body.get("options", {}))

    # ------------------------------------------------------------------
    # Per-creature mutation ops
    # ------------------------------------------------------------------

    async def switch_model(self, creature_id: str, model: str) -> str:
        body = _maybe_raise(
            await self._req(
                "switch_model", {"creature_id": creature_id, "model": model}
            )
        )
        return str(body.get("model", model))

    async def list_plugins(self, creature_id: str) -> list[dict[str, Any]]:
        body = _maybe_raise(
            await self._req("list_plugins", {"creature_id": creature_id})
        )
        return list(body.get("plugins", []))

    async def toggle_plugin(
        self,
        creature_id: str,
        plugin_name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req(
                "toggle_plugin",
                {
                    "creature_id": creature_id,
                    "plugin_name": plugin_name,
                    "enabled": enabled,
                },
            )
        )
        return body

    # ------------------------------------------------------------------
    # Module catalog + slash commands
    # ------------------------------------------------------------------

    async def list_modules(self, creature_id: str) -> list[dict[str, Any]]:
        body = _maybe_raise(
            await self._req("list_modules", {"creature_id": creature_id})
        )
        return list(body.get("modules", []))

    async def get_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req(
                "get_module_options",
                {
                    "creature_id": creature_id,
                    "module_type": module_type,
                    "module_name": module_name,
                },
            )
        )
        return body

    async def set_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req(
                "set_module_options",
                {
                    "creature_id": creature_id,
                    "module_type": module_type,
                    "module_name": module_name,
                    "values": values or {},
                },
            )
        )
        return body

    async def toggle_module(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req(
                "toggle_module",
                {
                    "creature_id": creature_id,
                    "module_type": module_type,
                    "module_name": module_name,
                },
            )
        )
        return body

    async def execute_command(
        self,
        creature_id: str,
        command: str,
        args: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        # Args is conventionally a string; accept dict for forward-compat.
        body = _maybe_raise(
            await self._req(
                "execute_command",
                {
                    "creature_id": creature_id,
                    "command": command,
                    "args": args if args is not None else "",
                },
            )
        )
        return body

    # ------------------------------------------------------------------
    # Per-creature wiring
    # ------------------------------------------------------------------

    async def list_output_wiring(self, creature_id: str) -> list[dict[str, Any]]:
        body = _maybe_raise(
            await self._req("list_output_wiring", {"creature_id": creature_id})
        )
        return list(body.get("edges", []))

    async def wire_output(
        self,
        creature_id: str,
        target: str | dict[str, Any],
    ) -> dict[str, Any]:
        body = _maybe_raise(
            await self._req(
                "wire_output", {"creature_id": creature_id, "target": target}
            )
        )
        return {"edge_id": str(body.get("edge_id", ""))}

    async def unwire_output(self, creature_id: str, edge_id: str) -> bool:
        body = _maybe_raise(
            await self._req(
                "unwire_output", {"creature_id": creature_id, "edge_id": edge_id}
            )
        )
        return bool(body.get("unwired", False))

    async def wire_creature(
        self,
        graph_id: str,
        creature_id: str,
        channel: str,
        direction: str,
        *,
        enabled: bool = True,
    ) -> None:
        _maybe_raise(
            await self._req(
                "wire_creature",
                {
                    "graph_id": graph_id,
                    "creature_id": creature_id,
                    "channel": channel,
                    "direction": direction,
                    "enabled": enabled,
                },
            )
        )

    async def unwire_output_sink(self, creature_id: str, sink_id: str) -> bool:
        body = _maybe_raise(
            await self._req(
                "unwire_output_sink",
                {"creature_id": creature_id, "sink_id": sink_id},
            )
        )
        return bool(body.get("unwired", False))

    # ------------------------------------------------------------------
    # Attach policies + runtime graph
    # ------------------------------------------------------------------

    async def attach_policies(self, creature_id: str) -> list[str]:
        body = _maybe_raise(
            await self._req("attach_policies", {"creature_id": creature_id})
        )
        return list(body.get("policies", []))

    async def session_attach_policies(self, session_id: str) -> list[str]:
        body = _maybe_raise(
            await self._req("session_attach_policies", {"session_id": session_id})
        )
        return list(body.get("policies", []))

    async def runtime_graph_snapshot(self) -> dict[str, Any]:
        body = _maybe_raise(await self._req("runtime_graph_snapshot", {}))
        snap = body.get("snapshot", {"graphs": [], "version": 0})
        # The worker's own snapshot reports ``node_id="_host"`` for each
        # graph because, on its side, IT *is* the host of its engine.
        # Rewrite to OUR ``node_id`` so the host-side aggregator and the
        # graph editor render worker graphs with the correct site chip
        # (user-reported "graphs show as host" bug stems from this
        # missing rewrite — cluster linkage also keys on this id).
        for graph in snap.get("graphs", []):
            graph["node_id"] = self._target_node
            # Stamp each creature's home site so the graph editor can
            # render the per-creature worker chip (BUG #147: worker
            # creatures previously surfaced as ``_host``).
            for creature in graph.get("creatures", []) or []:
                if isinstance(creature, dict):
                    creature["home_node"] = self._target_node
        return snap

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def add_channel(
        self,
        graph_id: str,
        name: str,
        description: str = "",
    ) -> ChannelInfo:
        body = _maybe_raise(
            await self._req(
                "add_channel",
                {"graph_id": graph_id, "name": name, "description": description},
            )
        )
        return unpack_channel_info(body["channel"])

    async def remove_channel(self, graph_id: str, name: str) -> TopologyDelta:
        body = _maybe_raise(
            await self._req("remove_channel", {"graph_id": graph_id, "name": name})
        )
        return unpack_topology_delta(body["delta"])

    async def channel_history(
        self,
        graph_id: str,
        name: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        body = _maybe_raise(
            await self._req(
                "channel_history",
                {"graph_id": graph_id, "name": name, "limit": limit},
            )
        )
        # The worker side already returns JSON-friendly dicts; pass
        # through to keep the wire shape stable.
        messages = body.get("messages") or []
        return [dict(m) for m in messages]

    async def send_channel_message(
        self,
        graph_id: str,
        name: str,
        content: str | list[dict[str, Any]],
        *,
        sender: str = "human",
    ) -> str:
        body = _maybe_raise(
            await self._req(
                "send_channel_message",
                {
                    "graph_id": graph_id,
                    "name": name,
                    "content": pack_content(content),
                    "sender": sender,
                },
            )
        )
        return str(body.get("message_id", ""))

    async def connect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> ConnectionResult:
        body = _maybe_raise(
            await self._req(
                "connect",
                {
                    "sender_id": sender_id,
                    "receiver_id": receiver_id,
                    "channel": channel,
                },
            )
        )
        return unpack_connection_result(body["result"])

    async def disconnect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> DisconnectionResult:
        body = _maybe_raise(
            await self._req(
                "disconnect",
                {
                    "sender_id": sender_id,
                    "receiver_id": receiver_id,
                    "channel": channel,
                },
            )
        )
        return unpack_disconnection_result(body["result"])

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    async def inject_input(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
        *,
        source: str = "chat",
    ) -> None:
        _maybe_raise(
            await self._req(
                "inject_input",
                {
                    "creature_id": creature_id,
                    "message": pack_content(message),
                    "source": source,
                },
            )
        )

    def chat(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        return self._stream_chat(creature_id, message)

    async def _stream_chat(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        async with await RemoteStream.open(
            demux=self._demux,
            sender=self._sender,
            target_node=self._target_node,
            start_namespace="terrarium.events",
            start_type="start_chat",
            body={
                "creature_id": creature_id,
                "message": pack_content(message),
            },
            timeout=self._timeout,
        ) as rs:
            async for frame in rs:
                token = frame.get("token")
                if token is not None:
                    yield token

    def subscribe(
        self,
        filter: EventFilter | None = None,
    ) -> AsyncIterator[EngineEvent]:
        return self._stream_subscribe(filter)

    async def _stream_subscribe(
        self,
        filter: EventFilter | None,
    ) -> AsyncIterator[EngineEvent]:
        async with await RemoteStream.open(
            demux=self._demux,
            sender=self._sender,
            target_node=self._target_node,
            start_namespace="terrarium.events",
            start_type="start_subscribe",
            body={"filter": pack_event_filter(filter)},
            timeout=self._timeout,
        ) as rs:
            async for frame in rs:
                ev = frame.get("event")
                if ev is not None:
                    yield unpack_engine_event(ev)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _req(self, type_: str, body: dict[str, Any]) -> Any:
        return await self._sender.request(
            to_node=self._target_node,
            namespace="terrarium.runtime",
            type=type_,
            body=body,
            timeout=self._timeout,
        )


__all__ = [
    "CreatureNotHostedHere",
    "RemoteEngineError",
    "RemoteTerrariumService",
]
