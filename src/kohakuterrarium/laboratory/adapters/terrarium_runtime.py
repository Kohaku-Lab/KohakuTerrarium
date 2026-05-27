"""APP extension adapter for ``terrarium.runtime``.

Binds one :class:`Terrarium` engine to one Lab node (host or client).
Translates incoming APP messages on namespace ``terrarium.runtime``
into engine method calls and packs results back via
:mod:`kohakuterrarium.terrarium.wire`.

The dispatcher in :meth:`_handle` is the authoritative implementation
of the namespace's type → body → response shapes.

Error translation: engine exceptions become structured error bodies
(``{"error": {"kind": "...", "message": "..."}}``) which
:class:`RemoteTerrariumService` re-raises on the controller side. All
KeyError → ``not_found``; ValueError → ``invalid``; everything else
logged at ``exception`` and surfaced as ``engine`` kind.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.llm.backends import set_remote_backend
from kohakuterrarium.llm.preset_store import preset_from_data, set_remote_preset
from kohakuterrarium.llm.profile_types import LLMBackend
from kohakuterrarium.terrarium.creature_ops import (
    agent_env,
    agent_execute_command,
    agent_get_module_options,
    agent_get_native_tool_options,
    agent_list_modules,
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
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import (
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
    """Serialize a :class:`ChannelMessage` to JSON-friendly form for
    the ``channel_history`` wire shape — mirrors the helper in
    ``terrarium/service.py`` but lives here so the worker adapter
    doesn't import the service module.
    """
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
    """Raised when an op references a creature this engine does NOT host.

    Subclass of :class:`KeyError` so that any catch-all ``except KeyError``
    block still catches it, but the dispatcher can map this *specifically*
    to a ``creature_not_hosted`` wire kind that the controller-side
    :class:`MultiNodeTerrariumService` uses as the signal that ``_home``
    is stale and the call should be retried elsewhere.  A generic
    ``KeyError`` raised from inside an engine method body (e.g. a plugin
    dict lookup) MUST NOT be conflated with this — that's what
    distinguishes "wrong worker" from "engine failed mid-op".
    """


class TerrariumRuntimeAdapter:
    """Binds a Terrarium engine to a Lab node under ``terrarium.runtime``.

    Args:
        engine: the local :class:`Terrarium` engine to expose.
        lab_node: a :class:`HostEngine` or :class:`ClientConnector`.
        node_id: identifier returned by the ``node_id`` APP type.
            Defaults to ``lab_node.client_id`` if present, otherwise
            ``"_host"``.
        session_attacher: optional :class:`WorkerSessionAttacher`.  When
            provided (worker mode), every ``add_creature`` automatically
            attaches a SessionStore + ``SessionEventTee`` so events
            persist locally and mirror to the controller; every
            ``remove_creature`` detaches the Tee.  The host's local
            adapter leaves this ``None`` because Studio's own session
            lifecycle handles attachment for host-local creatures.
    """

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
        # Optional IdentityCache used to pre-warm API keys / profiles
        # before the engine builds the creature's LLM provider.  Workers
        # pass one; the host-side adapter (when present) doesn't.
        self._identity_cache = identity_cache
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
            # Order matters: _NotHostedHere is a KeyError subclass,
            # listed first so the controller can tell "wrong worker"
            # apart from a generic KeyError raised mid-op.
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
        """Best-effort: fetch the profile body + API key the config needs.

        Two side effects:

        - **Profile body stashed for sync lookup.**  The host is the
          source of truth for LLM profiles; the worker has no local
          ``llm_profiles.yaml``.  We fetch the body via
          :meth:`IdentityCache.get_profile` and call
          :func:`preset_store.set_remote_preset` so the sync
          ``load_presets()`` path that builds the LLM provider finds it.
          Without this, the worker silently falls through to its own
          (empty) profile store and picks up a stray default profile
          — the user-reported "API key not found for profile
          'defprofile'" symptom is precisely this fallback.
        - **API key pre-warmed.**  The :class:`IdentityCache` key
          cache is populated for the profile's provider so the SYNC
          ``llm.api_keys.get_api_key`` resolver finds the key when the
          provider is built.
        """
        cache = self._identity_cache
        if cache is None:
            return
        # ``config`` is either an :class:`AgentConfig` (inline form) or
        # a string / ``Path`` (path-form, deployed via studio.deploy).
        # Path-form values carry NO ``llm_profile`` attribute, so
        # ``getattr`` falls through — every worker creature built from
        # a config path then silently misses the prewarm and the LLM
        # build picks up the worker's local default profile.  That is
        # the user-reported "API key not found for profile 'defprofile'"
        # cascade.  Load the on-disk config so the prewarm sees the
        # real ``llm_profile`` / provider / model fields.
        if isinstance(config, (str, Path)):
            try:
                config = load_agent_config(config)
            except Exception:  # pragma: no cover - best-effort
                return
        # Profile lookup: resolves to the profile's provider name.
        # The host's ``studio.identity.get_profile`` matches by bare
        # name (no provider prefix), so split ``provider/name`` form
        # before the RPC and pass only the suffix.
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
                # If the profile uses Codex OAuth, the api_key path
                # isn't enough — the worker's CodexOAuthProvider
                # consults the codex token resolver instead.  Warm it.
                if (profile.get("backend_type") or "") == "codex":
                    await cache.prefetch_for_codex_if_needed()
        # Inline provider — explicit ``provider`` first, then fall back
        # to the prefix of ``model`` (e.g. ``"openai/gpt-4o"`` →
        # ``"openai"``).  Without the model-prefix fallback most configs
        # never trigger a pre-warm because ``provider`` is usually empty.
        prov = getattr(config, "provider", "") or ""
        if not prov:
            model = getattr(config, "model", "") or ""
            if "/" in model:
                prov = model.split("/", 1)[0]
        if prov:
            await cache.prefetch_for_provider(prov)

    async def _prewarm_profile_by_selector(self, selector: str) -> None:
        """Fetch a profile from the host's identity store + stash it.

        Called BEFORE ``agent.switch_model`` so the worker's
        ``resolve_controller_llm`` finds the requested profile.
        ``selector`` is the user-supplied ``model`` string —
        ``provider/name`` or bare ``name`` form.  No-op when the
        worker has no :class:`IdentityCache` wired (standalone tests).
        """
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
        """Convert a host-fetched profile dict to an LLMPreset and stash.

        Two-part stash so the sync ``resolve_controller_llm`` path sees
        the full picture:

        - Synthetic :class:`LLMBackend` keyed by the profile's
          provider, carrying its ``backend_type`` / ``base_url`` /
          ``api_key_env`` — ``_resolve_preset`` drops any preset whose
          provider isn't in ``load_backends()``, so without this the
          preset arrives but resolution fails.
        - The :class:`LLMPreset` itself.

        Accepts ``profile_name`` in either ``"name"`` or
        ``"provider/name"`` form — the latter is what the agent
        config's ``llm_profile`` field typically carries.  Falls back
        to the ``provider`` field on the profile body when the name
        has no slash.
        """
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

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "node_id":
                # Refresh from the live node in case the host assigned
                # a client_id after the adapter was constructed.
                live = getattr(self._node, "client_id", None) or self._node_id
                return {"node_id": live}

            # ---- topology reads -------------------------------------
            case "list_creatures":
                return {
                    "creatures": [
                        pack_creature_info(creature_to_info(c))
                        for c in self._engine.list_creatures()
                    ]
                }

            case "get_creature_info":
                # Reads are different: a missing creature is reported
                # as ``creature_info: null`` (not as creature_not_hosted)
                # so the controller's fan-out aggregation still works
                # — None means "not on this node", and the aggregator
                # tries other nodes.
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

            # ---- lifecycle ------------------------------------------
            case "add_creature":
                config = unpack_creature_build_input(msg.body["config"])
                # Pre-warm identity if a cache is wired.  Best-effort:
                # silently misses become "no key configured, the LLM
                # call will fail at run time".  Done BEFORE
                # ``engine.add_creature`` so the creature's LLM
                # provider sees the key during its build.
                if self._identity_cache is not None:
                    await self._prewarm_identity(config)
                creature = await self._engine.add_creature(
                    config,
                    graph=msg.body.get("graph_id"),
                    creature_id=msg.body.get("creature_id"),
                    llm_override=msg.body.get("llm_override"),
                    pwd=msg.body.get("pwd"),
                    start=msg.body.get("start", True),
                    is_privileged=msg.body.get("is_privileged", False),
                    parent_creature_id=msg.body.get("parent_creature_id"),
                    # The user's chosen display name, threaded from the
                    # Studio spawn form — applied to the worker creature
                    # so it isn't stuck with the config file's own name.
                    name=msg.body.get("name"),
                    # A worker creature is Lab-managed: the controller
                    # drives it through the attach WebSocket. Its config
                    # IO (``input: cli``) must NOT boot — on a foreground
                    # ``kt lab-client`` it would hijack terminal stdin.
                    suppress_io=True,
                )
                # Worker mode only: attach a SessionStore + Tee so the
                # creature's events persist locally AND mirror to the
                # controller.  Without this hook remote creatures have
                # zero persistence — chat tokens stream but nothing
                # can be resumed or replayed.
                if self._session_attacher is not None:
                    self._session_attacher.attach(creature.creature_id)
                return {"creature_info": pack_creature_info(creature_to_info(creature))}

            case "remove_creature":
                cid = msg.body["creature_id"]
                # Pre-check distinguishes "wrong worker" from any
                # KeyError raised inside the engine's remove path —
                # we MUST NOT let the controller retry a remove that
                # already partially succeeded on this node.
                self._require_hosted(cid)
                # Detach the Tee BEFORE the engine removes the
                # creature; the SessionStore stays open so resume
                # still works through the controller's mirror.
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

            # ---- channels -------------------------------------------
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

            # ---- interaction ----------------------------------------
            case "inject_input":
                # Pre-check via _require_hosted so a re-injection after
                # an inner-loop KeyError can't double the side effect.
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                await creature.inject_input(
                    unpack_content(msg.body["message"]),
                    source=msg.body.get("source", "chat"),
                )
                return {}

            # ---- per-creature control --------------------------------
            # Service Protocol §interrupt / list_jobs / stop_job /
            # promote_job. Each routes by ``_home`` on the controller;
            # this side just executes against the agent. Pre-check via
            # ``_require_hosted`` so a stale ``_home`` mapping surfaces
            # as ``creature_not_hosted`` instead of a generic KeyError.
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

            # ---- per-creature chat ops -------------------------------
            # See ``terrarium.creature_ops`` for the matching pure
            # helpers used by both this adapter and ``LocalTerrariumService``.
            case "chat_history":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                return {"history": chat_history_for(self._engine, cid)}

            case "chat_branches":
                cid = msg.body["creature_id"]
                self._require_hosted(cid)
                return {"branches": chat_branches_for(self._engine, cid)}

            case "regenerate":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                agent = creature.agent
                await agent.regenerate_last_response(
                    turn_index=msg.body.get("turn_index"),
                    branch_view=msg.body.get("branch_view"),
                )
                regen_out: dict[str, Any] = {"status": "regenerating"}
                _ti = getattr(agent, "_turn_index", None)
                _bi = getattr(agent, "_branch_id", None)
                if isinstance(_ti, int):
                    regen_out["turn_index"] = _ti
                if isinstance(_bi, int):
                    regen_out["branch_id"] = _bi
                return regen_out

            case "edit_message":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                agent = creature.agent
                ok = await agent.edit_and_rerun(
                    msg.body["msg_idx"],
                    unpack_content(msg.body["content"]),
                    turn_index=msg.body.get("turn_index"),
                    user_position=msg.body.get("user_position"),
                    branch_view=msg.body.get("branch_view"),
                )
                edit_out: dict[str, Any] = {"edited": bool(ok)}
                if ok:
                    edit_out["status"] = "edited"
                    _ti = getattr(agent, "_turn_index", None)
                    _bi = getattr(agent, "_branch_id", None)
                    if isinstance(_ti, int):
                        edit_out["turn_index"] = _ti
                    if isinstance(_bi, int):
                        edit_out["branch_id"] = _bi
                return edit_out

            case "rewind":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                await creature.agent.rewind_to(msg.body["msg_idx"])
                return {}

            # ---- per-creature state ops ------------------------------
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

            # ---- per-creature mutation ops ---------------------------
            case "switch_model":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                model = msg.body["model"]
                # Pre-warm the new profile from the host's identity
                # store BEFORE switching — the worker's local profile
                # store may not know about the model the user just
                # picked.  Without this, ``resolve_controller_llm``
                # inside ``agent.switch_model`` raises "Model profile
                # not found" and the runtime model switch fails even
                # though the model is configured on the host.
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

            # ---- per-creature wiring ---------------------------------
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
                # No _require_hosted pre-check: the creature lookup
                # happens inside wire_creature_on_engine, which raises
                # KeyError that maps to ``not_found`` on the wire.  We
                # don't conflate this with ``creature_not_hosted``
                # because the call's home-node routing is the
                # controller's responsibility, not ours.
                wire_creature_on_engine(
                    self._engine,
                    msg.body["graph_id"],
                    msg.body["creature_id"],
                    msg.body["channel"],
                    msg.body["direction"],
                    enabled=bool(msg.body.get("enabled", True)),
                )
                return {}

            # ---- attach policies / runtime graph --------------------
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

            # ---- module catalog + slash commands --------------------
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

            case "execute_command":
                cid = msg.body["creature_id"]
                creature = self._require_hosted(cid)
                # Use the shared coercion so dict-form args don't get
                # silently dropped — matches LocalImpl behaviour.
                args = _normalize_command_args(msg.body.get("args"))
                return await agent_execute_command(
                    creature.agent, msg.body["command"], args
                )

            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.runtime type: {msg.type!r}",
                    }
                }


__all__ = ["TerrariumRuntimeAdapter"]
