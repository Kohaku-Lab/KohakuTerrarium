"""Sessions namespace for the :class:`Studio` façade.

Split out of ``studio/studio.py`` (file-size cap).  Every class here is
a thin delegation layer over ``kohakuterrarium.studio.sessions.*`` —
the façade wires them up as ``studio.sessions`` / ``studio.sessions.chat``
etc.  Real signatures (no ``*args/**kwargs`` passthrough) so the
programmatic surface is discoverable and at parity with the HTTP layer.
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
    handles as _session_handles,
    lifecycle as _session_lifecycle,
    memory_search as _session_memory,
    topology as _session_topology,
    wiring as _session_wiring,
)

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

    def find_creature(self, session_id: str, name_or_id: str) -> Any:
        return _session_lifecycle.find_creature(
            self._studio._service, session_id, name_or_id
        )

    async def find_session_for_creature(self, creature_id: str) -> str | None:
        return await _session_lifecycle.find_session_for_creature(
            self._studio._service, creature_id
        )

    # creature CRUD inside a running session (hot-plug)
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

    # topology + wiring
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

    # memory search
    async def search_memory(
        self,
        name: str | Path,
        q: str,
        *,
        mode: str = "auto",
        k: int = 10,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """FTS5 / vector / hybrid search over a saved session.

        ``name`` is either a saved-session name (resolved against the
        session dir, the same lookup the HTTP routes use) or a direct
        ``.kohakutr`` path.  The engine is supplied from this Studio
        instance so a live creature's store is reused when present.
        Raises :class:`SessionNotFoundError` when the session cannot be
        resolved — never creates files as a side effect.
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
        # ``_session_chat.chat`` is an async *generator* — calling it
        # already returns an AsyncIterator. This wrapper must NOT be
        # ``async def`` (that would make the call return a coroutine,
        # breaking the documented ``async for chunk in ...chat(...)``).
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
    ) -> None:
        await _session_chat.regenerate(
            self._studio._service,
            session_id,
            creature_id,
            turn_index=turn_index,
            branch_view=branch_view,
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
    ) -> bool | dict[str, object]:
        return await _session_chat.edit_message(
            self._studio._service,
            session_id,
            creature_id,
            msg_idx,
            content,
            turn_index=turn_index,
            user_position=user_position,
            branch_view=branch_view,
        )

    async def rewind(self, session_id: str, creature_id: str, msg_idx: int) -> None:
        await _session_chat.rewind(
            self._studio._service, session_id, creature_id, msg_idx
        )

    def history(self, session_id: str, creature_id: str) -> dict[str, Any]:
        return _session_chat.history(self._studio._service, session_id, creature_id)

    def branches(self, session_id: str, creature_id: str) -> dict[str, Any]:
        return _session_chat.branches(self._studio._service, session_id, creature_id)


class _SessionsCtl:
    """Per-creature control — interrupt, jobs, cancel, promote."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    async def interrupt(self, session_id: str, creature_id: str) -> None:
        await _session_ctl.interrupt(self._studio._service, session_id, creature_id)

    async def list_jobs(self, session_id: str, creature_id: str) -> list[dict]:
        # ``_session_ctl.list_jobs`` is ``async def`` — must be awaited.
        return await _session_ctl.list_jobs(
            self._studio._service, session_id, creature_id
        )

    async def cancel_job(self, session_id: str, creature_id: str, job_id: str) -> bool:
        return await _session_ctl.cancel_job(
            self._studio._service, session_id, creature_id, job_id
        )

    async def promote_job(self, session_id: str, creature_id: str, job_id: str) -> bool:
        # ``_session_ctl.promote_job`` is ``async def`` — must be awaited.
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
