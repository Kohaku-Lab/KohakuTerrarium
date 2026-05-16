"""Behavior tests for :class:`Studio`'s catalog / identity / editors /
persistence-viewer delegation forwarders.

``studio.py`` is a thin facade; these tests drive the *real*
collaborators through the ``Studio`` namespaces and assert the real
observable result — a workspace listing reflecting scaffolded files, a
schema parsed from real source, the bundled remote registry's shape,
an installed-package list, a viewer payload built from a real
``SessionStore``. No mocks of the delegated functions.
"""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace
from kohakuterrarium.studio.studio import Studio

# ── catalog.packages — real package dir / bundled registry ─────


class TestCatalogPackagesForward:
    def test_scan_returns_entries_for_cwd_creatures(self, tmp_path, monkeypatch):
        # scan_catalog merges packages + cwd/creatures. Run it from a
        # tmp cwd holding one *properly scaffolded* creature folder and
        # assert it surfaces as a "local"-sourced entry.
        s = Studio()
        s.editors.creatures.scaffold(tmp_path / "creatures", "scancreature")
        monkeypatch.chdir(tmp_path)
        entries = s.catalog.packages.scan()
        # The cwd creature is discovered, tagged source="local".
        local = [
            e
            for e in entries
            if getattr(e, "name", None) == "scancreature"
            and getattr(e, "source", None) == "local"
        ]
        assert len(local) == 1

    def test_remote_returns_the_bundled_registry_shape(self):
        # The bundled remote index always resolves to a dict with a
        # 'repos' list — the delegation must not reshape it.
        out = Studio().catalog.packages.remote()
        assert isinstance(out, dict)
        assert "repos" in out
        assert isinstance(out["repos"], list)

    def test_list_returns_installed_package_records(self):
        # Whatever is installed, the delegation returns the real
        # list_packages() result — a list of dict records.
        out = Studio().catalog.packages.list()
        assert isinstance(out, list)
        assert all(isinstance(p, dict) for p in out)

    def test_show_unknown_agent_path_reports_an_error_code(self):
        # load_agent_info returns ``(status, payload)``; an unresolvable
        # path yields a non-200 status, not an exception.
        status, _payload = Studio().catalog.packages.show("@nope/creatures/ghost")
        assert status != 200


# ── catalog.creatures / catalog.modules — real workspace ───────


@pytest.fixture
def workspace(tmp_path):
    """A real on-disk workspace rooted at tmp_path."""
    return LocalWorkspace(Path(tmp_path))


class TestCatalogCreaturesForward:
    def test_list_reflects_scaffolded_creatures(self, workspace):
        s = Studio()
        # Scaffold a creature through the editors namespace into the
        # workspace's own creatures dir, then list it through the
        # catalog namespace — both hit the same on-disk tree.
        s.editors.creatures.scaffold(workspace.creatures_dir, "alice")
        listing = s.catalog.creatures.list(workspace)
        assert any(c.get("name") == "alice" for c in listing)

    def test_get_returns_the_scaffolded_creature_config(self, workspace):
        s = Studio()
        s.editors.creatures.scaffold(workspace.creatures_dir, "alice")
        creature = s.catalog.creatures.get(workspace, "alice")
        assert creature["name"] == "alice"

    def test_read_prompt_returns_written_content(self, workspace):
        s = Studio()
        s.editors.creatures.scaffold(workspace.creatures_dir, "alice")
        s.editors.creatures.write_prompt(
            workspace.creatures_dir, "alice", "system.md", "hello from alice"
        )
        text = s.catalog.creatures.read_prompt(workspace, "alice", "system.md")
        assert text == "hello from alice"


class TestCatalogModulesForward:
    def test_list_reflects_scaffolded_modules(self, workspace):
        s = Studio()
        s.editors.modules.scaffold(
            workspace.module_kind_dir("tools"), "tools", "mytool", None
        )
        listing = s.catalog.modules.list(workspace, "tools")
        assert any(m.get("name") == "mytool" for m in listing)

    def test_get_returns_the_scaffolded_module(self, workspace):
        s = Studio()
        s.editors.modules.scaffold(
            workspace.module_kind_dir("tools"), "tools", "mytool", None
        )
        module = s.catalog.modules.get(workspace, "tools", "mytool")
        assert module["name"] == "mytool"

    def test_doc_returns_the_saved_documentation(self, workspace):
        s = Studio()
        created = s.editors.modules.scaffold(
            workspace.module_kind_dir("tools"), "tools", "mytool", None
        )
        s.editors.modules.save_doc(created, "## how to use mytool")
        doc = s.catalog.modules.doc(workspace, "tools", "mytool")
        assert "how to use mytool" in doc.get("content", "")
        assert doc["exists"] is True


# ── catalog.introspect.custom_schema — real AST parse ──────────


class TestCatalogIntrospectForward:
    def test_custom_schema_parses_real_source(self):
        s = Studio()
        schema = s.catalog.introspect.custom_schema(
            "class T:\n    def __init__(self, host: str, port: int = 8080):\n"
            "        pass\n",
            "T",
        )
        names = {p["name"] for p in schema["params"]}
        assert names == {"host", "port"}
        port = next(p for p in schema["params"] if p["name"] == "port")
        # The default literal is recovered from the AST.
        assert port["default"] == 8080


# ── identity.llm — real preset/profile catalog ─────────────────


class TestIdentityLlmModelForward:
    def test_list_models_combines_presets_and_profiles(self):
        models = Studio().identity.llm.list_models()
        assert isinstance(models, list)
        # The combined model list always surfaces the built-in presets.
        assert models, "expected at least the built-in model presets"
        assert all(isinstance(m, dict) for m in models)

    def test_list_native_tools_returns_records(self):
        native = Studio().identity.llm.list_native_tools()
        assert isinstance(native, list)
        assert all(isinstance(t, dict) for t in native)


class TestIdentityLlmProfileForward:
    @pytest.fixture(autouse=True)
    def _redirect(self, tmp_path, monkeypatch):
        # Identity stores resolve through ``config_dir()`` / KT_CONFIG_DIR
        # every call — the ``PROFILES_PATH`` constant is back-compat
        # display only and a setattr does NOT redirect the save path.
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))

    def test_save_profile_then_get_round_trips(self):
        s = Studio()
        s.identity.llm.save_profile("myprofile", "gpt-4o", "openai")
        # The saved profile is resolvable by its identifier.
        resolved = s.identity.llm.get_profile("myprofile")
        assert resolved is not None

    def test_delete_profile_removes_it(self):
        s = Studio()
        s.identity.llm.save_profile("myprofile", "gpt-4o", "openai")
        assert s.identity.llm.delete_profile("myprofile", "openai") is True
        # A second delete is a clean miss.
        assert s.identity.llm.delete_profile("myprofile", "openai") is False


# ── editors.modules — real scaffold / delete round-trip ────────


class TestEditorModulesForward:
    def test_scaffold_then_delete_round_trips_on_disk(self, tmp_path):
        s = Studio()
        kind_dir = tmp_path / "tools"
        created = s.editors.modules.scaffold(kind_dir, "tools", "mytool", None)
        assert created.exists()
        # delete_module(kind, name, path) — pass the explicit module path.
        s.editors.modules.delete("tools", "mytool", created)
        assert not created.exists()

    def test_save_doc_writes_documentation_file(self, tmp_path):
        s = Studio()
        kind_dir = tmp_path / "tools"
        created = s.editors.modules.scaffold(kind_dir, "tools", "mytool", None)
        # save_module_doc(py_path, content) — the doc sits beside the .py.
        s.editors.modules.save_doc(created, "## usage\nrun it")
        docs = list(kind_dir.rglob("*.md"))
        assert any("run it" in d.read_text(encoding="utf-8") for d in docs)


class TestEditorCreaturesSaveForward:
    def test_save_writes_the_creature_config_to_disk(self, tmp_path):
        s = Studio()
        creatures_dir = tmp_path / "creatures"
        s.editors.creatures.scaffold(creatures_dir, "alice")
        # The save body mirrors the API shape: {"config": {...}, ...}.
        # An edited config field must land on disk + read back through
        # the catalog.
        s.editors.creatures.save(
            creatures_dir,
            "alice",
            {"config": {"name": "alice", "version": "9.9", "skill_mode": "static"}},
        )
        ws = LocalWorkspace(Path(tmp_path))
        reloaded = s.catalog.creatures.get(ws, "alice")
        assert reloaded["config"]["version"] == "9.9"
        assert reloaded["config"]["skill_mode"] == "static"


# ── persistence.viewer — real SessionStore payloads ────────────


def _make_session(path, name="alice"):
    store = SessionStore(str(path))
    store.init_meta("sess", "agent", "/p", "/w", [name])
    store.append_event(name, "user_message", {"role": "user", "content": "hi"})
    store.append_event(
        name, "assistant_message", {"role": "assistant", "content": "yo"}
    )
    store.flush()
    return store


class TestPersistenceViewerForward:
    def test_summary_payload_describes_the_session(self, tmp_path):
        store = _make_session(tmp_path / "alice.kohakutr")
        try:
            payload = Studio().persistence.viewer.summary(store, "sess", "alice")
            # The summary forward returns a real dict payload for the
            # target, not an error.
            assert isinstance(payload, dict)
        finally:
            store.close()

    def test_turns_payload_is_built_from_the_store(self, tmp_path):
        store = _make_session(tmp_path / "alice.kohakutr")
        try:
            payload = Studio().persistence.viewer.turns(
                store,
                "sess",
                agent="alice",
                from_turn=None,
                to_turn=None,
                limit=50,
                offset=0,
            )
            assert isinstance(payload, dict)
        finally:
            store.close()

    def test_events_payload_is_built_from_the_store(self, tmp_path):
        store = _make_session(tmp_path / "alice.kohakutr")
        try:
            payload = Studio().persistence.viewer.events(
                store,
                "sess",
                agent="alice",
                turn_index=None,
                types=None,
                from_ts=None,
                to_ts=None,
                limit=50,
                cursor=None,
            )
            assert isinstance(payload, dict)
        finally:
            store.close()

    def test_diff_payload_compares_two_sessions(self, tmp_path):
        store_a = _make_session(tmp_path / "a.kohakutr")
        store_a.close()
        store_b = _make_session(tmp_path / "b.kohakutr")
        store_b.close()
        payload = Studio().persistence.viewer.diff(
            tmp_path / "a.kohakutr", tmp_path / "b.kohakutr", agent="alice"
        )
        assert isinstance(payload, dict)


class TestPersistenceHistoryForward:
    def test_history_index_lists_the_session_targets(self, tmp_path):
        path = tmp_path / "alice.kohakutr"
        store = _make_session(path)
        store.close()
        payload = Studio().persistence.history_index(path)
        # The index payload enumerates the session's history targets.
        assert isinstance(payload, dict)

    def test_history_returns_a_targets_slice(self, tmp_path):
        path = tmp_path / "alice.kohakutr"
        store = _make_session(path)
        store.close()
        payload = Studio().persistence.history(path, "alice")
        assert isinstance(payload, dict)
        assert payload.get("target") == "alice"

    def test_announce_migration_is_a_noop_for_unversioned_path(self, tmp_path):
        # announce_migration_if_needed only logs when a migration is
        # pending; for a path with no on-disk version files it must
        # return cleanly without raising.
        Studio().persistence.announce_migration(tmp_path / "nonexistent.kohakutr")
