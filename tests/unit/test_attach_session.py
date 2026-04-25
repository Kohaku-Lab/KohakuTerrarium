"""Wave F — attach / detach primitive roundtrip tests.

Covers the core :func:`kohakuterrarium.session.attach.attach_agent_to_session`
semantics without pulling in a full Agent runtime:

* Basic attach → emit events → detach cycle
* ``AlreadyAttachedError`` when attaching a second session
* Re-attach after detach bumps ``<attach_seq>``
* ``agent_attached`` / ``agent_detached`` lineage events appear in the
  host namespace with the documented field shape

Uses a stub ``Agent`` surface that mimics ``agent.config.name`` and
``agent.output_router`` — enough for the attach module to wire a
``SessionOutput`` secondary sink against.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.session.attach import (
    attach_agent_to_session,
    detach_agent_from_session,
    get_attach_state,
)
from kohakuterrarium.session.errors import (
    AlreadyAttachedError,
    NotAttachedError,
)
from kohakuterrarium.session.session import Session
from kohakuterrarium.session.store import SessionStore


class _StubOutput:
    """Minimal OutputModule shim so OutputRouter can be constructed."""

    async def start(self):
        pass

    async def stop(self):
        pass

    async def write(self, text):
        pass

    async def write_stream(self, chunk):
        pass

    async def flush(self):
        pass

    async def on_processing_start(self):
        pass

    async def on_processing_end(self):
        pass

    def on_activity(self, activity_type, detail):
        pass


def _make_stub_agent(name: str):
    """Build a duck-typed Agent stub the attach primitive accepts."""
    router = OutputRouter(default_output=_StubOutput(), named_outputs={})
    config = SimpleNamespace(name=name)
    agent = SimpleNamespace(
        config=config,
        output_router=router,
        session_store=None,
    )
    return agent


@pytest.fixture
def host_store(tmp_path):
    path = tmp_path / "attach_host.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="sess_attach_host",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["host"],
    )
    yield store
    try:
        store.close(update_status=False)
    except Exception:
        pass


@pytest.fixture
def host_session(host_store):
    host_agent = _make_stub_agent("host")
    return Session(host_store, agent=host_agent, name="host-session")


# ---------------------------------------------------------------------
# Basic attach / detach roundtrip
# ---------------------------------------------------------------------


def test_attach_writes_events_to_attached_namespace(host_session, host_store):
    helper = _make_stub_agent("helper")
    attach_agent_to_session(helper, host_session, role="helper")

    state = get_attach_state(helper)
    assert state is not None
    assert state["host"] == "host"
    assert state["role"] == "helper"
    assert state["attach_seq"] == 0
    assert state["prefix"] == "host:attached:helper:0"

    # Trigger the attached sink by forwarding a tool activity through
    # the helper's router — SessionOutput is registered as a secondary.
    helper.output_router.notify_activity(
        "tool_start",
        "[ls] helper running ls",
        metadata={"job_id": "helper-1", "args": {"cmd": "ls"}},
    )

    host_events = host_store.get_events("host")
    attached_events = host_store.get_events("host:attached:helper:0")
    # ``agent_attached`` lives in the host namespace; the tool_call
    # should be under the attached namespace.
    assert any(e["type"] == "agent_attached" for e in host_events)
    assert any(e["type"] == "tool_call" for e in attached_events)
    # Attached event carries the tool_call metadata we emitted.
    tool_evt = next(e for e in attached_events if e["type"] == "tool_call")
    assert tool_evt["call_id"] == "helper-1"


def test_detach_emits_lineage_and_clears_state(host_session, host_store):
    helper = _make_stub_agent("helper")
    attach_agent_to_session(helper, host_session, role="helper")
    detach_agent_from_session(helper)

    assert get_attach_state(helper) is None

    host_events = host_store.get_events("host")
    attached = [e for e in host_events if e["type"] == "agent_attached"]
    detached = [e for e in host_events if e["type"] == "agent_detached"]
    assert len(attached) == 1
    assert len(detached) == 1
    # Both lineage events share role + attach_seq.
    assert attached[0]["role"] == detached[0]["role"] == "helper"
    assert attached[0]["attach_seq"] == detached[0]["attach_seq"] == 0


def test_detach_not_attached_raises(host_session):
    helper = _make_stub_agent("helper")
    with pytest.raises(NotAttachedError):
        detach_agent_from_session(helper)


# ---------------------------------------------------------------------
# Constraint: one session per agent
# ---------------------------------------------------------------------


def test_attach_twice_same_session_is_idempotent(host_session):
    helper = _make_stub_agent("helper")
    attach_agent_to_session(helper, host_session, role="helper")
    # Idempotent: same session, no raise; state unchanged.
    attach_agent_to_session(helper, host_session, role="helper")
    state = get_attach_state(helper)
    assert state["attach_seq"] == 0


def test_attach_to_different_session_raises(tmp_path, host_session):
    other_path = tmp_path / "other.kohakutr.v2"
    other_store = SessionStore(other_path)
    other_store.init_meta(
        session_id="other",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["other_host"],
    )
    other_host_agent = _make_stub_agent("other_host")
    other_session = Session(other_store, agent=other_host_agent, name="other")
    try:
        helper = _make_stub_agent("helper")
        attach_agent_to_session(helper, host_session, role="helper")
        with pytest.raises(AlreadyAttachedError):
            attach_agent_to_session(helper, other_session, role="helper")
    finally:
        other_store.close(update_status=False)


# ---------------------------------------------------------------------
# Re-attach after detach bumps attach_seq
# ---------------------------------------------------------------------


def test_reattach_after_detach_bumps_attach_seq(host_session, host_store):
    helper = _make_stub_agent("helper")
    attach_agent_to_session(helper, host_session, role="helper")
    first_prefix = get_attach_state(helper)["prefix"]
    detach_agent_from_session(helper)

    attach_agent_to_session(helper, host_session, role="helper")
    second_state = get_attach_state(helper)
    assert second_state["attach_seq"] == 1
    assert second_state["prefix"] == "host:attached:helper:1"
    assert second_state["prefix"] != first_prefix

    # Different roles track their own attach_seq counter.
    other_helper = _make_stub_agent("memory_reader")
    attach_agent_to_session(other_helper, host_session, role="memory_reader")
    assert get_attach_state(other_helper)["attach_seq"] == 0


# ---------------------------------------------------------------------
# Lineage event shape
# ---------------------------------------------------------------------


def test_lineage_event_shape(host_session, host_store):
    helper = _make_stub_agent("helper")
    attach_agent_to_session(
        helper,
        host_session,
        role="helper",
        attached_by="test_harness",
    )

    host_events = host_store.get_events("host")
    attached_evt = next(e for e in host_events if e["type"] == "agent_attached")
    for field in (
        "agent_name",
        "role",
        "attached_by",
        "session_id",
        "attach_seq",
        "ts",
    ):
        assert field in attached_evt, f"missing field {field}"
    assert attached_evt["agent_name"] == "helper"
    assert attached_evt["role"] == "helper"
    assert attached_evt["attached_by"] == "test_harness"
    assert attached_evt["attach_seq"] == 0


# ---------------------------------------------------------------------
# Persistence: reopen store, confirm namespace survives
# ---------------------------------------------------------------------


def test_attached_events_survive_close_and_reopen(tmp_path):
    path = tmp_path / "survive.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="survive",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["host"],
    )
    try:
        host_agent = _make_stub_agent("host")
        session = Session(store, agent=host_agent)
        helper = _make_stub_agent("helper")
        attach_agent_to_session(helper, session, role="helper")
        helper.output_router.notify_activity(
            "tool_start",
            "[ls] listing",
            metadata={"job_id": "j1", "args": {}},
        )
        store.flush()
    finally:
        store.close(update_status=False)

    reopened = SessionStore(path)
    try:
        attached_events = reopened.get_events("host:attached:helper:0")
        assert any(e["type"] == "tool_call" for e in attached_events)
        # discover_agents_from_events excludes attached namespaces; the
        # dedicated discover_attached_agents surface exposes them.
        assert "host:attached:helper:0" not in reopened.discover_agents_from_events()
        attached_meta = reopened.discover_attached_agents()
        assert len(attached_meta) == 1
        assert attached_meta[0]["host"] == "host"
        assert attached_meta[0]["role"] == "helper"
        assert attached_meta[0]["attach_seq"] == 0
    finally:
        reopened.close(update_status=False)
