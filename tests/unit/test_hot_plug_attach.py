"""Wave F — hot-plug flow uses the public attach_to_session API.

The terrarium ``hotplug.py`` code path currently wires creatures via the
pre-Wave-F ``attach_session_store`` *internal* entry. The Wave F story
is that hot-plugged creatures can *also* use the public primitive
(``Agent.attach_to_session``) so programmatic hot-plug callers don't
need to reach into ``attach_session_store``.

This test exercises the hot-plug *scenario* against a shared store:
a creature is introduced mid-run and attaches via the new public API.
We keep the full ``TerrariumRuntime`` stack out of this fixture — the
semantic under test is "multiple creatures can share one store and
each one's events stay namespaced correctly," which is exactly what
the attach primitive promises.
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
    config = SimpleNamespace(name=name)
    return SimpleNamespace(config=config, output_router=router, session_store=None)


@pytest.fixture
def shared_store(tmp_path):
    path = tmp_path / "hotplug_attach.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="hotplug_attach",
        config_type="terrarium",
        config_path="/tmp/terrarium",
        pwd=str(tmp_path),
        agents=["root"],
        terrarium_name="team",
        terrarium_channels=[{"name": "tasks", "type": "queue"}],
        terrarium_creatures=[],
    )
    yield store
    try:
        store.close(update_status=False)
    except Exception:
        pass


def test_hot_plug_creature_attaches_via_public_api(shared_store):
    """A hot-plugged creature wires its events via attach_to_session.

    The session already has a ``root`` host; later a ``reviewer``
    creature is spawned and joins the session using the public API.
    Events from each land in their own namespace; no cross-contamination.
    """
    root_agent = _make_stub("root")
    session = Session(shared_store, agent=root_agent, name="root-session")

    # Root runs first — its events land under ``root:``.
    # (We stub the SessionOutput wiring by calling store directly
    # here; the attach test already covers the full sink path.)
    shared_store.append_event("root", "user_message", {"content": "start the team up"})

    # Mid-run: creature hot-plugged via the public API.
    reviewer = _make_stub("reviewer")
    reviewer.attach_to_session = lambda s, r: attach_agent_to_session(reviewer, s, r)
    reviewer.attach_to_session(session, "creature:reviewer")

    state = get_attach_state(reviewer)
    assert state is not None
    assert state["host"] == "root"
    assert state["role"] == "creature:reviewer"
    assert state["prefix"] == "root:attached:creature:reviewer:0"

    # Reviewer emits a tool activity; it lands under the attached ns.
    reviewer.output_router.notify_activity(
        "tool_start",
        "[review] reviewer inspects",
        metadata={"job_id": "rv-1", "args": {}},
    )

    root_events = shared_store.get_events("root")
    attached_events = shared_store.get_events("root:attached:creature:reviewer:0")
    assert any(e["type"] == "user_message" for e in root_events)
    assert any(e["type"] == "agent_attached" for e in root_events)
    assert any(e["type"] == "tool_call" for e in attached_events)

    # Hot-unplug: detach, confirm lineage event.
    detach_agent_from_session(reviewer)
    root_events = shared_store.get_events("root")
    detached = [e for e in root_events if e["type"] == "agent_detached"]
    assert len(detached) == 1
    assert detached[0]["role"] == "creature:reviewer"


def test_hot_plug_re_add_bumps_attach_seq(shared_store):
    """Hot-plug removing and re-adding the same creature gives distinct
    attach_seq slots, so the two runs don't collide in the store."""
    root_agent = _make_stub("root")
    session = Session(shared_store, agent=root_agent)

    reviewer1 = _make_stub("reviewer")
    attach_agent_to_session(reviewer1, session, role="creature:reviewer")
    assert get_attach_state(reviewer1)["attach_seq"] == 0
    detach_agent_from_session(reviewer1)

    # Re-add — typical hot-plug remove/re-add cycle.
    reviewer2 = _make_stub("reviewer")
    attach_agent_to_session(reviewer2, session, role="creature:reviewer")
    state2 = get_attach_state(reviewer2)
    assert state2["attach_seq"] == 1
    assert state2["prefix"] == "root:attached:creature:reviewer:1"


def test_discover_attached_agents_lists_every_hotplug_namespace(shared_store):
    """``discover_attached_agents`` returns one row per attach cycle."""
    root_agent = _make_stub("root")
    session = Session(shared_store, agent=root_agent)

    rv = _make_stub("reviewer")
    attach_agent_to_session(rv, session, role="creature:reviewer")
    # Trigger an event so the namespace is materialised on disk.
    rv.output_router.notify_activity(
        "tool_start", "[x] go", metadata={"job_id": "j", "args": {}}
    )
    detach_agent_from_session(rv)

    rv2 = _make_stub("reviewer")
    attach_agent_to_session(rv2, session, role="creature:reviewer")
    rv2.output_router.notify_activity(
        "tool_start", "[y] go2", metadata={"job_id": "k", "args": {}}
    )

    # Flush the event cache so ``discover_attached_agents`` sees the
    # most recent writes (events KVault uses a flush-interval cache).
    shared_store.flush()
    discovered = shared_store.discover_attached_agents()
    prefixes = {d["namespace"] for d in discovered}
    assert "root:attached:creature:reviewer:0" in prefixes
    assert "root:attached:creature:reviewer:1" in prefixes
    # discover_agents_from_events filters these out; only the host
    # namespace shows up in the primary list.
    primary = shared_store.discover_agents_from_events()
    assert "root" in primary
    assert all(":attached:" not in a for a in primary)
