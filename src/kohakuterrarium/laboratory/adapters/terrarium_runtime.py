"""Expose a Terrarium engine through the ``terrarium.runtime`` namespace."""

from pathlib import Path
from typing import Any

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory._internal.protocol import HOST_NODE_ID
from kohakuterrarium.laboratory.adapters.terrarium_runtime_drive import (
    handle_drive_request,
    is_drive_verb,
)
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.llm.backends import set_remote_backend
from kohakuterrarium.llm.preset_store import preset_from_data, set_remote_preset
from kohakuterrarium.llm.profile_types import LLMBackend
from kohakuterrarium.terrarium.creature_ops import (
    agent_command_inventory,
    agent_env,
    agent_execute_command,
    agent_get_module_options,
    agent_get_native_tool_options,
    agent_list_modules,
    agent_invoke_skill,
    agent_list_plugins,
    agent_native_tool_inventory,
    agent_patch_scratchpad,
    agent_scratchpad,
    agent_set_module_options,
    agent_set_native_tool_options,
    agent_set_working_dir,
    agent_system_prompt,
    agent_toggle_module,
    agent_toggle_plugin,
    agent_triggers,
    agent_working_dir,
    attach_policies_for,
    build_runtime_graph_snapshot_for,
    chat_branches_for,
    chat_history_for,
    session_attach_policies_for,
    wire_creature_on_engine,
)
from kohakuterrarium.session.raw_history import UserMessageSelector
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import (
    LocalTerrariumService,
    _completed_branch_result,
    _normalize_command_args,
    creature_to_info,
)
from kohakuterrarium.terrarium.wire import (
    pack_channel_info,
    pack_connection_result,
    pack_creature_info,
    pack_disconnection_result,
    pack_graph_topology,
    pack_topology_delta,
    unpack_content,
    unpack_creature_build_input,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _channel_message_to_dict(m: Any) -> dict[str, Any]:
    """Serialize a channel message for the history wire response."""
    ts = getattr(m, "timestamp", None)
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
    return {
        "message_id": getattr(m, "message_id", ""),
        "sender": getattr(m, "sender", ""),
        "sender_id": getattr(m, "sender_id", None),
        "content": getattr(m, "content", ""),
        "channel": getattr(m, "channel", None),
        "timestamp": ts_str,
    }


class _NotHostedHere(KeyError):
    """Signal that routing selected a worker which does not host the creature.

    Remaining a :class:`KeyError` subtype preserves generic error handling while
    allowing the controller to retry stale home-node routing safely.
    """


class TerrariumRuntimeAdapter:
    """Bind a local Terrarium engine to laboratory runtime requests."""

    NAMESPACE = "terrarium.runtime"

    def __init__(
        self,
        engine: Terrarium,
        lab_node: LabRegistrar,
        *,
        node_id: str | None = None,
        session_attacher: "Any" = None,
        identity_cache: "Any" = None,
    ) -> None:
        self._engine = engine
        self._node = lab_node
        if node_id is not None:
            self._node_id = node_id
        else:
            self._node_id = getattr(lab_node, "client_id", None) or "_host"
        self._session_attacher = session_attacher
        # Workers prewarm remote identity before constructing LLM providers.
        self._identity_cache = identity_cache
        # Defer Drive service construction until a Drive verb is received.
        self._drive_service: LocalTerrariumService | None = None
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info(
            "lab adapter registered",
            namespace=self.NAMESPACE,
            node_id=self._node_id,
            has_session_attacher=session_attacher is not None,
            has_identity_cache=identity_cache is not None,
        )

    @property
    def node_id(self) -> str:
        return self._node_id

    def detach(self) -> None:
        """Unregister the APP extension. Safe to call once."""
        self._node.unregister_app_extension(self.NAMESPACE)
        logger.info(
            "lab adapter detached",
            namespace=self.NAMESPACE,
            node_id=self._node_id,
        )

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except _NotHostedHere as e:
            # Catch the routing sentinel before its KeyError base class.
            return {"error": {"kind": "creature_not_hosted", "message": str(e)}}
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("terrarium.runtime handler failed: %s", msg.type)
            return {"error": {"kind": "engine", "message": str(e)}}

    def _require_hosted(self, creature_id: str):
        """Look up the creature on this engine or raise :class:`_NotHostedHere`."""
        try:
            return self._engine.get_creature(creature_id)
        except KeyError:
            raise _NotHostedHere(creature_id) from None

    async def _prewarm_identity(self, config: Any) -> None:
        """Best-effort preload remote profiles and credentials for LLM creation."""
        cache = self._identity_cache
        if cache is None:
            return
        # Path inputs must be loaded before profile and provider fields are visible.
        if isinstance(config, (str, Path)):
            try:
                config = load_agent_config(config)
            except Exception:  # pragma: no cover - best-effort
                return
        # Identity lookup accepts bare profile names rather than provider/name selectors.
        profile_name = getattr(config, "llm_profile", "") or ""
        if profile_name:
            bare_name = (
                profile_name.split("/", 1)[1] if "/" in profile_name else profile_name
            )
            try:
                profile = await cache.get_profile(bare_name)
            except Exception:  # pragma: no cover - best-effort
                profile = None
            if isinstance(profile, dict):
                self._stash_remote_preset(profile_name, profile)
                prov = profile.get("provider") or ""
                if prov:
                    await cache.prefetch_for_provider(prov)
                # Codex providers resolve OAuth tokens outside the API-key cache.
                if (profile.get("backend_type") or "") == "codex":
                    await cache.prefetch_for_codex_if_needed()
        # Model prefixes supply the provider when inline configs omit it.
        prov = getattr(config, "provider", "") or ""
        if not prov:
            model = getattr(config, "model", "") or ""
            if "/" in model:
                prov = model.split("/", 1)[0]
        if prov:
            await cache.prefetch_for_provider(prov)

    async def _prewarm_profile_by_selector(self, selector: str) -> None:
        """Preload a selected host profile before switching a worker model."""
        cache = self._identity_cache
        if cache is None or not selector:
            return
        bare = selector.split("/", 1)[1] if "/" in selector else selector
        try:
            profile = await cache.get_profile(bare)
        except Exception:  # pragma: no cover - best-effort
            return
        if not isinstance(profile, dict):
            return
        self._stash_remote_preset(selector, profile)
        prov = profile.get("provider") or ""
        if prov:
            await cache.prefetch_for_provider(prov)
        if (profile.get("backend_type") or "") == "codex":
            await cache.prefetch_for_codex_if_needed()

    @staticmethod
    def _stash_remote_preset(profile_name: str, profile: dict) -> None:
        """Register a host-fetched backend and preset for synchronous resolution."""
        if "/" in profile_name:
            prov_part, name_part = profile_name.split("/", 1)
        else:
            prov_part = ""
            name_part = profile_name
        provider = prov_part or profile.get("provider") or ""
        if not provider:
            return
        backend_type = profile.get("backend_type") or provider
        set_remote_backend(
            LLMBackend(
                name=provider,
                backend_type=backend_type,
                base_url=profile.get("base_url", "") or "",
                api_key_env=profile.get("api_key_env", "") or "",
            )
        )
        try:
            preset = preset_from_data(name_part, profile, provider)
        except Exception:  # pragma: no cover - defensive
            return
        set_remote_preset(provider, name_part, preset)

    def _local_service(self) -> LocalTerrariumService:
        """Return the adapter's lazily constructed local Drive service."""
        if self._drive_service is None:
            self._drive_service = LocalTerrariumService(
                self._engine, node_id=self._node_id
            )
        return self._drive_service

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        # Drive dispatch preserves typed errors before generic mapping applies.
        if is_drive_verb(msg.type):
            return await handle_drive_request(self._local_service(), msg)
        match msg.type:
            case "node_id":
                # Client IDs may be assigned after adapter construction.
                live = getattr(self._node, "client_id", None) or self._node_id
                return {"node_id": live}

            case "list_creatures":
                return {
                    "creatures": [
                        pack_creature_info(creature_to_info(c))
                        for c in self._engine.list_creatures()
                    ]
                }

            case "get_creature_info":
                # Fan-out reads use null to mean "not on this node" and continue elsewhere.
                cid = msg.body["creature_id"]
                try:
                    creature = self._engine.get_creature(cid)
                except KeyError:
                    return {"creature_info": None}
                return {"creature_info": pack_creature_info(creature_to_info(creature))}

            case "list_graphs":
                return {
                    "graphs": [
                        pack_graph_topology(g) for g in self._engine.list_graphs()
                    ]
                }

            case "get_graph":
                try:
                    g = self._engine.get_graph(msg.body["graph_id"])
                except KeyError:
                    return {"graph": None}
                return {"graph": pack_graph_topology(g)}

            case "list_channels":
                try:
                    g = self._engine.get_graph(msg.body["graph_id"])
                except KeyError:
                    return {"channels": []}
                return {
                    "channels": [
                        pack_channel_info(info) for info in g.channels.values()
                    ]
                }

            case "creature_status":
                try:
                    status = self._engine.status(msg.body["creature_id"])
                except KeyError:
                    return {"status": None}
                return {"status": status}

            case "status_snapshot":
                return {"status": self._engine.status()}

            case "add_creature":
                config = unpack_creature_build_input(msg.body["config"])
                # LLM construction needs remote profiles and credentials already cached.
                if self._identity_cache is not None:
                    await self._prewarm_identity(config)
                creature = await self._engine.add_creature(
                    config,
                    graph=msg.body.get("graph_id"),
                    creature_id=msg.body.get("creature_id"),
                    llm=msg.body.get("llm"),
                    pwd=msg.body.get("pwd"),
                    start=msg.body.get("start", True),
                    is_privileged=msg.body.get("is_privileged", False),
                    parent_creature_id=msg.body.get("parent_creature_id"),
                    # Studio display names override the packaged config name.
                    name=msg.body.get("name"),
                    # Remote attach owns input; configured CLI input would steal worker stdin.
                    io="none",
                    # Studio can repair model and configuration failures after spawn.
                    strict=False,
                    # WorkerSessionAttacher is the sole persistence owner for remote creatures.
                    session=False,
                )
                # The tee keeps worker persistence and the controller mirror consistent.
                if self._session_attacher is not None:
                    self._session_attacher.attach(creature.creature_id)
                return {"creature_info": pack_creature_info(creature_to_info(creature))}

            case "remove_creature":
                cid = msg.body["creature_id"]
                # Validate routing before mutation so inner failures cannot trigger a retry.
                self._require_hosted(cid)
                # Detach event forwarding before removal while retaining resumable storage.
                if self._session_attacher is not None:
                    self._session_attacher.detach(cid)
                await self._engine.remove_creature(cid)
                return {}

            case "start_creature":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                await self._engine.start(cid)
                return {}

            case "stop_creature":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                await self._engine.stop(cid)
                return {}

            case "shutdown":
                await self._engine.shutdown()
                return {}

            case "add_channel":
                info = await self._engine.add_channel(
                    msg.body["graph_id"],
                    msg.body["name"],
                    msg.body.get("description", ""),
                )
                return {"channel": pack_channel_info(info)}

            case "remove_channel":
                delta = await self._engine.remove_channel(
                    msg.body["graph_id"], msg.body["name"]
                )
                return {"delta": pack_topology_delta(delta)}

            case "channel_history":
                graph_id = msg.body["graph_id"]
                name = msg.body["name"]
                limit = msg.body.get("limit")
                env = self._engine._environments.get(graph_id)
                if env is None:
                    raise KeyError(f"graph {graph_id!r} not found")
                ch = env.shared_channels.get(name)
                if ch is None:
                    raise KeyError(f"channel {name!r} not in graph {graph_id!r}")
                messages = list(getattr(ch, "history", []) or [])
                if isinstance(limit, int) and limit >= 0:
                    messages = messages[-limit:]
                return {"messages": [_channel_message_to_dict(m) for m in messages]}

            case "send_channel_message":
                graph_id = msg.body["graph_id"]
                name = msg.body["name"]
                env = self._engine._environments.get(graph_id)
                if env is None:
                    raise KeyError(f"graph {graph_id!r} not found")
                ch = env.shared_channels.get(name)
                if ch is None:
                    available = env.shared_channels.list_channels()
                    raise ValueError(
                        f"Channel {name!r} not found. Available: {available}"
                    )
                content = unpack_content(msg.body["content"])
                cm = ChannelMessage(
                    sender=msg.body.get("sender", "human"),
                    content=content,
                )
                await ch.send(cm)
                return {"message_id": cm.message_id}

            case "connect":
                result = await self._engine.connect(
                    msg.body["sender_id"],
                    msg.body["receiver_id"],
                    channel=msg.body.get("channel"),
                )
                return {"result": pack_connection_result(result)}

            case "disconnect":
                result = await self._engine.disconnect(
                    msg.body["sender_id"],
                    msg.body["receiver_id"],
                    channel=msg.body.get("channel"),
                )
                return {"result": pack_disconnection_result(result)}

            case "inject_input":
                # Validate routing before injection to prevent retries duplicating input.
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                await creature.inject_input(
                    unpack_content(msg.body["message"]),
                    source=msg.body.get("source", "chat"),
                )
                return {}

            # Hosted checks let the controller repair stale home-node routing.
            case "interrupt":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                creature.agent.interrupt()
                return {}

            case "list_jobs":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                agent = creature.agent
                jobs = [j.to_dict() for j in agent.executor.get_running_jobs()]
                jobs.extend(
                    j.to_dict() for j in agent.subagent_manager.get_running_jobs()
                )
                return {"jobs": jobs}

            case "stop_job":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                agent = creature.agent
                job_id = msg.body["job_id"]
                if agent._interrupt_direct_job(job_id):
                    return {"cancelled": True}
                if await agent.executor.cancel(job_id):
                    return {"cancelled": True}
                cancelled = await agent.subagent_manager.cancel(job_id)
                return {"cancelled": bool(cancelled)}

            case "promote_job":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                agent = creature.agent
                ok = bool(agent._promote_handle(msg.body["job_id"]))
                return {"promoted": ok}

            case "chat_history":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                return {"history": chat_history_for(self._engine, cid)}

            case "chat_branches":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                return {"branches": chat_branches_for(self._engine, cid)}

            case "regenerate":
                agent = self._require_hosted(msg.body["creature_id"]).agent
                raw_target = msg.body.get("target")
                target = UserMessageSelector(**raw_target) if raw_target else None
                kwargs = {
                    "turn_index": msg.body.get("turn_index"),
                    "branch_view": msg.body.get("branch_view"),
                    "request_id": msg.body.get("request_id"),
                }
                if target is not None:
                    kwargs["target"] = target
                await agent.regenerate_last_response(**kwargs)
                return _completed_branch_result(agent, msg.body.get("request_id"))

            case "edit_message":
                agent = self._require_hosted(msg.body["creature_id"]).agent
                raw_target = msg.body.get("target")
                target = UserMessageSelector(**raw_target) if raw_target else None
                kwargs = {
                    "turn_index": msg.body.get("turn_index"),
                    "user_position": msg.body.get("user_position"),
                    "branch_view": msg.body.get("branch_view"),
                    "request_id": msg.body.get("request_id"),
                }
                if target is not None:
                    kwargs["target"] = target
                ok = await agent.edit_and_rerun(
                    msg.body["msg_idx"],
                    unpack_content(msg.body["content"]),
                    **kwargs,
                )
                if not ok:
                    raise ValueError(f"message {msg.body['msg_idx']} cannot be edited")
                return _completed_branch_result(agent, msg.body.get("request_id"))

            case "rewind":
                await self._require_hosted(msg.body["creature_id"]).agent.rewind_to(
                    msg.body["msg_idx"]
                )
                return {}

            case "get_scratchpad":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"scratchpad": agent_scratchpad(creature.agent)}

            case "patch_scratchpad":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {
                    "scratchpad": agent_patch_scratchpad(
                        creature.agent, msg.body["updates"]
                    )
                }

            case "list_triggers":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"triggers": agent_triggers(creature.agent)}

            case "get_env":

                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"env": agent_env(creature.agent)}

            case "get_system_prompt":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return agent_system_prompt(creature.agent)

            case "get_working_dir":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"working_dir": agent_working_dir(creature.agent)}

            case "set_working_dir":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {
                    "working_dir": agent_set_working_dir(
                        creature.agent, msg.body["new_path"]
                    )
                }

            case "native_tool_inventory":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"inventory": agent_native_tool_inventory(creature.agent)}

            case "get_native_tool_options":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"options": agent_get_native_tool_options(creature.agent)}

            case "set_native_tool_options":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {
                    "options": agent_set_native_tool_options(
                        creature.agent,
                        msg.body["tool"],
                        msg.body.get("values", {}),
                    )
                }

            case "switch_model":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                model = msg.body["model"]
                # Remote profiles must be registered before synchronous model resolution.
                await self._prewarm_profile_by_selector(model)
                setter = getattr(creature.agent, "switch_model", None)
                if callable(setter):
                    setter(model)
                else:
                    creature.agent.config.model = model
                return {"model": model}

            case "list_plugins":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"plugins": agent_list_plugins(creature.agent)}

            case "toggle_plugin":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                name = msg.body["plugin_name"]
                enabled = bool(msg.body.get("enabled", True))
                result = await agent_toggle_plugin(creature.agent, name, enabled)
                return {"plugin": result["name"], "enabled": result["enabled"]}

            case "list_output_wiring":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                try:
                    edges = self._engine.list_output_wiring(cid)
                except Exception:
                    edges = []
                return {"edges": [dict(e) for e in edges]}

            case "wire_output":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                edge_id = await self._engine.wire_output(cid, msg.body["target"])
                return {"edge_id": str(edge_id)}

            case "unwire_output":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                ok = await self._engine.unwire_output(cid, msg.body["edge_id"])
                return {"unwired": bool(ok)}

            case "unwire_output_sink":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                ok = await self._engine.unwire_output_sink(cid, msg.body["sink_id"])
                return {"unwired": bool(ok)}

            case "wire_creature":
                # Wiring lookup failures are resource errors, not stale home-node signals.
                wire_creature_on_engine(
                    self._engine,
                    msg.body["graph_id"],
                    msg.body["creature_id"],
                    msg.body["channel"],
                    msg.body["direction"],
                    enabled=bool(msg.body.get("enabled", True)),
                )
                return {}

            case "attach_policies":
                cid = msg.body["creature_id"]
                return {"policies": attach_policies_for(self._engine, cid)}

            case "session_attach_policies":
                sid = msg.body["session_id"]
                return {"policies": session_attach_policies_for(self._engine, sid)}

            case "runtime_graph_snapshot":
                snap = build_runtime_graph_snapshot_for(self._engine)
                for g in snap.get("graphs", []):
                    g.setdefault("node_id", self._node_id)
                return {"snapshot": snap}

            case "list_modules":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return {"modules": agent_list_modules(creature.agent)}

            case "get_module_options":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return agent_get_module_options(
                    creature.agent,
                    msg.body["module_type"],
                    msg.body["module_name"],
                )

            case "set_module_options":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return agent_set_module_options(
                    creature.agent,
                    msg.body["module_type"],
                    msg.body["module_name"],
                    msg.body.get("values", {}),
                )

            case "toggle_module":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return await agent_toggle_module(
                    creature.agent,
                    msg.body["module_type"],
                    msg.body["module_name"],
                )

            case "command_inventory":
                if msg.sender_node != HOST_NODE_ID:
                    return {
                        "error": {
                            "kind": "forbidden",
                            "message": (
                                "command_inventory refused from non-host origin "
                                f"{msg.sender_node!r}"
                            ),
                        }
                    }
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return agent_command_inventory(creature.agent)

            case "invoke_skill":
                if msg.sender_node != HOST_NODE_ID:
                    return {
                        "error": {
                            "kind": "forbidden",
                            "message": (
                                "invoke_skill refused from non-host origin "
                                f"{msg.sender_node!r}"
                            ),
                        }
                    }
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                return await agent_invoke_skill(
                    creature.agent,
                    msg.body["skill"],
                    msg.body.get("args"),
                    source=msg.body.get("source", "web:skill"),
                )

            case "execute_command":
                # Principal and operator authority may only originate from the host.
                if msg.sender_node != HOST_NODE_ID:
                    return {
                        "error": {
                            "kind": "forbidden",
                            "message": (
                                "execute_command refused from non-host origin "
                                f"{msg.sender_node!r}"
                            ),
                        }
                    }
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                # Shared coercion keeps remote command arguments aligned with local calls.
                args = _normalize_command_args(msg.body.get("args"))
                return await agent_execute_command(
                    creature.agent,
                    msg.body["command"],
                    args,
                    service=self._local_service(),
                    engine=self._engine,
                    creature_id=cid,
                    principal=msg.body.get("principal", "user:local"),
                    is_operator=msg.body.get("is_operator", False),
                )

            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.runtime type: {msg.type!r}",
                    }
                }


__all__ = ["TerrariumRuntimeAdapter"]
