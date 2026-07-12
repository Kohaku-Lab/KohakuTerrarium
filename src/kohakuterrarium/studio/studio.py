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
``kohakuterrarium.studio.<sub>.*``.
"""

from pathlib import Path
from typing import Any

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
from kohakuterrarium.studio.facade_persistence import _PersistenceNS
from kohakuterrarium.studio.facade_sessions import _SessionsNS
from kohakuterrarium.studio.identity import (
    api_keys as _identity_keys,
    codex_oauth as _identity_codex,
    drive_settings as _identity_drives,
    llm_backends as _identity_backends,
    llm_default as _identity_default,
    llm_native_tools as _identity_native_tools,
    llm_profiles as _identity_profiles,
    mcp_servers as _identity_mcp,
    settings as _identity_settings,
    ui_prefs as _identity_ui_prefs,
)
from kohakuterrarium.studio.nodes import NodeMap, build_node_map_if_multi_node
from kohakuterrarium.terrarium import LocalTerrariumService, TerrariumService
from kohakuterrarium.terrarium.engine import Terrarium


class Studio:
    """Programmatic interface for the studio tier.

    ``Studio()`` owns its own runtime; pass ``engine=existing_engine``
    to share one (e.g. with the HTTP server's process-wide singleton).
    The class is an async context manager — entering starts the
    engine, exiting calls :meth:`shutdown`.

    **Runtime dependency.** Studio depends on a
    :class:`TerrariumService` (an abstraction that can be a local
    in-process :class:`LocalTerrariumService` or a future Lab-backed
    remote service). Single-host code is unchanged because
    ``LocalTerrariumService`` is a thin wrapper around the same
    :class:`Terrarium` engine. The :attr:`service` property is the
    new primary handle; :attr:`engine` remains as a backward-compat
    escape hatch that returns ``service.engine``.
    """

    def __init__(
        self,
        engine: Terrarium | None = None,
        *,
        service: TerrariumService | None = None,
    ) -> None:
        # Studio's runtime dependency is a TerrariumService. Three
        # construction paths:
        #
        # - ``service=`` provided (multi-node host mode): use it
        #   directly.  The caller is responsible for engine ownership.
        # - ``engine=`` provided (single-host): wrap in
        #   :class:`LocalTerrariumService`.
        # - neither provided: fresh :class:`Terrarium` +
        #   :class:`LocalTerrariumService` (default single-host case).
        #
        # NB: ``engine or Terrarium()`` is wrong here — Terrarium defines
        # ``__len__`` (number of creatures) which makes an empty engine
        # falsy, so the user's engine would get silently replaced. Test
        # for ``None`` explicitly.
        if service is not None and engine is not None:
            raise TypeError(
                "Studio accepts at most one of {service, engine}; "
                "service implies its own engine"
            )
        if service is not None:
            self._service: TerrariumService = service
            # Only the service-injection path may hold a
            # MultiNodeTerrariumService; calling the helper here is the
            # only place we touch the laboratory layer.  The helper
            # lazy-imports MultiNodeTerrariumService so this branch is
            # the single trigger for that import.
            self.nodes: NodeMap | None = build_node_map_if_multi_node(service)
        elif engine is not None:
            # Explicit programmatic engine — never overwrite its Drive runtime
            # with managed settings (design §8.5).
            self._service = LocalTerrariumService(engine)
            self.nodes = None
        else:
            # Studio owns the engine -> it is the managed surface, so it resolves
            # the node's Drive settings into explicit constructor args (design
            # §8.4/§8.5). Absent/disabled settings degrade to a Drive-disabled
            # engine, so this is behaviourally identical to a bare Terrarium().
            self._service = LocalTerrariumService(
                Terrarium(**_identity_drives.resolve_drive_kwargs())
            )
            # Standalone path — we just built a LocalTerrariumService.
            # Skip the helper entirely so the laboratory layer never
            # loads in single-host boots.
            self.nodes = None
        self.catalog = _CatalogNS(self)
        self.identity = _IdentityNS(self)
        self.sessions = _SessionsNS(self)
        self.persistence = _PersistenceNS(self)
        self.editors = _EditorsNS(self)
        self.attach = _AttachNS(self)

    @property
    def service(self) -> TerrariumService:
        """The runtime service Studio depends on.

        In single-host mode this is a
        :class:`LocalTerrariumService` wrapping the in-process
        Terrarium engine. Multi-node deployments will swap in a
        Lab-backed remote service implementation; Studio code is
        agnostic to the choice.
        """
        return self._service

    @property
    def engine(self) -> Terrarium:
        """The underlying Terrarium engine.

        Backward-compatible accessor — equivalent to
        ``studio.service.engine``. The escape hatch for code that
        needs methods not on the :class:`TerrariumService` Protocol.
        New code should prefer :attr:`service` plus the Protocol
        surface; reaching into ``engine`` ties the call site to
        single-host mode.
        """
        return self._service.engine

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
        cls,
        recipe: str | Path,
        *,
        pwd: str | None = None,
        llm: str | None = None,
        name: str | None = None,
    ) -> "Studio":
        """Construct a Studio with a freshly-applied terrarium recipe.

        Behaves identically to ``studio.sessions.start_terrarium``: the
        session store is attached, the session meta is registered, and
        the session lists by name — previously this constructor applied
        the recipe straight onto a bare engine and silently skipped all
        persistence bookkeeping.
        """
        studio = cls()
        await studio.sessions.start_terrarium(recipe, pwd=pwd, llm=llm, name=name)
        return studio

    @classmethod
    async def with_creature(
        cls,
        config: str | Path,
        *,
        pwd: str | None = None,
        llm: str | None = None,
    ) -> "Studio":
        """Construct a Studio with a single creature already started."""
        studio = cls()
        await studio.sessions.start_creature(config, pwd=pwd, llm=llm)
        return studio

    @classmethod
    async def resume(
        cls,
        store_or_path: str | Path,
        *,
        pwd: str | None = None,
        llm: str | None = None,
    ) -> "Studio":
        """Construct a Studio from a saved session, adopted into a fresh engine."""
        studio = cls()
        await studio.persistence.resume(store_or_path, pwd_override=pwd, llm=llm)
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
            source, editable=editable, name=name
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
        self.drives = _IdentityDrives(studio)


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


class _IdentityDrives:
    """Managed Drive runtime settings — the Studio settings owner (design §8.4).

    ``save`` persists validated config; ``apply`` is the distinct live-application
    operation against the Studio-owned engine (design §8.6). ``resolve`` returns
    the explicit :class:`DriveRuntimeSpec` managed construction paths inject.
    """

    def __init__(self, studio: Studio) -> None:
        self._studio = studio

    def path(self) -> Path:
        return _identity_drives.drive_settings_path()

    def load(self) -> Any:
        return _identity_drives.load_settings()

    def save(
        self,
        values: dict[str, Any],
        *,
        expected_revision: str | None = None,
        expected_exists: bool | None = None,
    ) -> Any:
        return _identity_drives.save_settings(
            values,
            expected_revision=expected_revision,
            expected_exists=expected_exists,
        )

    def resolve(self, node: str = _identity_drives.DEFAULT_NODE) -> Any:
        return _identity_drives.resolve_runtime(node)

    def status(self, node: str = _identity_drives.DEFAULT_NODE) -> dict[str, Any]:
        return _identity_drives.settings_status(node)

    def apply(self, node: str = _identity_drives.DEFAULT_NODE) -> dict[str, Any]:
        return _identity_drives.apply_runtime(self._studio.engine, node=node)


# ---------------------------------------------------------------------------
# sessions namespace
# ---------------------------------------------------------------------------


# _SessionsNS and its per-creature sub-namespaces live in
# ``studio/facade_sessions.py`` (split for the file-size cap).


# persistence namespace
# ---------------------------------------------------------------------------


# _PersistenceNS / _PersistenceViewer live in
# ``studio/facade_persistence.py`` (split for the file-size cap).


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
        return _policies.get_creature_policies(self._studio._service, creature_id)

    def policies_for_session(self, session_id: str) -> list:
        return _policies.get_session_policies(self._studio._service, session_id)
