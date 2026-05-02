"""Studio — programmatic façade for the studio tier.

Wraps a :class:`Terrarium` engine and exposes the studio
sub-packages (catalog / identity / sessions / persistence / editors /
attach) as nested namespaces.  Pure consumer of the existing
``studio/*`` submodules — no logic of its own beyond delegation.

Construction::

    async with Studio() as s: ...                    # owns its own engine
    async with Studio(engine=t) as s: ...            # share an engine
    s = await Studio.from_recipe("terrarium.yaml")   # build + start
    s = await Studio.with_creature("@kt-biome/creatures/general")

Usage::

    async with Studio() as s:
        sess = await s.sessions.start_creature("@kt-biome/creatures/general")
        pkgs = s.catalog.packages.list()
        profiles = s.identity.llm.list_profiles()
        saved = s.persistence.list()
        async for chunk in s.sessions.chat.chat(sess.session_id, sess.creatures[0]['creature_id'], "hi"):
            print(chunk, end="", flush=True)
        await s.sessions.stop(sess.session_id)

The Studio class is *organizational* — every namespace method is a
one-liner that forwards to an existing function under
``kohakuterrarium.studio.<sub>.*``.  See
``plans/structure-hierarchy/studio-class/design.md`` for the
rationale.
"""

from pathlib import Path
from typing import Any, AsyncIterator

from kohakuterrarium.studio.attach import policies as _policies
from kohakuterrarium.studio.catalog import (
    builtins as _catalog_builtins,
    creatures as _catalog_creatures,
    introspect as _catalog_introspect,
    modules as _catalog_modules,
    packages as _catalog_packages,
    packages_remote as _catalog_remote,
    packages_scan as _catalog_scan,
)
from kohakuterrarium.studio.editors import creatures_crud as _editor_creatures
from kohakuterrarium.studio.editors import modules_crud as _editor_modules
from kohakuterrarium.studio.identity import (
    api_keys as _identity_keys,
    codex_oauth as _identity_codex,
    llm_backends as _identity_backends,
    llm_default as _identity_default,
    llm_native_tools as _identity_native_tools,
    llm_profiles as _identity_profiles,
    mcp_servers as _identity_mcp,
    settings as _identity_settings,
    ui_prefs as _identity_ui_prefs,
)
from kohakuterrarium.studio.persistence import (
    fork as _persistence_fork,
    history as _persistence_history,
    resume as _persistence_resume,
    store as _persistence_store,
)
from kohakuterrarium.studio.persistence.viewer import (
    diff as _viewer_diff,
    events as _viewer_events,
    export as _viewer_export,
    summary as _viewer_summary,
    tree as _viewer_tree,
    turns as _viewer_turns,
)
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
from kohakuterrarium.terrarium.engine import Terrarium


class Studio:
    """Programmatic interface for the studio tier.

    ``Studio()`` owns its own ``Terrarium`` engine; pass
    ``engine=existing_engine`` to share one (e.g. with the HTTP
    server's process-wide singleton).  The class is an async context
    manager — entering starts the engine, exiting calls
    :meth:`shutdown`.
    """

    def __init__(self, engine: Terrarium | None = None) -> None:
        self.engine: Terrarium = engine or Terrarium()
        self.catalog = _CatalogNS(self)
        self.identity = _IdentityNS(self)
        self.sessions = _SessionsNS(self)
        self.persistence = _PersistenceNS(self)
        self.editors = _EditorsNS(self)
        self.attach = _AttachNS(self)

    # --- async context manager ---

    async def __aenter__(self) -> "Studio":
        await self.engine.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.engine.__aexit__(exc_type, exc, tb)

    async def shutdown(self) -> None:
        """Stop every session and shut the engine down."""
        await self.engine.shutdown()

    # --- classmethod constructors ---

    @classmethod
    async def from_recipe(
        cls, recipe: str | Path, *, pwd: str | None = None
    ) -> "Studio":
        """Construct a Studio with a freshly-applied terrarium recipe."""
        engine = await Terrarium.from_recipe(str(recipe), pwd=pwd)
        return cls(engine=engine)

    @classmethod
    async def with_creature(
        cls,
        config: str | Path,
        *,
        pwd: str | None = None,
        llm_override: str | None = None,
    ) -> "Studio":
        """Construct a Studio with a single creature already started."""
        studio = cls()
        await studio.sessions.start_creature(config, pwd=pwd, llm_override=llm_override)
        return studio

    @classmethod
    async def resume(
        cls,
        store_or_path: str | Path,
        *,
        pwd: str | None = None,
        llm_override: str | None = None,
    ) -> "Studio":
        """Construct a Studio from a saved session, adopted into a fresh engine."""
        studio = cls()
        await studio.persistence.resume(
            store_or_path, pwd_override=pwd, llm_override=llm_override
        )
        return studio


# ---------------------------------------------------------------------------
# catalog namespace
# ---------------------------------------------------------------------------


class _CatalogNS:
    """Read-only catalogs: packages, creatures, modules, builtins, introspect."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio
        self.packages = _CatalogPackages()
        self.creatures = _CatalogCreatures()
        self.modules = _CatalogModules()
        self.builtins = _CatalogBuiltins()
        self.introspect = _CatalogIntrospect()


class _CatalogPackages:
    """Package catalog — install / uninstall / update / list."""

    # ``list`` is defined last so it does not shadow the ``list``
    # builtin in the annotations of the methods below it.

    def scan(self) -> list[Any]:
        return _catalog_scan.scan_catalog()

    def install(
        self, source: str, *, editable: bool = False, name: str | None = None
    ) -> str:
        return _catalog_packages.install_package_op(
            source, editable=editable, name_override=name
        )

    def uninstall(self, name: str) -> bool:
        return _catalog_packages.uninstall_package_op(name)

    def update(self, name: str) -> tuple[int, str]:
        return _catalog_packages.update_package_op(name)

    def update_all(self) -> tuple[int, list[str], int, int]:
        return _catalog_packages.update_all_packages_op()

    def show(self, agent_path: str) -> tuple[int, dict | str]:
        return _catalog_packages.load_agent_info(agent_path)

    def remote(self) -> list[dict[str, Any]]:
        return _catalog_remote.load_remote_registry()

    def list(self) -> list[dict[str, Any]]:
        return _catalog_packages.list_installed_packages()


class _CatalogCreatures:
    """Workspace creature catalog (read side)."""

    def list(self, workspace: Any) -> list[dict]:
        return _catalog_creatures.list_creatures(workspace)

    def get(self, workspace: Any, name: str) -> dict:
        return _catalog_creatures.load_creature(workspace, name)

    def read_prompt(self, workspace: Any, creature: str, rel: str) -> str:
        return _catalog_creatures.read_prompt(workspace, creature, rel)


class _CatalogModules:
    """Workspace module catalog (read side)."""

    def list(self, workspace: Any, kind: str) -> list[dict]:
        return _catalog_modules.list_modules(workspace, kind)

    def get(self, workspace: Any, kind: str, name: str) -> dict:
        return _catalog_modules.load_module(workspace, kind, name)

    def doc(self, workspace: Any, kind: str, name: str) -> dict:
        return _catalog_modules.load_module_doc(workspace, kind, name)


class _CatalogBuiltins:
    """Builtin extensions catalog (tools, subagents, triggers, plugins, IO)."""

    def list(self, kind: str | None = None) -> list[dict]:
        return _catalog_builtins.list_builtins(kind)

    def info(self, name: str) -> dict | None:
        return _catalog_builtins.builtin_info(name)


class _CatalogIntrospect:
    """Schema introspection over builtin + workspace modules."""

    def builtin_schema(self, kind: str) -> dict:
        return _catalog_introspect.builtin_schema(kind)

    def custom_schema(self, *args, **kwargs) -> dict:
        return _catalog_introspect.custom_schema(*args, **kwargs)


# ---------------------------------------------------------------------------
# identity namespace
# ---------------------------------------------------------------------------


class _IdentityNS:
    """LLM profiles + keys + Codex + MCP + UI prefs + generic config."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio
        self.llm = _IdentityLLM()
        self.keys = _IdentityKeys()
        self.codex = _IdentityCodex()
        self.mcp = _IdentityMCP()
        self.ui_prefs = _IdentityUIPrefs()
        self.settings = _IdentitySettings()


class _IdentityLLM:
    """LLM backends + profiles + default-model + native-tools."""

    # backends
    def list_backends(self) -> list[dict[str, Any]]:
        return _identity_backends.list_backends()

    def save_backend(self, *args, **kwargs) -> Any:
        return _identity_backends.save_backend_record(*args, **kwargs)

    def delete_backend(self, name: str) -> bool:
        return _identity_backends.remove_backend(name)

    # profiles
    def list_profiles(self) -> list[dict[str, Any]]:
        return _identity_profiles.list_profiles_payload()

    def save_profile(self, *args, **kwargs) -> Any:
        return _identity_profiles.save_profile_record(*args, **kwargs)

    def delete_profile(self, name: str, provider: str = "") -> bool:
        return _identity_profiles.remove_profile(name, provider)

    def get_profile(self, identifier: str) -> Any:
        return _identity_profiles.get_profile_for_identifier(identifier)

    # default model
    def get_default(self) -> str:
        return _identity_default.get_default()

    def set_default(self, identifier: str) -> str:
        return _identity_default.set_default(identifier)

    def list_models(self) -> list[dict[str, Any]]:
        return _identity_default.list_all_models_combined()

    # native tools
    def list_native_tools(self) -> list[dict[str, Any]]:
        return _identity_native_tools.list_native_tools()


class _IdentityKeys:
    """Provider API keys."""

    def list(self) -> list[dict[str, Any]]:
        return _identity_keys.list_keys_payload()

    def set(self, provider: str, key: str) -> None:
        return _identity_keys.set_key(provider, key)

    def delete(self, provider: str) -> None:
        return _identity_keys.remove_key(provider)

    def get(self, provider: str) -> str:
        return _identity_keys.get_existing_key(provider)


class _IdentityCodex:
    """Codex OAuth — login / status / usage."""

    async def login(self) -> dict[str, Any]:
        return await _identity_codex.login_async()

    def status(self) -> dict[str, Any]:
        return _identity_codex.get_status()

    async def usage(self) -> dict[str, Any]:
        return await _identity_codex.get_usage_async()


class _IdentityMCP:
    """MCP server registry — single canonical yaml parser."""

    # ``list`` is defined last so it does not shadow the builtin
    # within the annotations of methods below it.

    def save_all(self, servers: list[dict[str, Any]]) -> None:
        return _identity_mcp.save_servers(servers)

    def upsert(self, server: dict[str, Any]) -> dict[str, Any]:
        return _identity_mcp.upsert_server(server)

    def delete(self, name: str) -> bool:
        return _identity_mcp.delete_server(name)

    def find(self, name: str) -> dict[str, Any] | None:
        return _identity_mcp.find_server(name)

    def list(self) -> list[dict[str, Any]]:
        return _identity_mcp.load_servers()


class _IdentityUIPrefs:
    """UI preferences."""

    def load(self) -> dict[str, Any]:
        return _identity_ui_prefs.load_prefs()

    def save(self, values: dict[str, Any]) -> dict[str, Any]:
        return _identity_ui_prefs.save_prefs(values)


class _IdentitySettings:
    """Generic config settings (paths / show / edit)."""

    def paths(self) -> dict[str, Path]:
        return _identity_settings.config_paths()


# ---------------------------------------------------------------------------
# sessions namespace
# ---------------------------------------------------------------------------


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
        llm_override: str | None = None,
    ) -> _session_handles.Session:
        return await _session_lifecycle.start_creature(
            self._studio.engine,
            config_path=str(config_or_path),
            pwd=pwd,
            llm_override=llm_override,
        )

    async def start_terrarium(
        self,
        config_or_path: str | Path,
        *,
        pwd: str | None = None,
    ) -> _session_handles.Session:
        return await _session_lifecycle.start_terrarium(
            self._studio.engine, config_path=str(config_or_path), pwd=pwd
        )

    def list(self) -> list[_session_handles.SessionListing]:
        return _session_lifecycle.list_sessions(self._studio.engine)

    def get(self, session_id: str) -> _session_handles.Session:
        return _session_lifecycle.get_session(self._studio.engine, session_id)

    async def stop(self, session_id: str) -> None:
        await _session_lifecycle.stop_session(self._studio.engine, session_id)

    def find_creature(self, session_id: str, name_or_id: str) -> Any:
        return _session_lifecycle.find_creature(
            self._studio.engine, session_id, name_or_id
        )

    def find_session_for_creature(self, creature_id: str) -> str | None:
        return _session_lifecycle.find_session_for_creature(
            self._studio.engine, creature_id
        )

    # creature CRUD inside a running session (hot-plug)
    async def add_creature(self, session_id: str, config: Any) -> str:
        return await _session_lifecycle.add_creature(
            self._studio.engine, session_id, config
        )

    def list_creatures(self, session_id: str) -> "list[dict]":
        return _session_lifecycle.list_creatures(self._studio.engine, session_id)

    async def remove_creature(self, session_id: str, creature_id: str) -> bool:
        return await _session_lifecycle.remove_creature(
            self._studio.engine, session_id, creature_id
        )

    # topology + wiring
    async def add_channel(self, session_id: str, *args, **kwargs) -> Any:
        return await _session_topology.add_channel(
            self._studio.engine, session_id, *args, **kwargs
        )

    async def connect(self, *args, **kwargs) -> Any:
        return await _session_topology.connect(self._studio.engine, *args, **kwargs)

    async def disconnect(self, *args, **kwargs) -> Any:
        return await _session_topology.disconnect(self._studio.engine, *args, **kwargs)

    async def wire_output(self, *args, **kwargs) -> Any:
        return await _session_wiring.wire_output(self._studio.engine, *args, **kwargs)

    async def unwire_output(self, *args, **kwargs) -> Any:
        return await _session_wiring.unwire_output(self._studio.engine, *args, **kwargs)

    def list_output_wiring(self, *args, **kwargs) -> Any:
        return _session_wiring.list_output_wiring(self._studio.engine, *args, **kwargs)

    async def wire_output_sink(self, *args, **kwargs) -> Any:
        return await _session_wiring.wire_output_sink(
            self._studio.engine, *args, **kwargs
        )

    async def unwire_output_sink(self, *args, **kwargs) -> Any:
        return await _session_wiring.unwire_output_sink(
            self._studio.engine, *args, **kwargs
        )

    # memory search
    def search_memory(self, name: str, **kwargs) -> dict[str, Any]:
        return _session_memory.search_session_memory(name, **kwargs)


class _SessionsChat:
    """Per-creature chat — chat HTTP fallback, regenerate, edit, rewind."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    async def chat(
        self, session_id: str, creature_id: str, content: Any
    ) -> AsyncIterator[str]:
        return _session_chat.chat(self._studio.engine, session_id, creature_id, content)

    async def regenerate(self, session_id: str, creature_id: str) -> None:
        await _session_chat.regenerate(self._studio.engine, session_id, creature_id)

    async def edit_message(
        self, session_id: str, creature_id: str, msg_idx: int, content: Any, **kwargs
    ) -> bool:
        return await _session_chat.edit_message(
            self._studio.engine, session_id, creature_id, msg_idx, content, **kwargs
        )

    async def rewind(self, session_id: str, creature_id: str, msg_idx: int) -> None:
        await _session_chat.rewind(
            self._studio.engine, session_id, creature_id, msg_idx
        )

    def history(self, session_id: str, creature_id: str) -> dict[str, Any]:
        return _session_chat.history(self._studio.engine, session_id, creature_id)

    def branches(self, session_id: str, creature_id: str) -> dict[str, Any]:
        return _session_chat.branches(self._studio.engine, session_id, creature_id)


class _SessionsCtl:
    """Per-creature control — interrupt, jobs, cancel, promote."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    async def interrupt(self, session_id: str, creature_id: str) -> None:
        await _session_ctl.interrupt(self._studio.engine, session_id, creature_id)

    def list_jobs(self, session_id: str, creature_id: str) -> list[dict]:
        return _session_ctl.list_jobs(self._studio.engine, session_id, creature_id)

    async def cancel_job(self, session_id: str, creature_id: str, job_id: str) -> bool:
        return await _session_ctl.cancel_job(
            self._studio.engine, session_id, creature_id, job_id
        )

    def promote_job(self, session_id: str, creature_id: str, job_id: str) -> bool:
        return _session_ctl.promote_job(
            self._studio.engine, session_id, creature_id, job_id
        )


class _SessionsState:
    """Per-creature state — scratchpad, triggers, env, system prompt, working dir."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def scratchpad(self, session_id: str, creature_id: str) -> dict[str, str]:
        return _session_state.get_scratchpad(
            self._studio.engine, session_id, creature_id
        )

    def patch_scratchpad(
        self, session_id: str, creature_id: str, updates: dict[str, str | None]
    ) -> dict[str, str]:
        return _session_state.patch_scratchpad(
            self._studio.engine, session_id, creature_id, updates
        )

    def triggers(self, session_id: str, creature_id: str) -> list[dict[str, Any]]:
        return _session_state.list_triggers(
            self._studio.engine, session_id, creature_id
        )

    def env(self, session_id: str, creature_id: str) -> dict[str, Any]:
        return _session_state.get_env(self._studio.engine, session_id, creature_id)

    def system_prompt(self, session_id: str, creature_id: str) -> dict[str, str]:
        return _session_state.get_system_prompt(
            self._studio.engine, session_id, creature_id
        )

    def working_dir(self, session_id: str, creature_id: str) -> str:
        return _session_state.get_working_dir(
            self._studio.engine, session_id, creature_id
        )

    def set_working_dir(self, session_id: str, creature_id: str, new_path: str) -> str:
        return _session_state.set_working_dir(
            self._studio.engine, session_id, creature_id, new_path
        )


class _SessionsPlugins:
    """Per-creature plugin list / toggle."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def list(self, session_id: str, creature_id: str) -> list[dict]:
        return _session_plugins.list_plugins(
            self._studio.engine, session_id, creature_id
        )

    async def toggle(self, session_id: str, creature_id: str, plugin_name: str) -> dict:
        return await _session_plugins.toggle_plugin(
            self._studio.engine, session_id, creature_id, plugin_name
        )


class _SessionsModel:
    """Per-creature model + native-tool-options."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def switch(self, session_id: str, creature_id: str, profile_name: str) -> str:
        return _session_model.switch_model(
            self._studio.engine, session_id, creature_id, profile_name
        )

    def native_tool_options(self, session_id: str, creature_id: str) -> dict[str, dict]:
        return _session_state.get_native_tool_options(
            self._studio.engine, session_id, creature_id
        )


class _SessionsCommand:
    """Per-creature slash command execution."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    async def execute(
        self, session_id: str, creature_id: str, command: str, args: str = ""
    ) -> dict:
        return await _session_command.execute_command(
            self._studio.engine, session_id, creature_id, command, args
        )


# ---------------------------------------------------------------------------
# persistence namespace
# ---------------------------------------------------------------------------


class _PersistenceNS:
    """Saved-session list / resume / fork / viewer / memory."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio
        self.viewer = _PersistenceViewer()

    def list(self, **kwargs) -> list[dict]:
        return _persistence_store.get_session_index(**kwargs)

    async def resume(
        self,
        store_or_path: str | Path,
        *,
        pwd_override: str | None = None,
        llm_override: str | None = None,
    ) -> _session_handles.Session:
        return await _persistence_resume.resume_session(
            self._studio.engine,
            store_or_path,
            pwd_override=pwd_override,
            llm_override=llm_override,
        )

    def announce_migration(self, path: Path) -> None:
        _persistence_resume.announce_migration_if_needed(path)

    async def fork(self, *args, **kwargs) -> Any:
        return await _persistence_fork.fork_session_handler(*args, **kwargs)

    def delete(self, name: str) -> "list[Path]":
        return _persistence_store.delete_session_files(name)

    def history_index(self, path: Path) -> dict[str, Any]:
        return _persistence_history.history_index_payload(path)

    def history(self, path: Path, target: str) -> dict[str, Any]:
        return _persistence_history.history_payload(path, target)

    def resolve_path(self, name: str) -> Path | None:
        return _persistence_store.resolve_session_path_default(name)


class _PersistenceViewer:
    """Post-hoc viewer payloads — tree / summary / turns / events / diff / export."""

    def tree(self, store: Any, session_name: str) -> dict[str, Any]:
        return _viewer_tree.build_tree_payload(store, session_name)

    def summary(self, *args, **kwargs) -> dict[str, Any]:
        return _viewer_summary.build_summary_payload(*args, **kwargs)

    def turns(self, *args, **kwargs) -> dict[str, Any]:
        return _viewer_turns.build_turns_payload(*args, **kwargs)

    def events(self, *args, **kwargs) -> dict[str, Any]:
        return _viewer_events.build_events_payload(*args, **kwargs)

    def diff(self, *args, **kwargs) -> dict[str, Any]:
        return _viewer_diff.build_diff_payload(*args, **kwargs)

    def export(self, *args, **kwargs) -> Any:
        return _viewer_export.build_export(*args, **kwargs)


# ---------------------------------------------------------------------------
# editors namespace
# ---------------------------------------------------------------------------


class _EditorsNS:
    """Workspace creature + module CRUD."""

    def __init__(self, studio: Studio) -> None:
        self._studio = studio
        self.creatures = _EditorCreatures()
        self.modules = _EditorModules()


class _EditorCreatures:
    """Workspace creature scaffold / save / delete / write_prompt."""

    def scaffold(self, creatures_dir: Path, name: str, base: str | None = None) -> Path:
        return _editor_creatures.scaffold_creature(creatures_dir, name, base)

    def save(self, creatures_dir: Path, name: str, body: dict) -> Path:
        return _editor_creatures.save_creature(creatures_dir, name, body)

    def delete(self, creatures_dir: Path, name: str) -> None:
        _editor_creatures.delete_creature(creatures_dir, name)

    def write_prompt(
        self, creatures_dir: Path, creature: str, rel: str, body: str
    ) -> None:
        _editor_creatures.write_prompt(creatures_dir, creature, rel, body)


class _EditorModules:
    """Workspace module scaffold / save / delete / doc."""

    def scaffold(self, *args, **kwargs) -> Any:
        return _editor_modules.scaffold_module(*args, **kwargs)

    def save(self, *args, **kwargs) -> Any:
        return _editor_modules.save_module(*args, **kwargs)

    def delete(self, *args, **kwargs) -> Any:
        return _editor_modules.delete_module(*args, **kwargs)

    def save_doc(self, *args, **kwargs) -> Any:
        return _editor_modules.save_module_doc(*args, **kwargs)


# ---------------------------------------------------------------------------
# attach namespace — programmatic policy advertisement
# ---------------------------------------------------------------------------


class _AttachNS:
    """Attach-policy advertisement for the studio surface.

    The streaming attach helpers (IO chat, channel observer, live
    trace, log tail, file watcher, pty) are WebSocket-bound today and
    intentionally not exposed here — programmatic streaming is part
    of the follow-up work.  The advertisement helpers below let
    programmatic callers ask "what attach modes does this creature
    support" without spinning up a websocket.
    """

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def policies_for_creature(self, creature_id: str) -> list:
        return _policies.get_creature_policies(self._studio.engine, creature_id)

    def policies_for_session(self, session_id: str) -> list:
        return _policies.get_session_policies(self._studio.engine, session_id)
