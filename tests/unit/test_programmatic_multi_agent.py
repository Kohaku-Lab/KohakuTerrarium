"""Wave F — programmatic multi-agent attach to one shared Session.

User code outside of Terrarium can build N agents and attach each to a
single :class:`Session`. Each agent's events land in its own attached
namespace under the host's key; nothing else in the Session changes.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.session.attach import (
    attach_agent_to_session,
    detach_agent_from_session,
    get_attach_state,
)
from kohakuterrarium.session.session import Session
from kohakuterrarium.session.store import SessionStore


class _StubOutput:
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


def _make_stub(name: str):
    router = OutputRouter(default_output=_StubOutput(), named_outputs={})
    return SimpleNamespace(
        config=SimpleNamespace(name=name),
        output_router=router,
        session_store=None,
    )


@pytest.fixture
def shared_session(tmp_path):
    path = tmp_path / "programmatic.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="sess_programmatic",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["host"],
    )
    host_agent = _make_stub("host")
    session = Session(store, agent=host_agent, name="shared")
    yield session, store, host_agent
    try:
        store.close(update_status=False)
    except Exception:
        pass


# ---------------------------------------------------------------------
# N agents attached to one session
# ---------------------------------------------------------------------


def test_two_agents_one_session_isolated_namespaces(shared_session):
    session, store, _host = shared_session
    writer = _make_stub("writer")
    critic = _make_stub("critic")
    attach_agent_to_session(writer, session, role="writer")
    attach_agent_to_session(critic, session, role="critic")

    # Both emit events; they should land in distinct namespaces.
    writer.output_router.notify_activity(
        "tool_start",
        "[write] writer starts",
        metadata={"job_id": "w-1", "args": {}},
    )
    critic.output_router.notify_activity(
        "tool_start",
        "[review] critic reviews",
        metadata={"job_id": "c-1", "args": {}},
    )

    store.flush()
    writer_events = store.get_events("host:attached:writer:0")
    critic_events = store.get_events("host:attached:critic:0")
    assert any(e.get("call_id") == "w-1" for e in writer_events)
    assert any(e.get("call_id") == "c-1" for e in critic_events)
    # Cross-contamination check.
    assert not any(e.get("call_id") == "c-1" for e in writer_events)
    assert not any(e.get("call_id") == "w-1" for e in critic_events)


def test_host_lineage_has_two_attach_events(shared_session):
    session, store, _host = shared_session
    a = _make_stub("alpha")
    b = _make_stub("beta")
    attach_agent_to_session(a, session, role="alpha")
    attach_agent_to_session(b, session, role="beta")

    host_events = store.get_events("host")
    attached = [e for e in host_events if e["type"] == "agent_attached"]
    assert len(attached) == 2
    roles = {e["role"] for e in attached}
    assert roles == {"alpha", "beta"}
    # Each agent gets attach_seq=0 in its own role namespace.
    assert all(e["attach_seq"] == 0 for e in attached)


def test_detach_one_leaves_the_other_running(shared_session):
    session, store, _host = shared_session
    a = _make_stub("alpha")
    b = _make_stub("beta")
    attach_agent_to_session(a, session, role="alpha")
    attach_agent_to_session(b, session, role="beta")
    detach_agent_from_session(a)

    # Beta is still attached and writes events fine.
    b.output_router.notify_activity(
        "tool_start", "[x] beta works", metadata={"job_id": "b1", "args": {}}
    )
    store.flush()
    beta_events = store.get_events("host:attached:beta:0")
    assert any(e.get("call_id") == "b1" for e in beta_events)

    assert get_attach_state(a) is None
    assert get_attach_state(b) is not None


def test_session_attach_agent_mirror_works(shared_session):
    """``session.attach_agent(agent, role)`` is a mirror of the agent API."""
    session, store, _host = shared_session
    a = _make_stub("alpha")
    session.attach_agent(a, role="alpha")
    state = get_attach_state(a)
    assert state is not None
    assert state["session"] is session
    assert state["prefix"] == "host:attached:alpha:0"

    session.detach_agent(a)
    assert get_attach_state(a) is None


def test_session_detach_agent_wrong_session_raises(shared_session, tmp_path):
    from kohakuterrarium.session.errors import NotAttachedError

    session, _store, _host = shared_session
    other_path = tmp_path / "other.kohakutr.v2"
    other_store = SessionStore(other_path)
    other_store.init_meta(
        session_id="other",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["other"],
    )
    other_host = _make_stub("other")
    other_session = Session(other_store, agent=other_host)

    try:
        a = _make_stub("alpha")
        session.attach_agent(a, role="alpha")
        # Detaching via the *wrong* session raises.
        with pytest.raises(NotAttachedError):
            other_session.detach_agent(a)
    finally:
        other_store.close(update_status=False)
