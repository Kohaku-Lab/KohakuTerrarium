"""Unit tests for :mod:`kohakuterrarium.studio.sessions.store_attach`.

The attach body itself (mint / reuse / meta-append) is exercised end to
end through the ``lifecycle`` re-export in ``test_lifecycle_full.py``;
this file pins the pieces that live ONLY here: the session-dir resolver
and the retroactive channel-persistence walk.
"""

from types import SimpleNamespace

from kohakuterrarium.studio.sessions import store_attach
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class TestSessionDir:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "custom"))
        assert store_attach.session_dir() == str(tmp_path / "custom")

    def test_defaults_to_config_dir_sessions(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KT_SESSION_DIR", raising=False)
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "cfg"))
        out = store_attach.session_dir()
        assert out.endswith("sessions")
        assert str(tmp_path / "cfg") in out


class TestRetroInstall:
    async def test_walks_registered_channels(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            sid = t.get_creature("alice").graph_id
            # Should not raise — exercises the loop body.
            store_attach._retro_install_channel_persistence(t, sid)
        finally:
            await t.shutdown()

    def test_no_env_returns(self):
        engine = SimpleNamespace(_environments={})
        # No env for sid → early return.
        store_attach._retro_install_channel_persistence(engine, "ghost")


class TestReuseBranchIndexHook:
    """P0 regression pin — a store minted by the ENGINE (autosession)
    and merely REUSED by the studio attach must still be registered
    with the saved-sessions index sidecar, or the session never shows
    up in the saved list until a manual ``?refresh=true``."""

    def test_reuse_attaches_index_hook(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(
            store_attach._index_hooks,
            "attach",
            lambda sid, store, sess_dir: calls.append((sid, store)),
        )
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))

        existing = SimpleNamespace()  # engine-minted store stand-in
        agent = SimpleNamespace(
            config=SimpleNamespace(name="alice"),
            attach_session_store=lambda store: None,
        )
        creature = SimpleNamespace(
            graph_id="graph_1", creature_id="alice_1", agent=agent
        )
        engine = SimpleNamespace(
            _session_stores={"graph_1": existing}, _environments={}
        )
        monkeypatch.setattr(store_attach, "as_engine", lambda svc: engine)
        monkeypatch.setattr(store_attach, "stores_for", lambda svc: {})
        monkeypatch.setattr(
            store_attach._autosession,
            "register_agents_in_meta",
            lambda store, names: None,
        )

        store_attach.attach_session_store_for_creature(engine, creature)
        assert calls == [("graph_1", existing)]
