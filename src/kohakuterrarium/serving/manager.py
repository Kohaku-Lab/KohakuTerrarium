"""Unified service manager for agents and terrariums.

All runtime operations go through KohakuManager.
Transport-agnostic - used by any interface (CLI, TUI, Web, Gradio).

Method hierarchy:
  agent_*              Standalone agent lifecycle + interaction
  agent_channel_*      Standalone agent channel ops
  terrarium_*          Terrarium lifecycle
  terrarium_channel_*  Terrarium shared channel ops
  creature_*           Creature lifecycle + hot-plug
  creature_channel_*   Creature private channel ops
"""

import asyncio
import os
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.core.channel import AgentChannel, ChannelMessage
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.packages import is_package_ref, resolve_package_path
from kohakuterrarium.serving.agent_session import AgentSession
from kohakuterrarium.serving.events import ChannelEvent
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.config import (
    CreatureConfig,
    TerrariumConfig,
    load_terrarium_config,
)
from kohakuterrarium.terrarium.observer import ChannelObserver
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _normalize_pwd(pwd: str | None) -> str | None:
    if pwd is None:
        return None
    resolved = str(Path(pwd).expanduser().resolve())
    path = Path(resolved)
    if not path.exists():
        raise ValueError(f"Working directory does not exist: {pwd}")
    if not path.is_dir():
        raise ValueError(f"Working directory is not a directory: {pwd}")
    return resolved


class KohakuManager:
    """Unified service manager for agents and terrariums.

    Method naming convention:
      agent_*              standalone agent ops
      agent_channel_*      standalone agent channel ops
      terrarium_*          terrarium ops
      terrarium_channel_*  terrarium shared channel ops
      creature_*           creature ops (within a terrarium)
      creature_channel_*   creature private channel ops
    """

    def __init__(self, session_dir: str | None = None) -> None:
        self._terrariums: dict[str, TerrariumRuntime] = {}
        self._terrarium_tasks: dict[str, asyncio.Task] = {}
        self._mounted: dict[str, AgentSession] = {}  # mounted creature sessions
        self._agents: dict[str, AgentSession] = {}
        self._observers: dict[str, ChannelObserver] = {}
        self._session_stores: dict[str, Any] = {}
        self._session_dir = session_dir

    # =================================================================
    # Agent Lifecycle
    # =================================================================

    async def agent_create(
        self,
        config_path: str | None = None,
        config: AgentConfig | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ) -> str:
        """Create and start a standalone agent. Returns agent_id."""
        pwd = _normalize_pwd(pwd)
        if config_path:
            if is_package_ref(config_path):
                config_path = str(resolve_package_path(config_path))
            session = await AgentSession.from_path(
                config_path, llm_override=llm_override, pwd=pwd
            )
        elif config:
            session = await AgentSession.from_config(config)
        else:
            raise ValueError("Must provide config_path or config")
        self._agents[session.agent_id] = session

        # Auto-attach session store for persistence
        if self._session_dir:
            try:
                session_path = Path(self._session_dir) / f"{session.agent_id}.kohakutr"
                store = SessionStore(session_path)
                store.init_meta(
                    session_id=session.agent_id,
                    config_type="agent",
                    config_path=config_path or "",
                    pwd=pwd or os.getcwd(),
                    agents=[session.agent.config.name],
                )
                session.agent.attach_session_store(store)
                self._session_stores[session.agent_id] = store
            except Exception as e:
                logger.warning("Session store creation failed", error=str(e))

        logger.info("Agent created", agent_id=session.agent_id)
        return session.agent_id

    async def register_agent(self, agent: Any, store: Any = None) -> str:
        """Register a pre-built agent (e.g. from resume). Returns agent_id."""
        session = await AgentSession.from_agent(agent)
        self._agents[session.agent_id] = session
        if store:
            self._session_stores[session.agent_id] = store
        logger.info("Agent registered", agent_id=session.agent_id)
        return session.agent_id

    async def agent_stop(self, agent_id: str) -> None:
        """Stop and cleanup an agent."""
        session = self._agents.pop(agent_id, None)
        if session:
            await session.stop()

    async def agent_chat(
        self, agent_id: str, message: str | list[dict]
    ) -> AsyncIterator[str]:
        """Send a message and stream the response."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        async for chunk in session.chat(message):
            yield chunk

    def agent_status(self, agent_id: str) -> dict:
        """Get agent status (running, tools, subagents)."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        return session.get_status()

    def agent_list(self) -> list[dict]:
        """List all running agents."""
        return [s.get_status() for s in self._agents.values()]

    def agent_interrupt(self, agent_id: str) -> None:
        """Interrupt the agent's current turn."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        session.agent.interrupt()

    def agent_get_jobs(self, agent_id: str) -> list[dict]:
        """Get running/recent jobs for an agent (tools + sub-agents)."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        jobs = [j.to_dict() for j in session.agent.executor.get_running_jobs()]
        jobs.extend(
            j.to_dict() for j in session.agent.subagent_manager.get_running_jobs()
        )
        return jobs

    async def agent_cancel_job(self, agent_id: str, job_id: str) -> bool:
        """Cancel a running job (tool or sub-agent). Returns True if cancelled."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        if session.agent._interrupt_direct_job(job_id):
            return True
        # Try executor (tools) first, then sub-agent manager
        if await session.agent.executor.cancel(job_id):
            return True
        return await session.agent.subagent_manager.cancel(job_id)

    def agent_switch_model(self, agent_id: str, profile_name: str) -> str:
        """Switch an agent's LLM model. Returns the new model name."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        return session.agent.switch_model(profile_name)

    def agent_native_tool_options(self, agent_id: str) -> dict[str, dict]:
        """Return the agent's session-wise provider-native tool overrides."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        helper = getattr(session.agent, "native_tool_options", None)
        if helper is None:
            return {}
        return helper.list()

    def agent_set_native_tool_options(
        self, agent_id: str, tool_name: str, values: dict[str, Any]
    ) -> dict:
        """Replace the override dict for one provider-native tool.

        See :class:`~kohakuterrarium.core.agent_native_tools.NativeToolOptions`.
        """
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        helper = getattr(session.agent, "native_tool_options", None)
        if helper is None:
            raise ValueError(f"Agent {agent_id!r} has no native_tool_options helper")
        return helper.set(tool_name, values or {})

    def agent_get_working_dir(self, agent_id: str) -> str:
        """Return the agent's current working directory."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        ws = getattr(session.agent, "workspace", None)
        if ws is None:
            return str(getattr(session.agent.executor, "_working_dir", ""))
        return ws.get()

    def agent_set_working_dir(self, agent_id: str, new_path: str) -> str:
        """Switch the agent's tool-side working directory mid-session.

        Delegates to ``agent.workspace.set`` (see
        :mod:`kohakuterrarium.core.agent_workspace`). Raises ``ValueError``
        for missing agents / bad paths and ``RuntimeError`` if the agent
        is currently processing.
        """
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        ws = getattr(session.agent, "workspace", None)
        if ws is None:
            raise RuntimeError(f"Agent {agent_id!r} has no workspace helper")
        return ws.set(new_path)

    def agent_native_tool_inventory(self, agent_id: str) -> list[dict]:
        """List provider-native tools registered on this agent + their schemas."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        registry = session.agent.registry
        helper = getattr(session.agent, "native_tool_options", None)
        out: list[dict] = []
        for name in registry.list_tools():
            tool = registry.get_tool(name)
            if tool is None or not getattr(tool, "is_provider_native", False):
                continue
            schema_fn = getattr(type(tool), "provider_native_option_schema", None)
            try:
                schema = schema_fn() if callable(schema_fn) else {}
            except Exception:
                schema = {}
            values = helper.get(name) if helper else {}
            out.append(
                {
                    "name": name,
                    "description": getattr(tool, "description", "") or "",
                    "option_schema": schema,
                    "values": values,
                }
            )
        out.sort(key=lambda entry: entry["name"])
        return out

    async def agent_execute_command(
        self, agent_id: str, command: str, args: str = ""
    ) -> dict:
        """Execute a user slash command on an agent. Returns result dict."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")

        cmd = get_builtin_user_command(command)
        if cmd is None:
            raise ValueError(f"Unknown command: /{command}")

        context = UserCommandContext(
            agent=session.agent,
            session=getattr(session.agent, "session", None),
        )
        result = await cmd.execute(args, context)
        resp: dict = {
            "command": command,
            "output": result.output,
            "error": result.error,
            "success": result.success,
        }
        if result.data is not None:
            resp["data"] = result.data
        return resp

    def agent_get_history(self, agent_id: str) -> list[dict]:
        """Get conversation history for an agent."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        store = self._session_stores.get(agent_id)
        if store:
            return store.get_resumable_events(session.agent.config.name)
        return session.agent.conversation_history

    # =================================================================
    # Agent Channel Ops
    # =================================================================

    def agent_channel_list(self, agent_id: str) -> list[dict[str, str]]:
        """List channels for a standalone agent."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        return session.agent.session.channels.get_channel_info()

    def agent_channel_info(self, agent_id: str, channel: str) -> dict | None:
        """Get info about a specific agent channel."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        ch = session.agent.session.channels.get(channel)
        if ch is None:
            return None
        return {
            "name": ch.name,
            "type": ch.channel_type,
            "description": ch.description,
            "qsize": ch.qsize,
        }

    async def agent_channel_send(
        self,
        agent_id: str,
        channel: str,
        content: str | list[dict],
        sender: str = "human",
    ) -> str:
        """Send a message to a standalone agent's channel. Returns message_id."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        ch = session.agent.session.channels.get_or_create(channel)
        msg = ChannelMessage(sender=sender, content=content)
        await ch.send(msg)
        return msg.message_id

    async def agent_channel_stream(
        self, agent_id: str, channels: list[str] | None = None
    ) -> AsyncIterator[ChannelEvent]:
        """Stream channel events for a standalone agent."""
        session = self._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent not found: {agent_id}")
        async for event in self._stream_from_registry(
            session.agent.session.channels,
            source_id=agent_id,
            source_type="agent",
            filter_channels=channels,
            running_check=lambda: session.agent.is_running,
        ):
            yield event

    # =================================================================
    # Terrarium Lifecycle
    # =================================================================

    async def terrarium_create(
        self,
        config_path: str | None = None,
        config: TerrariumConfig | None = None,
        pwd: str | None = None,
    ) -> str:
        """Create and start a terrarium. Returns terrarium_id."""
        pwd = _normalize_pwd(pwd)
        if config_path:
            if is_package_ref(config_path):
                config_path = str(resolve_package_path(config_path))
            cfg = load_terrarium_config(config_path)
        elif config:
            cfg = config
        else:
            raise ValueError("Must provide config_path or config")

        terrarium_id = f"terrarium_{uuid4().hex}"
        env = Environment(env_id=f"terrarium_{cfg.name}_{uuid4().hex[:8]}")
        runtime = TerrariumRuntime(cfg, environment=env, pwd=pwd)
        self._terrariums[terrarium_id] = runtime

        # Prepare session store before run (auto-attached after start inside run)
        if self._session_dir:
            try:
                session_path = Path(self._session_dir) / f"{terrarium_id}.kohakutr"
                store = SessionStore(session_path)
                store.init_meta(
                    session_id=terrarium_id,
                    config_type="terrarium",
                    config_path=config_path or "",
                    pwd=pwd or os.getcwd(),
                    agents=[c.name for c in cfg.creatures]
                    + (["root"] if cfg.root else []),
                    terrarium_name=cfg.name,
                    terrarium_channels=[
                        {
                            "name": ch.name,
                            "type": ch.channel_type,
                            "description": ch.description,
                        }
                        for ch in cfg.channels
                    ],
                    terrarium_creatures=[
                        {
                            "name": c.name,
                            "listen": c.listen_channels,
                            "send": c.send_channels,
                        }
                        for c in cfg.creatures
                    ],
                )
                runtime._pending_session_store = store
                self._session_stores[terrarium_id] = store
                logger.info("Session store prepared", terrarium_id=terrarium_id)
            except Exception as e:
                logger.warning(
                    "Failed to create session store",
                    terrarium_id=terrarium_id,
                    error=str(e),
                )

        task = asyncio.create_task(runtime.run())
        self._terrarium_tasks[terrarium_id] = task
        await asyncio.sleep(0.5)

        logger.info("Terrarium created", terrarium_id=terrarium_id)
        return terrarium_id

    async def register_terrarium(
        self, runtime: TerrariumRuntime, store: Any = None
    ) -> str:
        """Register a pre-built terrarium (e.g. from resume). Returns terrarium_id."""
        terrarium_id = f"terrarium_{uuid4().hex}"
        self._terrariums[terrarium_id] = runtime
        if store:
            self._session_stores[terrarium_id] = store

        task = asyncio.create_task(runtime.run())
        self._terrarium_tasks[terrarium_id] = task
        await asyncio.sleep(0.5)

        logger.info("Terrarium registered (resumed)", terrarium_id=terrarium_id)
        return terrarium_id

    async def terrarium_stop(self, terrarium_id: str) -> None:
        """Stop all creatures and cleanup."""
        observer = self._observers.pop(terrarium_id, None)
        if observer:
            await observer.stop()
        # Cleanup any mounted sessions for this terrarium
        to_remove = [k for k in self._mounted if k.startswith(f"{terrarium_id}:")]
        for k in to_remove:
            self._mounted.pop(k, None)
        runtime = self._terrariums.pop(terrarium_id, None)
        if runtime:
            await runtime.stop()
        task = self._terrarium_tasks.pop(terrarium_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def terrarium_mount(self, terrarium_id: str, target: str) -> AgentSession:
        """Mount onto a creature (or root) in a running terrarium.

        Creates an AgentSession wrapping the target's Agent. This lets
        the API inject input and capture output without modifying the
        terrarium runtime.

        Args:
            terrarium_id: The terrarium ID.
            target: "root" for the root agent, or a creature name.

        Returns:
            AgentSession wrapping the target agent.
        """
        mount_key = f"{terrarium_id}:{target}"
        if mount_key in self._mounted:
            return self._mounted[mount_key]

        runtime = self._get_runtime(terrarium_id)

        if target == "root":
            agent = runtime.root_agent
            if agent is None:
                raise ValueError(f"Terrarium {terrarium_id} has no root agent")
        else:
            agent = runtime.get_creature_agent(target)
            if agent is None:
                raise ValueError(f"Creature not found: {target}")

        session = AgentSession(agent, agent_id=mount_key)
        self._mounted[mount_key] = session
        return session

    async def terrarium_chat(
        self, terrarium_id: str, target: str, message: str | list[dict]
    ) -> AsyncIterator[str]:
        """Chat with any creature (or root) in a terrarium.

        Mounts onto the target on first call, then injects input
        and streams response chunks.

        Args:
            terrarium_id: The terrarium ID.
            target: "root" for root agent, or creature name.
            message: The message to send.
        """
        session = self.terrarium_mount(terrarium_id, target)
        async for chunk in session.chat(message):
            yield chunk

    def terrarium_status(self, terrarium_id: str) -> dict:
        """Get terrarium status (creatures, channels, running state)."""
        runtime = self._get_runtime(terrarium_id)
        status = runtime.get_status()
        status["terrarium_id"] = terrarium_id
        return status

    def terrarium_list(self) -> list[dict]:
        """List all running terrariums."""
        return [
            {**rt.get_status(), "terrarium_id": tid}
            for tid, rt in self._terrariums.items()
        ]

    # =================================================================
    # Terrarium Channel Ops (shared channels)
    # =================================================================

    def terrarium_channel_list(self, terrarium_id: str) -> list[dict[str, str]]:
        """List shared (inter-creature) channels."""
        runtime = self._get_runtime(terrarium_id)
        return runtime.environment.shared_channels.get_channel_info()

    def terrarium_channel_info(self, terrarium_id: str, channel: str) -> dict | None:
        """Get info about a shared channel."""
        runtime = self._get_runtime(terrarium_id)
        ch = runtime.environment.shared_channels.get(channel)
        if ch is None:
            return None
        return {
            "name": ch.name,
            "type": ch.channel_type,
            "description": ch.description,
            "qsize": ch.qsize,
            "scope": "shared",
        }

    async def terrarium_channel_send(
        self,
        terrarium_id: str,
        channel: str,
        content: str | list[dict],
        sender: str = "human",
    ) -> str:
        """Send a message to a shared terrarium channel. Returns message_id."""
        runtime = self._get_runtime(terrarium_id)
        ch = runtime.environment.shared_channels.get(channel)
        if ch is None:
            available = runtime.environment.shared_channels.list_channels()
            raise ValueError(f"Channel '{channel}' not found. Available: {available}")
        msg = ChannelMessage(sender=sender, content=content)
        await ch.send(msg)
        return msg.message_id

    async def terrarium_channel_add(
        self,
        terrarium_id: str,
        name: str,
        channel_type: str = "queue",
        description: str = "",
    ) -> None:
        """Add a shared channel to a running terrarium (hot-plug)."""
        runtime = self._get_runtime(terrarium_id)
        await runtime.add_channel(name, channel_type, description)

    async def terrarium_channel_stream(
        self, terrarium_id: str, channels: list[str] | None = None
    ) -> AsyncIterator[ChannelEvent]:
        """Stream shared channel events from a terrarium."""
        runtime = self._get_runtime(terrarium_id)
        async for event in self._stream_from_registry(
            runtime.environment.shared_channels,
            source_id=terrarium_id,
            source_type="terrarium",
            filter_channels=channels,
            running_check=lambda: runtime.is_running,
        ):
            yield event

    # =================================================================
    # Creature Ops (within a terrarium)
    # =================================================================

    def creature_list(self, terrarium_id: str) -> list[dict]:
        """List creatures in a terrarium."""
        runtime = self._get_runtime(terrarium_id)
        status = runtime.get_status()
        return [
            {"name": name, **info} for name, info in status.get("creatures", {}).items()
        ]

    async def creature_add(self, terrarium_id: str, config: CreatureConfig) -> str:
        """Add a creature to a running terrarium (hot-plug). Returns name."""
        runtime = self._get_runtime(terrarium_id)
        handle = await runtime.add_creature(config)
        return handle.name

    async def creature_remove(self, terrarium_id: str, name: str) -> bool:
        """Remove a creature from a running terrarium."""
        runtime = self._get_runtime(terrarium_id)
        return await runtime.remove_creature(name)

    async def creature_wire(
        self, terrarium_id: str, creature: str, channel: str, direction: str
    ) -> None:
        """Wire a creature to a channel (listen or send)."""
        runtime = self._get_runtime(terrarium_id)
        await runtime.wire_channel(creature, channel, direction)

    def creature_switch_model(
        self, terrarium_id: str, name: str, profile_name: str
    ) -> str:
        """Switch a creature's LLM model. Returns the new model name."""
        runtime = self._get_runtime(terrarium_id)
        agent = (
            runtime.root_agent if name == "root" else runtime.get_creature_agent(name)
        )
        if agent is None:
            raise ValueError(f"Creature not found: {name}")
        return agent.switch_model(profile_name)

    async def creature_execute_command(
        self, terrarium_id: str, name: str, command: str, args: str = ""
    ) -> dict:
        """Execute a user slash command targeting a specific creature.

        ``name`` may be either a creature name or the literal string
        ``"root"``. This is the terrarium-scoped equivalent of
        :meth:`agent_execute_command` and is the correct path for
        things like ``/compact`` / ``/clear`` / ``/status`` when the
        user is focused on a particular creature tab in the web UI
        or the TUI. Using the wrong agent (always routing to root)
        was the root cause of "/compact does nothing in terrarium".
        """
        runtime = self._get_runtime(terrarium_id)
        agent = (
            runtime.root_agent if name == "root" else runtime.get_creature_agent(name)
        )
        if agent is None:
            raise ValueError(f"Creature not found: {name}")

        cmd = get_builtin_user_command(command)
        if cmd is None:
            raise ValueError(f"Unknown command: /{command}")

        context = UserCommandContext(
            agent=agent,
            session=getattr(agent, "session", None),
        )
        result = await cmd.execute(args, context)
        resp: dict = {
            "command": command,
            "output": result.output,
            "error": result.error,
            "success": result.success,
        }
        if result.data is not None:
            resp["data"] = result.data
        return resp

    async def creature_interrupt(self, terrarium_id: str, name: str) -> None:
        """Interrupt a creature's current turn."""
        runtime = self._get_runtime(terrarium_id)
        agent = (
            runtime.root_agent if name == "root" else runtime.get_creature_agent(name)
        )
        if agent is None:
            raise ValueError(f"Creature not found: {name}")
        agent.interrupt()

    def creature_get_jobs(self, terrarium_id: str, name: str) -> list[dict]:
        """Get running jobs for a creature (tools + sub-agents)."""
        runtime = self._get_runtime(terrarium_id)
        agent = (
            runtime.root_agent if name == "root" else runtime.get_creature_agent(name)
        )
        if agent is None:
            raise ValueError(f"Creature not found: {name}")
        jobs = [j.to_dict() for j in agent.executor.get_running_jobs()]
        jobs.extend(j.to_dict() for j in agent.subagent_manager.get_running_jobs())
        return jobs

    async def creature_cancel_job(
        self, terrarium_id: str, name: str, job_id: str
    ) -> bool:
        """Cancel a creature's running job (tool or sub-agent)."""
        runtime = self._get_runtime(terrarium_id)
        agent = (
            runtime.root_agent if name == "root" else runtime.get_creature_agent(name)
        )
        if agent is None:
            raise ValueError(f"Creature not found: {name}")
        if agent._interrupt_direct_job(job_id):
            return True
        if await agent.executor.cancel(job_id):
            return True
        return await agent.subagent_manager.cancel(job_id)

    # =================================================================
    # Creature Channel Ops (private/sub-agent channels)
    # =================================================================

    def creature_channel_list(
        self, terrarium_id: str, creature: str
    ) -> list[dict[str, str]]:
        """List a creature's private (sub-agent) channels."""
        runtime = self._get_runtime(terrarium_id)
        session = runtime.environment.get_session(creature)
        return session.channels.get_channel_info()

    def creature_channel_info(
        self, terrarium_id: str, creature: str, channel: str
    ) -> dict | None:
        """Get info about a creature's private channel."""
        runtime = self._get_runtime(terrarium_id)
        session = runtime.environment.get_session(creature)
        ch = session.channels.get(channel)
        if ch is None:
            return None
        return {
            "name": ch.name,
            "type": ch.channel_type,
            "description": ch.description,
            "qsize": ch.qsize,
            "scope": "private",
            "creature": creature,
        }

    async def creature_channel_send(
        self,
        terrarium_id: str,
        creature: str,
        channel: str,
        content: str | list[dict],
        sender: str = "human",
    ) -> str:
        """Send a message to a creature's private channel. Returns message_id."""
        runtime = self._get_runtime(terrarium_id)
        session = runtime.environment.get_session(creature)
        ch = session.channels.get(channel)
        if ch is None:
            ch = session.channels.get_or_create(channel)
        msg = ChannelMessage(sender=sender, content=content)
        await ch.send(msg)
        return msg.message_id

    # =================================================================
    # Shared Helpers
    # =================================================================

    def _get_runtime(self, terrarium_id: str) -> TerrariumRuntime:
        """Get runtime, raising ValueError if not found."""
        runtime = self._terrariums.get(terrarium_id)
        if not runtime:
            raise ValueError(f"Terrarium not found: {terrarium_id}")
        return runtime

    async def _stream_from_registry(
        self,
        registry: Any,
        source_id: str,
        source_type: str,
        filter_channels: list[str] | None = None,
        running_check: Any = None,
    ) -> AsyncIterator[ChannelEvent]:
        """Stream channel events from any ChannelRegistry.

        Works for both shared (terrarium) and private (agent) channels.
        """
        observer = ChannelObserver(None)
        # Manually set the registry instead of session
        observer._session = None

        event_queue: asyncio.Queue[ChannelEvent] = asyncio.Queue()

        def on_message(msg: Any) -> None:
            event_queue.put_nowait(
                ChannelEvent(
                    terrarium_id=source_id,
                    channel=msg.channel,
                    sender=msg.sender,
                    content=msg.content,
                    message_id=msg.message_id,
                    timestamp=msg.timestamp,
                )
            )

        observer.on_message(on_message)

        all_channels = registry.list_channels()
        observe_channels = filter_channels or all_channels
        for ch_name in observe_channels:
            ch = registry.get(ch_name)
            if ch is not None:
                if isinstance(ch, AgentChannel):
                    sub = ch.subscribe(f"_stream_{source_id}_{ch_name}")
                    observer._subscriptions[ch_name] = sub
                    task = asyncio.create_task(observer._observe_loop(ch_name, sub))
                    observer._observe_tasks.append(task)

        try:
            while running_check is None or running_check():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    continue
        finally:
            await observer.stop()

    # =================================================================
    # Lifecycle
    # =================================================================

    async def shutdown(self) -> None:
        """Stop everything."""
        for agent_id in list(self._agents.keys()):
            await self.agent_stop(agent_id)
        for tid in list(self._terrariums.keys()):
            await self.terrarium_stop(tid)
