"""Sessions namespace for the :class:`Studio` façade.

Provides the typed ``studio.sessions`` namespaces while delegating behavior to
``kohakuterrarium.studio.sessions.*``. Explicit signatures keep the programmatic
surface discoverable and aligned with the HTTP adapters without duplicating
session logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from kohakuterrarium.errors import SessionNotFoundError
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence import store as _persistence_store
from kohakuterrarium.studio.sessions import (
    creature_chat as _session_chat,
    creature_command as _session_command,
    creature_ctl as _session_ctl,
    creature_model as _session_model,
    creature_plugins as _session_plugins,
    creature_state as _session_state,
    drives as _session_drives,
    handles as _session_handles,
    lifecycle as _session_lifecycle,
    memory_search as _session_memory,
    topology as _session_topology,
    wiring as _session_wiring,
)
from kohakuterrarium.terrarium.drive.models import ActorRef

if TYPE_CHECKING:
    from kohakuterrarium.studio.studio import Studio


class _SessionsNS:
    """Active engine-backed sessions."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio
        self.chat = _SessionsChat(studio)
        self.ctl = _SessionsCtl(studio)
        self.state = _SessionsState(studio)
        self.plugins = _SessionsPlugins(studio)
        self.model = _SessionsModel(studio)
        self.command = _SessionsCommand(studio)
        self.drives = _SessionsDrives(studio)

    async def start_creature(
        self,
        config_or_path: str | Path,
        *,
        pwd: str | None = None,
        llm: str | None = None,
        name: str | None = None,
        on_node: str = "_host",
    ) -> _session_handles.Session:
        return await _session_lifecycle.start_creature(
            self._studio._service,
            config_path=str(config_or_path),
            pwd=pwd,
            llm=llm,
            name=name,
            on_node=on_node,
        )

    async def start_terrarium(
        self,
        config_or_path: str | Path,
        *,
        pwd: str | None = None,
        llm: str | None = None,
        name: str | None = None,
    ) -> _session_handles.Session:
        return await _session_lifecycle.start_terrarium(
            self._studio._service,
            config_path=str(config_or_path),
            pwd=pwd,
            llm=llm,
            name=name,
        )

    def list(self) -> list[_session_handles.SessionListing]:
        return _session_lifecycle.list_sessions(self._studio._service)

    def get(self, session_id: str) -> _session_handles.Session:
        return _session_lifecycle.get_session(self._studio._service, session_id)

    async def stop(self, session_id: str) -> None:
        await _session_lifecycle.stop_session(self._studio._service, session_id)

    async def end(self, session_id: str) -> None:
        await _session_lifecycle.end_session(self._studio._service, session_id)

    def find_creature(self, session_id: str, name_or_id: str) -> Any:
        return _session_lifecycle.find_creature(
            self._studio._service, session_id, name_or_id
        )

    async def find_session_for_creature(self, creature_id: str) -> str | None:
        return await _session_lifecycle.find_session_for_creature(
            self._studio._service, creature_id
        )

    async def add_creature(self, session_id: str, config: Any) -> str:
        return await _session_lifecycle.add_creature(
            self._studio._service, session_id, config
        )

    def list_creatures(self, session_id: str) -> "list[dict]":
        return _session_lifecycle.list_creatures(self._studio._service, session_id)

    async def remove_creature(self, session_id: str, creature_id: str) -> bool:
        return await _session_lifecycle.remove_creature(
            self._studio._service, session_id, creature_id
        )

    async def add_channel(
        self,
        session_id: str,
        name: str,
        *,
        channel_type: str = "broadcast",
        description: str = "",
    ) -> dict[str, Any]:
        return await _session_topology.add_channel(
            self._studio._service,
            session_id,
            name,
            channel_type=channel_type,
            description=description,
        )

    async def connect(
        self,
        sender: str,
        receiver: str,
        *,
        channel: str | None = None,
        channel_type: str = "broadcast",
    ) -> dict[str, Any]:
        return await _session_topology.connect(
            self._studio._service,
            sender,
            receiver,
            channel=channel,
            channel_type=channel_type,
        )

    async def disconnect(
        self,
        sender: str,
        receiver: str,
        *,
        channel: str | None = None,
    ) -> dict[str, Any]:
        return await _session_topology.disconnect(
            self._studio._service, sender, receiver, channel=channel
        )

    async def wire_output(self, creature_id: str, target: str | dict[str, Any]) -> str:
        return await _session_wiring.wire_output(
            self._studio._service, creature_id, target
        )

    async def unwire_output(self, creature_id: str, edge_id: str) -> bool:
        return await _session_wiring.unwire_output(
            self._studio._service, creature_id, edge_id
        )

    def list_output_wiring(self, creature_id: str) -> list[dict[str, Any]]:
        return _session_wiring.list_output_wiring(self._studio._service, creature_id)

    async def wire_output_sink(self, creature_id: str, sink: Any) -> str:
        return await _session_wiring.wire_output_sink(
            self._studio._service, creature_id, sink
        )

    async def unwire_output_sink(self, creature_id: str, sink_id: str) -> bool:
        return await _session_wiring.unwire_output_sink(
            self._studio._service, creature_id, sink_id
        )

    async def search_memory(
        self,
        name: str | Path,
        q: str,
        *,
        mode: str = "auto",
        k: int = 10,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Search a saved session by name or direct ``.kohakutr`` path.

        Resolution matches the HTTP surface, and a local host engine is supplied
        so an already-open session store can be reused. Failed resolution raises
        :class:`SessionNotFoundError` without creating a database as a side
        effect.
        """
        path = Path(name)
        if not path.exists():
            resolved = _persistence_store.resolve_session_path_default(str(name))
            if resolved is None:
                raise SessionNotFoundError(f"Session not found: {name}")
            path = resolved
        engine = host_engine_or_none(self._studio._service)
        return await _session_memory.search_session_memory(
            path, q=q, mode=mode, k=k, agent=agent, engine=engine
        )


class _SessionsChat:
    """Per-creature chat — chat HTTP fallback, regenerate, edit, rewind."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def chat(
        self, session_id: str, creature_id: str, content: Any
    ) -> AsyncIterator[str]:
        # The delegated callable is an async generator, so this wrapper must
        # return it directly. Declaring the wrapper async would add a coroutine
        # layer and break direct ``async for`` consumption.
        return _session_chat.chat(
            self._studio._service, session_id, creature_id, content
        )

    async def regenerate(
        self,
        session_id: str,
        creature_id: str,
        *,
        turn_index: int | None = None,
        branch_view: dict[int, int] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return await _session_chat.regenerate(
            self._studio._service,
            session_id,
            creature_id,
            turn_index=turn_index,
            branch_view=branch_view,
            request_id=request_id,
        )

    async def edit_message(
        self,
        session_id: str,
        creature_id: str,
        msg_idx: int,
        content: Any,
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return await _session_chat.edit_message(
            self._studio._service,
            session_id,
            creature_id,
            msg_idx,
            content,
            turn_index=turn_index,
            user_position=user_position,
            branch_view=branch_view,
            request_id=request_id,
        )

    async def rewind(self, session_id: str, creature_id: str, msg_idx: int) -> None:
        await _session_chat.rewind(
            self._studio._service, session_id, creature_id, msg_idx
        )

    async def history(self, session_id: str, creature_id: str) -> dict[str, Any]:
        return await _session_chat.history(
            self._studio._service, session_id, creature_id
        )

    async def branches(self, session_id: str, creature_id: str) -> list[dict[str, Any]]:
        return await _session_chat.branches(
            self._studio._service, session_id, creature_id
        )


class _SessionsCtl:
    """Per-creature control — interrupt, jobs, cancel, promote."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    async def interrupt(self, session_id: str, creature_id: str) -> None:
        await _session_ctl.interrupt(self._studio._service, session_id, creature_id)

    async def list_jobs(self, session_id: str, creature_id: str) -> list[dict]:
        return await _session_ctl.list_jobs(
            self._studio._service, session_id, creature_id
        )

    async def cancel_job(self, session_id: str, creature_id: str, job_id: str) -> bool:
        return await _session_ctl.cancel_job(
            self._studio._service, session_id, creature_id, job_id
        )

    async def promote_job(self, session_id: str, creature_id: str, job_id: str) -> bool:
        return await _session_ctl.promote_job(
            self._studio._service, session_id, creature_id, job_id
        )


class _SessionsState:
    """Per-creature state — scratchpad, triggers, env, system prompt, working dir."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def scratchpad(self, session_id: str, creature_id: str) -> dict[str, str]:
        return _session_state.get_scratchpad(
            self._studio._service, session_id, creature_id
        )

    def patch_scratchpad(
        self, session_id: str, creature_id: str, updates: dict[str, str | None]
    ) -> dict[str, str]:
        return _session_state.patch_scratchpad(
            self._studio._service, session_id, creature_id, updates
        )

    def triggers(self, session_id: str, creature_id: str) -> list[dict[str, Any]]:
        return _session_state.list_triggers(
            self._studio._service, session_id, creature_id
        )

    def env(self, session_id: str, creature_id: str) -> dict[str, Any]:
        return _session_state.get_env(self._studio._service, session_id, creature_id)

    def system_prompt(self, session_id: str, creature_id: str) -> dict[str, str]:
        return _session_state.get_system_prompt(
            self._studio._service, session_id, creature_id
        )

    def working_dir(self, session_id: str, creature_id: str) -> str:
        return _session_state.get_working_dir(
            self._studio._service, session_id, creature_id
        )

    def set_working_dir(self, session_id: str, creature_id: str, new_path: str) -> str:
        return _session_state.set_working_dir(
            self._studio._service, session_id, creature_id, new_path
        )


class _SessionsPlugins:
    """Per-creature plugin list / toggle."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def list(self, session_id: str, creature_id: str) -> list[dict]:
        return _session_plugins.list_plugins(
            self._studio._service, session_id, creature_id
        )

    async def toggle(self, session_id: str, creature_id: str, plugin_name: str) -> dict:
        return await _session_plugins.toggle_plugin(
            self._studio._service, session_id, creature_id, plugin_name
        )


class _SessionsModel:
    """Per-creature model + native-tool-options."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def switch(self, session_id: str, creature_id: str, profile_name: str) -> str:
        return _session_model.switch_model(
            self._studio._service, session_id, creature_id, profile_name
        )

    def native_tool_options(self, session_id: str, creature_id: str) -> dict[str, dict]:
        return _session_state.get_native_tool_options(
            self._studio._service, session_id, creature_id
        )


class _SessionsCommand:
    """Per-creature slash command execution."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    async def execute(
        self, session_id: str, creature_id: str, command: str, args: str = ""
    ) -> dict:
        return await _session_command.execute_command(
            self._studio._service, session_id, creature_id, command, args
        )


class _SessionsDrives:
    """Manage graph-scoped Drive records through ``TerrariumService``.

    Session IDs identify graphs at this boundary. Calls default to the trusted
    ``user:local`` actor and operator privileges so the Python surface can create
    and assign Drives. Returned payloads are JSON-safe, and list rows omit
    sensitive specification and evidence details.
    """

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    @staticmethod
    def _actor(actor: ActorRef | str | None) -> ActorRef:
        if isinstance(actor, ActorRef):
            return actor
        if isinstance(actor, str) and actor:
            return ActorRef.parse(actor)
        return ActorRef("user", "local")

    async def list(
        self,
        session_id: str,
        *,
        actor: ActorRef | str | None = None,
        is_privileged: bool = True,
        statuses: Any = None,
        kinds: Any = None,
        owner: Any = None,
        assignee_creature_id: str | None = None,
        mine: bool = False,
        include_terminal: bool = True,
    ) -> list[dict]:
        return await _session_drives.list_records(
            self._studio._service,
            graph_id=session_id,
            actor=self._actor(actor),
            is_privileged=is_privileged,
            statuses=statuses,
            kinds=kinds,
            owner=owner,
            assignee_creature_id=assignee_creature_id,
            mine=mine,
            include_terminal=include_terminal,
        )

    async def get(
        self,
        drive_id: str,
        *,
        actor: ActorRef | str | None = None,
        is_privileged: bool = True,
    ) -> dict | None:
        return await _session_drives.get_record(
            self._studio._service,
            drive_id,
            actor=self._actor(actor),
            is_privileged=is_privileged,
        )

    async def create(
        self,
        session_id: str,
        body: dict,
        *,
        actor: ActorRef | str | None = None,
        is_privileged: bool = True,
        operator: bool = True,
    ) -> dict:
        return await _session_drives.create_record(
            self._studio._service,
            graph_id=session_id,
            actor=self._actor(actor),
            body=body,
            is_privileged=is_privileged,
            operator=operator,
        )

    async def update(
        self,
        drive_id: str,
        body: dict,
        *,
        expected_revision: int,
        actor: ActorRef | str | None = None,
        idempotency_key: str | None = None,
        is_privileged: bool = True,
    ) -> dict:
        return await _session_drives.update_record(
            self._studio._service,
            drive_id,
            expected_revision=expected_revision,
            actor=self._actor(actor),
            body=body,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
        )

    async def assign(
        self,
        drive_id: str,
        assignee_creature_id: str,
        assignee_graph_id: str,
        *,
        expected_revision: int,
        actor: ActorRef | str | None = None,
        is_privileged: bool = True,
        operator: bool = True,
    ) -> dict:
        return await _session_drives.assign_record(
            self._studio._service,
            drive_id,
            assignee_creature_id=assignee_creature_id,
            assignee_graph_id=assignee_graph_id,
            expected_revision=expected_revision,
            actor=self._actor(actor),
            is_privileged=is_privileged,
            operator=operator,
        )

    async def transition(
        self,
        drive_id: str,
        target_status: str,
        *,
        expected_revision: int,
        actor: ActorRef | str | None = None,
        status_reason: str | None = None,
        is_privileged: bool = True,
    ) -> dict:
        return await _session_drives.transition_record(
            self._studio._service,
            drive_id,
            target_status=target_status,
            expected_revision=expected_revision,
            actor=self._actor(actor),
            status_reason=status_reason,
            is_privileged=is_privileged,
        )

    async def report_progress(
        self,
        drive_id: str,
        *,
        summary: str,
        evidence: dict | None = None,
        actor: ActorRef | str | None = None,
        is_privileged: bool = True,
    ) -> dict:
        return await _session_drives.report_progress_record(
            self._studio._service,
            drive_id,
            summary=summary,
            evidence=evidence,
            actor=self._actor(actor),
            is_privileged=is_privileged,
        )

    async def deliveries(
        self,
        drive_id: str,
        *,
        actor: ActorRef | str | None = None,
        is_privileged: bool = True,
    ) -> list[dict]:
        return await _session_drives.list_deliveries_records(
            self._studio._service,
            drive_id,
            actor=self._actor(actor),
            is_privileged=is_privileged,
        )

    async def replay(
        self,
        delivery_id: str,
        *,
        actor: ActorRef | str | None = None,
        is_privileged: bool = True,
    ) -> dict:
        return await _session_drives.replay_delivery_record(
            self._studio._service,
            delivery_id,
            actor=self._actor(actor),
            is_privileged=is_privileged,
        )
