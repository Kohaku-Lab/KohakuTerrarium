"""Unit tests for :mod:`kohakuterrarium.session.attachment_service`."""

import pytest

from kohakuterrarium.session.attachment_service import (
    _ATTACHED_STATE_ATTR,
    _attach_seq_state_key,
    _build_event_key_prefix,
    _emit_lineage,
    _host_agent_name,
    _next_attach_seq,
    attach_agent_to_session,
    detach_agent_from_session,
    get_attach_state,
)
from kohakuterrarium.session.errors import AlreadyAttachedError, NotAttachedError
from kohakuterrarium.session.session import Session
from kohakuterrarium.session.store import SessionStore

# ── fakes ─────────────────────────────────────────────────────────


class _FakeConfig:
    def __init__(self, name="alice"):
        self.name = name


class _FakeRouter:
    def __init__(self):
        self.secondary = []

    def add_secondary(self, output):
        self.secondary.append(output)

    def remove_secondary(self, output):
        if output in self.secondary:
            self.secondary.remove(output)


class _FakeAgent:
    def __init__(self, name="alice", router=True):
        self.config = _FakeConfig(name)
        self.output_router = _FakeRouter() if router else None


def _make_store(tmp_path, name="x.kohakutr") -> SessionStore:
    return SessionStore(str(tmp_path / name))


def _make_session(store, agent=None) -> Session:
    return Session(store, agent=agent)


# ── key helpers ───────────────────────────────────────────────────


class TestKeyHelpers:
    def test_attach_seq_state_key(self):
        assert _attach_seq_state_key("host", "rev") == "attach_seq:host:rev"

    def test_build_event_key_prefix(self):
        assert _build_event_key_prefix("host", "rev", 3) == "host:attached:rev:3"


# ── _host_agent_name ──────────────────────────────────────────────


class TestHostAgentName:
    def test_from_agent_config(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            agent = _FakeAgent("alpha")
            sess = _make_session(store, agent=agent)
            assert _host_agent_name(sess) == "alpha"
        finally:
            store.close()

    def test_from_meta_agents(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            store.init_meta("s", "agent", "/p", "/w", ["from_meta"])
            sess = _make_session(store, agent=None)
            assert _host_agent_name(sess) == "from_meta"
        finally:
            store.close()

    def test_fallback_default(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store, agent=None)
            # No meta, no agent → "host"
            assert _host_agent_name(sess) == "host"
        finally:
            store.close()


# ── _next_attach_seq ──────────────────────────────────────────────


class TestNextAttachSeq:
    def test_starts_at_zero(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            seq = _next_attach_seq(store, "host", "rev")
            assert seq == 0
        finally:
            store.close()

    def test_increments_persisted(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            assert _next_attach_seq(store, "host", "rev") == 0
            assert _next_attach_seq(store, "host", "rev") == 1
            assert _next_attach_seq(store, "host", "rev") == 2
        finally:
            store.close()

    def test_isolated_per_role(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            assert _next_attach_seq(store, "host", "rev") == 0
            assert _next_attach_seq(store, "host", "critic") == 0
        finally:
            store.close()


# ── _emit_lineage ─────────────────────────────────────────────────


class TestEmitLineage:
    def test_writes_event(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            _emit_lineage(
                store,
                "host",
                event_type="agent_attached",
                agent_name="alice",
                role="rev",
                attach_seq=0,
                attached_by="alice",
                session_id="s",
            )
            store.flush()
            events = store.get_events("host")
            assert len(events) == 1
            assert events[0]["type"] == "agent_attached"
            assert events[0]["agent_name"] == "alice"
            assert events[0]["role"] == "rev"
        finally:
            store.close()


# ── attach / detach happy path ────────────────────────────────────


class TestAttachAgentToSession:
    def test_attach_records_state(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            store.init_meta("s", "agent", "/p", "/w", ["host_a"])
            sess = _make_session(store, agent=None)
            agent = _FakeAgent("worker_a")
            attach_agent_to_session(agent, sess, "reviewer")
            state = get_attach_state(agent)
            assert state is not None
            assert state["session"] is sess
            # No agent on the session; host is the first entry in
            # meta["agents"] (the existing main creature).
            assert state["host"] == "host_a"
            assert state["role"] == "reviewer"
            assert state["attach_seq"] == 0
            assert state["prefix"] == "host_a:attached:reviewer:0"
        finally:
            store.close()

    def test_attach_no_store_raises(self):
        # Use plain object lacking ``store``.
        class _BadSession:
            agent = None
            store = None

        with pytest.raises(ValueError):
            attach_agent_to_session(_FakeAgent(), _BadSession(), "rev")

    def test_attach_re_attach_same_session_is_noop(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            # Second attach to same session — no error, no second router slot.
            attach_agent_to_session(agent, sess, "rev")
            assert len(agent.output_router.secondary) == 1
        finally:
            store.close()

    def test_attach_to_different_session_raises(self, tmp_path):
        store_a = _make_store(tmp_path, "a.kohakutr")
        store_b = _make_store(tmp_path, "b.kohakutr")
        try:
            sess_a = _make_session(store_a)
            sess_b = _make_session(store_b)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess_a, "rev")
            with pytest.raises(AlreadyAttachedError):
                attach_agent_to_session(agent, sess_b, "rev")
        finally:
            store_a.close()
            store_b.close()

    def test_attach_registers_secondary_on_router(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            assert len(agent.output_router.secondary) == 1
        finally:
            store.close()

    def test_attach_emits_lineage_event(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            store.flush()
            # Lineage is emitted under the *host* namespace (here:
            # the fallback "host" since the session has no agent and
            # no recorded meta agents).
            events = store.get_events("host")
            assert any(e["type"] == "agent_attached" for e in events)
        finally:
            store.close()

    def test_attach_with_attached_by_argument(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev", attached_by="caller")
            store.flush()
            events = [
                e for e in store.get_events("host") if e["type"] == "agent_attached"
            ]
            assert events[0]["attached_by"] == "caller"
        finally:
            store.close()

    def test_attach_sets_viewer_default_agent(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            # The viewer default points to the attach namespace
            # (host fallback is "host" since session has no meta).
            assert store.meta.get("viewer_default_agent") == "host:attached:rev:0"
        finally:
            store.close()


class TestDetachAgentFromSession:
    def test_detach_without_attach_raises(self):
        with pytest.raises(NotAttachedError):
            detach_agent_from_session(_FakeAgent())

    def test_detach_removes_state(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            assert get_attach_state(agent) is not None
            detach_agent_from_session(agent)
            assert get_attach_state(agent) is None
        finally:
            store.close()

    def test_detach_removes_secondary_from_router(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            assert len(agent.output_router.secondary) == 1
            detach_agent_from_session(agent)
            assert agent.output_router.secondary == []
        finally:
            store.close()

    def test_detach_emits_lineage_event(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            detach_agent_from_session(agent)
            store.flush()
            events = [
                e for e in store.get_events("host") if e["type"] == "agent_detached"
            ]
            assert len(events) == 1
        finally:
            store.close()


class TestGetAttachState:
    def test_returns_none_when_unattached(self):
        assert get_attach_state(_FakeAgent()) is None

    def test_returns_copy_not_live_dict(self, tmp_path):
        store = _make_store(tmp_path)
        try:
            sess = _make_session(store)
            agent = _FakeAgent()
            attach_agent_to_session(agent, sess, "rev")
            state = get_attach_state(agent)
            state["host"] = "MUTATED"
            # Internal state unchanged.
            assert getattr(agent, _ATTACHED_STATE_ATTR)["host"] != "MUTATED"
        finally:
            store.close()
