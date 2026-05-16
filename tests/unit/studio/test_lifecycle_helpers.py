"""Unit tests for the pure helpers in :mod:`kohakuterrarium.studio.sessions.lifecycle`.

The full ``start_creature`` / ``start_terrarium`` paths require a live
engine + recipe, so those are exercised in higher-tier tests; this
module focuses on the pure helpers and the in-memory metadata
bookkeeping.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.sessions import lifecycle

# ── _normalize_pwd ────────────────────────────────────────────


class TestNormalizePwd:
    def test_none_passthrough(self):
        assert lifecycle._normalize_pwd(None) is None

    def test_existing_dir(self, tmp_path):
        out = lifecycle._normalize_pwd(str(tmp_path))
        # Resolves and returns an absolute path.
        assert Path(out) == tmp_path.resolve()

    def test_missing_raises(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            lifecycle._normalize_pwd(str(tmp_path / "no-such"))

    def test_not_dir_raises(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            lifecycle._normalize_pwd(str(f))


# ── _now_iso ───────────────────────────────────────────────────


class TestNowIso:
    def test_format(self):
        from datetime import datetime, timezone

        out = lifecycle._now_iso()
        # Parses back to a tz-aware UTC datetime close to now.
        parsed = datetime.fromisoformat(out)
        assert parsed.tzinfo == timezone.utc
        delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        assert delta < 5


# ── _session_dir ───────────────────────────────────────────────


class TestSessionDir:
    def test_default(self, monkeypatch):
        # Verify the HOME-derived fallback: clear BOTH env vars (the
        # autouse fixture sets ``KT_CONFIG_DIR`` to a tmp).
        monkeypatch.delenv("KT_SESSION_DIR", raising=False)
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        expected = str(Path.home() / ".kohakuterrarium" / "sessions")
        assert lifecycle._session_dir() == expected

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KT_SESSION_DIR", "/custom")
        assert lifecycle._session_dir() == "/custom"


# ── get_session_meta / get_session_store / list_session_stores ─


class TestPureLookups:
    def test_get_session_meta_unknown(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_meta", {})
        assert lifecycle.get_session_meta("ghost") == {}

    def test_get_session_meta_returns_copy(self, monkeypatch):
        meta = {"k": "v"}
        monkeypatch.setattr(lifecycle, "_meta", {"g1": meta})
        out = lifecycle.get_session_meta("g1")
        out["k"] = "mutated"
        # Original unchanged.
        assert meta["k"] == "v"

    def test_get_session_store_none(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_session_stores", {})
        assert lifecycle.get_session_store("ghost") is None

    def test_list_session_stores(self, monkeypatch):
        stores = {"g1": object(), "g2": object()}
        monkeypatch.setattr(lifecycle, "_session_stores", stores)
        out = lifecycle.list_session_stores()
        assert len(out) == 2

    def test_list_session_stores_skips_none(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_session_stores", {"g1": object(), "g2": None})
        out = lifecycle.list_session_stores()
        assert len(out) == 1


# ── _apply_creature_name ──────────────────────────────────────


class _FakeExecutor:
    def __init__(self):
        self._agent_name = "old"


class _FakeManager:
    def __init__(self):
        self._agent_name = "old"


class _FakeAgent:
    def __init__(self, *, with_managers=True):
        self.config = SimpleNamespace(name="old")
        if with_managers:
            self.executor = _FakeExecutor()
            self.trigger_manager = _FakeManager()
            self.compact_manager = _FakeManager()
        else:
            self.executor = None
            self.trigger_manager = None
            self.compact_manager = None


class _FakeCreature:
    def __init__(self, *, with_agent=True, with_managers=True):
        self.name = "old"
        self.config = SimpleNamespace(name="old")
        self.agent = _FakeAgent(with_managers=with_managers) if with_agent else None


class TestApplyCreatureName:
    def test_no_agent(self):
        c = _FakeCreature(with_agent=False)
        lifecycle._apply_creature_name(c, "new")
        assert c.name == "new"
        assert c.config.name == "new"

    def test_full_chain(self):
        c = _FakeCreature()
        lifecycle._apply_creature_name(c, "new")
        assert c.name == "new"
        assert c.config.name == "new"
        assert c.agent.config.name == "new"
        assert c.agent.executor._agent_name == "new"
        assert c.agent.trigger_manager._agent_name == "new"
        assert c.agent.compact_manager._agent_name == "new"

    def test_missing_managers(self):
        c = _FakeCreature(with_managers=False)
        # Doesn't raise on None managers.
        lifecycle._apply_creature_name(c, "new")
        assert c.name == "new"

    def test_no_config_skips_config_assignment(self):
        c = _FakeCreature(with_agent=False)
        c.config = None
        # Doesn't raise even with no creature config.
        lifecycle._apply_creature_name(c, "new")
        assert c.name == "new"


# ── rename_session / rename_creature ──────────────────────────


class _FakeGraph:
    def __init__(self, gid, creatures=None):
        self.graph_id = gid
        self.creature_ids = set(creatures or [])


class _FakeEngine:
    def __init__(self, graphs=None, creatures=None):
        self._graphs = graphs or []
        self._creatures = creatures or {}

    def list_graphs(self):
        return list(self._graphs)

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]

    async def get_creature_info(self, cid):
        """Async service-shaped accessor — ``find_session_for_creature``
        now routes through the TerrariumService Protocol, so a stand-in
        for a service must expose this."""
        c = self._creatures.get(cid)
        if c is None:
            return None
        return SimpleNamespace(
            creature_id=cid,
            name=getattr(c, "name", cid),
            graph_id=c.graph_id,
        )


def _LocalService(engine):
    """Pass-through: ``as_engine`` returns its input unchanged when it
    doesn't structurally match TerrariumService (the engine itself).
    Wrap as a callable to keep call sites simple."""
    return engine


class TestRenameSession:
    def test_empty_name_raises(self):
        eng = _FakeEngine([_FakeGraph("g1", ["c1"])])
        with pytest.raises(ValueError, match="must not be empty"):
            lifecycle.rename_session(_LocalService(eng), "g1", "")

    def test_missing_session(self):
        eng = _FakeEngine([])
        with pytest.raises(KeyError):
            lifecycle.rename_session(_LocalService(eng), "ghost", "new")

    def test_solo_renames_creature(self, monkeypatch):
        c = _FakeCreature()
        c.graph_id = "g1"
        eng = _FakeEngine([_FakeGraph("g1", ["c1"])], creatures={"c1": c})
        # Need ``_build_session_handle`` to work — it calls
        # ``env.shared_channels.get_channel_info``.  Patch
        # ``_build_session_handle`` directly to a stub.
        from kohakuterrarium.studio.sessions.handles import Session

        monkeypatch.setattr(
            lifecycle,
            "_build_session_handle",
            lambda eng, sid: Session(session_id=sid, name="new"),
        )
        monkeypatch.setattr(lifecycle, "_meta", {})
        out = lifecycle.rename_session(_LocalService(eng), "g1", "new")
        # Creature was renamed.
        assert c.name == "new"
        assert out.name == "new"


class TestRenameCreature:
    def test_empty_name_raises(self):
        eng = _FakeEngine()
        with pytest.raises(ValueError):
            lifecycle.rename_creature(_LocalService(eng), "c1", "")

    def test_renames(self, monkeypatch):
        c = _FakeCreature()
        c.graph_id = "g1"
        c.get_status = lambda: {"creature_id": "c1", "name": "new"}
        eng = _FakeEngine(
            [_FakeGraph("g1", ["c1"])],
            creatures={"c1": c},
        )
        monkeypatch.setattr(lifecycle, "_meta", {"g1": {"name": "old"}})
        out = lifecycle.rename_creature(_LocalService(eng), "c1", "new")
        assert out == {"creature_id": "c1", "name": "new"}
        # Meta name mirrored because solo creature.
        assert lifecycle._meta["g1"]["name"] == "new"


# ── find_session_for_creature / find_creature ─────────────────


class TestFindSessionForCreature:
    async def test_known(self):
        c = SimpleNamespace(graph_id="g1")
        eng = _FakeEngine(creatures={"c1": c})
        out = await lifecycle.find_session_for_creature(_LocalService(eng), "c1")
        assert out == "g1"

    async def test_unknown(self):
        eng = _FakeEngine()
        out = await lifecycle.find_session_for_creature(_LocalService(eng), "ghost")
        assert out is None


# ── _build_session_handle ─────────────────────────────────────


class TestBuildSessionHandle:
    def test_missing_graph_raises(self):
        eng = _FakeEngine()
        # No ``_environments`` attribute → AttributeError.  Add it.
        eng._environments = {}
        with pytest.raises(KeyError):
            lifecycle._build_session_handle(eng, "ghost")

    def test_builds_handle(self, monkeypatch):
        c = SimpleNamespace(
            graph_id="g1",
            get_status=lambda: {"creature_id": "c1", "name": "alice"},
        )
        eng = _FakeEngine([_FakeGraph("g1", ["c1"])], creatures={"c1": c})
        eng._environments = {
            "g1": SimpleNamespace(
                shared_channels=SimpleNamespace(get_channel_info=lambda: [])
            )
        }
        monkeypatch.setattr(
            lifecycle, "_meta", {"g1": {"name": "alice", "config_path": "/x"}}
        )
        out = lifecycle._build_session_handle(eng, "g1")
        assert out.name == "alice"
        assert out.config_path == "/x"
        assert len(out.creatures) == 1
