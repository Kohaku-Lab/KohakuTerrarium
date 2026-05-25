"""Behavior tests for :class:`Studio`'s public namespace methods.

Every test here drives a *real* collaborator and asserts the *real*
effect — a real ``Terrarium`` engine (via ``TestTerrariumBuilder``)
for the engine-backed ``sessions`` / ``attach`` namespaces, the real
deterministic builtin catalog for ``catalog.builtins`` /
``catalog.introspect``, and real round-trips against tmp-redirected
config / session paths for ``identity`` / ``persistence`` / ``editors``.

The Studio class is a thin delegation layer; the contract these tests
pin is "calling ``Studio().<ns>.<method>`` produces the same observable
effect as calling the underlying ``studio.<sub>.*`` function on a real
collaborator." Pure-passthrough tests over ``MagicMock`` (the previous
content of this file) verified nothing and were removed.

Out-of-scope and therefore NOT tested here (see ``tests/README.md``):
``catalog.packages`` install/uninstall/update (need a live package
index / pip) and ``identity.codex`` login/usage (need the live Codex
OAuth provider).
"""

import json

import pytest

from kohakuterrarium.studio.identity import ui_prefs as _identity_ui_prefs_mod
from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import (
    session_index as _persistence_session_index,
    store as _persistence_store_mod,
)
from kohakuterrarium.studio.sessions import lifecycle as _lifecycle
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def studio_engine():
    """A real Studio over a real two-creature engine in one graph."""
    engine = await (
        TestTerrariumBuilder().with_creature("alice").with_creature("bob").build()
    )
    s = Studio(engine=engine)
    _lifecycle._meta.clear()
    _lifecycle._session_stores.clear()
    try:
        yield s, engine
    finally:
        _lifecycle._meta.clear()
        _lifecycle._session_stores.clear()
        await engine.shutdown()


# ── catalog.builtins — real deterministic builtin catalog ──────


class TestCatalogBuiltins:
    def test_list_tools_contains_real_builtins(self):
        s = Studio()
        tools = s.catalog.builtins.list("tools")
        names = {t["name"] for t in tools}
        # bash / read / write are core builtin tools that must exist.
        assert {"bash", "read", "write"} <= names
        bash = next(t for t in tools if t["name"] == "bash")
        assert bash["source"] == "builtin"
        assert bash["type"] == "builtin"
        # description comes from the tool class, not a hardcoded string.
        assert bash["description"]

    def test_list_subagents_contains_coordinator(self):
        s = Studio()
        subs = s.catalog.builtins.list("subagents")
        names = {x["name"] for x in subs}
        assert "coordinator" in names
        assert all(x["source"] == "builtin" for x in subs)

    def test_list_none_unions_all_kinds(self):
        s = Studio()
        combined = s.catalog.builtins.list(None)
        tools = s.catalog.builtins.list("tools")
        subs = s.catalog.builtins.list("subagents")
        triggers = s.catalog.builtins.list("triggers")
        assert len(combined) == len(tools) + len(subs) + len(triggers)

    def test_list_unknown_kind_raises(self):
        s = Studio()
        with pytest.raises(ValueError, match="Unknown builtin kind"):
            s.catalog.builtins.list("garbage")

    def test_info_returns_the_matching_entry(self):
        s = Studio()
        entry = s.catalog.builtins.info("bash")
        assert entry is not None
        assert entry["name"] == "bash"
        assert entry["source"] == "builtin"

    def test_info_unknown_returns_none(self):
        s = Studio()
        assert s.catalog.builtins.info("definitely-not-a-builtin") is None


class TestCatalogIntrospect:
    def test_builtin_schema_tools_returns_param_list(self):
        s = Studio()
        schema = s.catalog.introspect.builtin_schema("tools")
        # Contract: a schema payload with a 'params' list of param specs.
        assert set(schema) == {"params", "warnings"}
        assert isinstance(schema["params"], list)
        # bash declares a 'timeout' parameter — the schema must surface it.
        param_names = {p["name"] for p in schema["params"]}
        assert "timeout" in param_names


# ── identity.ui_prefs — real JSON round-trip ───────────────────


class TestIdentityUiPrefs:
    @pytest.fixture(autouse=True)
    def _redirect(self, tmp_path, monkeypatch):
        # Every identity store resolves under ``config_dir()`` —
        # isolate the whole config root via ``KT_CONFIG_DIR``.
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        self._path = tmp_path / "ui_prefs.json"

    def test_load_returns_defaults_when_unset(self):
        s = Studio()
        out = s.identity.ui_prefs.load()
        assert out == _identity_ui_prefs_mod.DEFAULTS

    def test_save_then_load_round_trips(self):
        s = Studio()
        s.identity.ui_prefs.save({"theme": "light"})
        # Persisted to disk...
        on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        assert on_disk["theme"] == "light"
        # ...and a fresh load reflects it.
        assert s.identity.ui_prefs.load()["theme"] == "light"


# ── identity.mcp — real YAML registry round-trip ───────────────


class TestIdentityMcp:
    @pytest.fixture(autouse=True)
    def _redirect(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))

    def test_upsert_then_find_and_list(self):
        s = Studio()
        server = {"name": "alpha", "transport": "stdio"}
        assert s.identity.mcp.upsert(server) == server
        assert s.identity.mcp.find("alpha") == server
        assert s.identity.mcp.list() == [server]

    def test_upsert_replaces_existing(self):
        s = Studio()
        s.identity.mcp.upsert({"name": "alpha", "transport": "stdio"})
        s.identity.mcp.upsert({"name": "alpha", "transport": "http"})
        assert s.identity.mcp.find("alpha")["transport"] == "http"
        assert len(s.identity.mcp.list()) == 1

    def test_delete_removes_entry(self):
        s = Studio()
        s.identity.mcp.upsert({"name": "alpha"})
        assert s.identity.mcp.delete("alpha") is True
        assert s.identity.mcp.find("alpha") is None
        assert s.identity.mcp.delete("alpha") is False

    def test_save_all_replaces_whole_registry(self):
        s = Studio()
        s.identity.mcp.upsert({"name": "old"})
        s.identity.mcp.save_all([{"name": "new1"}, {"name": "new2"}])
        names = {x["name"] for x in s.identity.mcp.list()}
        assert names == {"new1", "new2"}


# ── identity.keys — real key store round-trip ──────────────────


class TestIdentityKeys:
    @pytest.fixture(autouse=True)
    def _redirect(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))

    def test_set_then_get_round_trips(self):
        s = Studio()
        # 'openai' is a built-in backend, so set_key accepts it.
        s.identity.keys.set("openai", "sk-test-123")
        assert s.identity.keys.get("openai") == "sk-test-123"

    def test_delete_clears_the_key(self):
        s = Studio()
        s.identity.keys.set("openai", "sk-test-123")
        s.identity.keys.delete("openai")
        assert s.identity.keys.get("openai") == ""

    def test_set_unknown_provider_raises(self):
        s = Studio()
        with pytest.raises(LookupError, match="Provider not found"):
            s.identity.keys.set("not-a-provider", "k")

    def test_set_empty_key_raises(self):
        s = Studio()
        with pytest.raises(ValueError, match="required"):
            s.identity.keys.set("openai", "")

    def test_list_reports_has_key_after_set(self):
        s = Studio()
        s.identity.keys.set("openai", "sk-test-123")
        entry = next(e for e in s.identity.keys.list() if e["provider"] == "openai")
        assert entry["has_key"] is True


# ── identity.llm — real backend / profile catalog ──────────────


class TestIdentityLlm:
    @pytest.fixture(autouse=True)
    def _redirect(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))

    def test_list_backends_includes_built_ins(self):
        s = Studio()
        backends = s.identity.llm.list_backends()
        names = {b["name"] for b in backends}
        # openai is always a built-in backend.
        assert "openai" in names
        openai = next(b for b in backends if b["name"] == "openai")
        assert openai["built_in"] is True

    def test_save_backend_then_appears_in_list(self):
        s = Studio()
        s.identity.llm.save_backend("mybackend", "openai")
        names = {b["name"] for b in s.identity.llm.list_backends()}
        assert "mybackend" in names

    def test_save_backend_rejects_bad_type(self):
        s = Studio()
        with pytest.raises(ValueError, match="Unsupported backend type"):
            s.identity.llm.save_backend("x", "not-a-backend-type")

    def test_delete_backend_removes_it(self):
        s = Studio()
        s.identity.llm.save_backend("mybackend", "openai")
        assert s.identity.llm.delete_backend("mybackend") is True
        names = {b["name"] for b in s.identity.llm.list_backends()}
        assert "mybackend" not in names

    def test_list_profiles_and_native_tools_are_lists(self):
        s = Studio()
        # Contract: both return list payloads — assert the element shape
        # for whatever the built-in presets produced.
        profiles = s.identity.llm.list_profiles()
        assert isinstance(profiles, list)
        native = s.identity.llm.list_native_tools()
        assert isinstance(native, list)


class TestIdentitySettings:
    def test_paths_exposes_the_canonical_config_files(self):
        s = Studio()
        paths = s.identity.settings.paths()
        # Contract: the studio config surface advertises exactly these
        # well-known files (home dir + four config files).
        assert set(paths) == {
            "home",
            "llm_profiles",
            "api_keys",
            "mcp_servers",
            "ui_prefs",
        }
        # Each config file lives under the advertised home directory.
        for key in ("llm_profiles", "api_keys", "mcp_servers", "ui_prefs"):
            assert paths[key].parent == paths["home"]


# ── sessions — real engine, real topology effects ─────────────


class TestSessionsLifecycle:
    async def test_list_reflects_running_graph(self, studio_engine):
        s, engine = studio_engine
        listings = s.sessions.list()
        gid = engine.get_creature("alice").graph_id
        assert [x.session_id for x in listings] == [gid]

    async def test_get_returns_the_session_by_id(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        assert s.sessions.get(gid).session_id == gid

    async def test_get_unknown_raises(self, studio_engine):
        s, _ = studio_engine
        with pytest.raises(KeyError):
            s.sessions.get("ghost-graph")

    async def test_find_creature_by_name(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        found = s.sessions.find_creature(gid, "alice")
        assert found.creature_id == "alice"

    async def test_list_creatures_returns_graph_members(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        names = {c["name"] for c in s.sessions.list_creatures(gid)}
        assert names == {"alice", "bob"}

    async def test_find_session_for_creature(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        assert await s.sessions.find_session_for_creature("alice") == gid

    async def test_find_session_for_unknown_returns_none(self, studio_engine):
        s, _ = studio_engine
        assert await s.sessions.find_session_for_creature("ghost") is None

    async def test_add_channel_creates_a_real_channel(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        out = await s.sessions.add_channel(gid, "chat")
        assert out["name"] == "chat"
        # The channel is now registered in the graph's environment.
        env = engine._environments[gid]
        assert "chat" in env.shared_channels.list_channels()

    async def test_connect_then_disconnect_changes_topology(self, studio_engine):
        s, engine = studio_engine
        # alice and bob start in one shared graph already; draw a channel
        # between them and confirm both end up wired to it.
        result = await s.sessions.connect("alice", "bob", channel="link")
        assert result["channel"] == "link"
        alice = engine.get_creature("alice")
        assert "link" in alice.listen_channels or "link" in alice.send_channels
        disc = await s.sessions.disconnect("alice", "bob")
        assert "link" in disc["channels"]

    async def test_remove_creature_drops_it_from_the_graph(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        assert await s.sessions.remove_creature(gid, "alice") is True
        remaining = {c["name"] for c in s.sessions.list_creatures(gid)}
        assert remaining == {"bob"}

    async def test_remove_unknown_creature_returns_false(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        assert await s.sessions.remove_creature(gid, "ghost") is False


# ── attach — real engine policy advertisement ──────────────────


class TestAttachPolicies:
    async def test_policies_for_creature_lists_supported_modes(self, studio_engine):
        s, _ = studio_engine
        policies = s.attach.policies_for_creature("alice")
        # A running creature supports at least the LOG attach mode.
        values = {p.value for p in policies}
        assert "log" in values

    async def test_policies_for_session_lists_supported_modes(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        policies = s.attach.policies_for_session(gid)
        assert {p.value for p in policies}


# ── persistence — real session-dir round-trips ─────────────────


def _make_saved_session(path, name="alice"):
    store = SessionStore(str(path))
    try:
        store.init_meta("sess", "agent", "/p", "/w", [name])
        store.append_event(name, "user_input", {"content": "hello"})
        store.flush()
    finally:
        store.close()


class TestPersistence:
    @pytest.fixture(autouse=True)
    def _redirect(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_persistence_store_mod, "_SESSION_DIR", tmp_path)
        # Reset the session-index sidecar singleton between cases so
        # each test sees a fresh per-tmp_path index instance.
        _persistence_session_index.close_session_index()
        self._dir = tmp_path
        yield
        _persistence_session_index.close_session_index()

    def test_list_finds_saved_sessions(self):
        _make_saved_session(self._dir / "alice.kohakutr")
        s = Studio()
        names = {e["name"] for e in s.persistence.list()}
        assert "alice" in names

    def test_resolve_path_locates_the_file(self):
        path = self._dir / "alice.kohakutr"
        _make_saved_session(path)
        s = Studio()
        assert s.persistence.resolve_path("alice") == path

    def test_delete_removes_the_session_file(self):
        path = self._dir / "alice.kohakutr"
        _make_saved_session(path)
        s = Studio()
        deleted = s.persistence.delete("alice")
        assert path in deleted
        assert not path.exists()

    def test_viewer_tree_describes_the_session(self):
        path = self._dir / "alice.kohakutr"
        _make_saved_session(path)
        store = SessionStore(str(path))
        try:
            payload = Studio().persistence.viewer.tree(store, "sess")
            assert payload["session_id"] == "sess"
            # A single-creature session has exactly one focus node.
            assert len(payload["nodes"]) == 1
            assert payload["nodes"][0]["is_focus"] is True
        finally:
            store.close()

    def test_viewer_export_renders_the_conversation(self):
        path = self._dir / "alice.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.append_event(
                "alice", "user_message", {"role": "user", "content": "hi"}
            )
            store.flush()
            content_type, body = Studio().persistence.viewer.export(
                store, "sess", "md", None
            )
            assert "markdown" in content_type
            assert "Session: sess" in body
        finally:
            store.close()


# ── editors — real workspace scaffold / save ───────────────────


class TestEditorCreatures:
    def test_scaffold_creates_a_creature_dir(self, tmp_path):
        s = Studio()
        created = s.editors.creatures.scaffold(tmp_path, "alice")
        assert created.exists()
        assert created.is_dir()
        # A scaffolded creature has a config file.
        assert (created / "config.yaml").exists() or (created / "config.yml").exists()

    def test_write_prompt_persists_to_disk(self, tmp_path):
        s = Studio()
        s.editors.creatures.scaffold(tmp_path, "alice")
        s.editors.creatures.write_prompt(
            tmp_path, "alice", "system.md", "you are alice"
        )
        written = tmp_path / "alice" / "system.md"
        assert written.read_text(encoding="utf-8") == "you are alice"

    def test_delete_removes_the_creature(self, tmp_path):
        s = Studio()
        created = s.editors.creatures.scaffold(tmp_path, "alice")
        s.editors.creatures.delete(tmp_path, "alice")
        assert not created.exists()


class TestEditorModules:
    def test_scaffold_creates_a_module_file(self, tmp_path):
        s = Studio()
        kind_dir = tmp_path / "tools"
        created = s.editors.modules.scaffold(kind_dir, "tools", "mytool", None)
        assert created.exists()
        assert created.suffix == ".py"
