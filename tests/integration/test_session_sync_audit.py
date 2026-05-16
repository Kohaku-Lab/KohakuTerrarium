"""Audit tests for the session sync + persistence layer.

Hypothesizes bugs from code reading and pins each one with a failing
test.  Where practical we drive a focused unit-style test directly
against the affected helper (SessionEventTee, SessionMirrorWriter,
session_coord.apply_merge, TerrariumSessionAdapter._op_resume); a full
RealLabHost+RealLabWorker roundtrip is reserved for the bugs that only
manifest across the wire (no-backfill on disconnect).
"""

import asyncio
from pathlib import Path

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.sync import (
    NAMESPACE,
    SessionEventTee,
    SessionMirrorWriter,
)
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.terrarium.session_coord import apply_merge
from kohakuterrarium.terrarium.topology import TopologyDelta

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Minimal LabNotifier / LabRegistrar stand-ins.
# ---------------------------------------------------------------------------


class _RecordingNode:
    """A LabNotifier+LabRegistrar fake that records every ``notify``.

    Lets a test drive a ``SessionEventTee`` directly: tee.attach() →
    feed events through the store → tee notifies us → we record the
    body so the test can assert what would have crossed the wire.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.notified: list[tuple[str, dict]] = []
        self.registered: dict = {}
        self.unregistered: list[str] = []
        self.fail = fail
        self.client_id = "_host"

    def register_app_extension(self, namespace, handler):
        self.registered[namespace] = handler

    def unregister_app_extension(self, namespace):
        self.unregistered.append(namespace)
        self.registered.pop(namespace, None)

    async def notify(self, *, to_node, namespace, type, body):
        if self.fail:
            raise RuntimeError("link down")
        self.notified.append((type, body))


# ---------------------------------------------------------------------------
# BUG candidate A: mirror append re-stamps event_id, so the mirror's
# event_id never matches the worker's event_id.  Callers using event_id
# as a stable identifier across worker↔mirror (since-filter, jump-to)
# get wrong rows.
# ---------------------------------------------------------------------------


class TestMirrorEventIdDivergence:
    async def test_mirror_event_id_matches_worker(self, tmp_path):
        """The worker's per-event event_id should be carried over into
        the mirror store verbatim.  Today ``SessionStore.append_event``
        overwrites ``data['event_id']`` with its own local counter when
        the mirror dispatches the wire event, so the mirror's
        ``event_id`` is independent of the worker's — breaking every
        ``since=event_id`` filter that mixes worker and mirror data.
        """
        worker_store = SessionStore(tmp_path / "worker.kohakutr")
        # Burn a few event_ids so the worker's next event isn't 1.
        for _ in range(5):
            worker_store.append_event("alice", "noise", {"content": "x"})
        # The next worker event uses event_id == 7 (1..5 used + 6 was
        # never appended; we set this up so worker_id != mirror_id even
        # if both stores start counting at 1 separately).
        worker_key, worker_eid = worker_store.append_event(
            "alice", "user_message", {"content": "load-bearing"}
        )
        assert worker_eid == 6  # invariant for this test setup

        node = _RecordingNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            # Hand-deliver the same payload the SessionEventTee would
            # send for that single event — same key + data shape.
            data = {k: v for k, v in worker_store.events[worker_key].items()}
            msg = AppMessage(
                namespace=NAMESPACE,
                type="event",
                body={
                    "session_id": "sess-1",
                    "key": worker_key,
                    "data": data,
                },
                sender_node="worker-1",
                request_id=None,
                in_reply_to=None,
            )
            await writer._dispatch(msg)

            mirror_store = writer.store_for("sess-1")
            mirror_events = mirror_store.get_events("alice")
            assert len(mirror_events) == 1
            # The mirror MUST preserve the worker's event_id.  Today it
            # re-stamps with the mirror's local counter (which starts
            # at 1) — so this assertion fails with mirror_eid==1.
            assert mirror_events[0]["event_id"] == worker_eid, (
                f"mirror event_id {mirror_events[0]['event_id']!r} != "
                f"worker event_id {worker_eid!r}; the mirror's event_id "
                "is its own local counter, not the worker's"
            )
        finally:
            writer.close()


# ---------------------------------------------------------------------------
# BUG candidate B: SessionEventTee drops events on a notify failure.
# When the host link is briefly down, queue-side back-pressure is by
# design (best-effort), but there's no backfill on reconnect — the
# mirror permanently misses every event that was in flight while the
# link was down, even though those events sit durably on the worker.
# ---------------------------------------------------------------------------


class TestNoBackfillAfterTransientLinkDown:
    async def test_events_during_link_down_never_reach_mirror(self, tmp_path):
        """Worker appends N events while the link is failing → those
        events stay on the worker but never make it to the mirror.
        A subsequent reconnect attaches a NEW tee but does NOT replay
        history.  The mirror is permanently shorter than the worker.

        This is the chat-history continuity bug: a user opening the
        host's history view sees a gap that doesn't exist on the worker.
        """
        worker_store = SessionStore(tmp_path / "worker.kohakutr")
        # First: with a healthy link, two events make it through.
        node = _RecordingNode()
        tee = SessionEventTee("sess-1", worker_store, node)
        tee.attach()
        worker_store.append_event("alice", "user_message", {"content": "a"})
        worker_store.append_event("alice", "user_message", {"content": "b"})
        await asyncio.sleep(0.05)
        # 1 meta + 2 events through; record them.
        baseline = [t for t, _ in node.notified if t == "event"]
        assert len(baseline) == 2

        # Now: link goes down — the pump's notify will raise.
        node.fail = True
        worker_store.append_event("alice", "user_message", {"content": "gap-1"})
        worker_store.append_event("alice", "user_message", {"content": "gap-2"})
        await asyncio.sleep(0.05)

        # Link recovers.  The tee currently never re-emits the gap
        # events — no backfill mechanism exists.
        node.fail = False
        worker_store.append_event("alice", "user_message", {"content": "c"})
        await asyncio.sleep(0.05)

        recorded_contents = [
            body["data"].get("content") for t, body in node.notified if t == "event"
        ]
        tee.detach()

        # Worker durably has every event.
        worker_events = worker_store.get_events("alice")
        worker_contents = [e["content"] for e in worker_events]
        assert worker_contents == ["a", "b", "gap-1", "gap-2", "c"]

        # The mirror SHOULD see the same five.  Today gap-1/gap-2 are
        # silently lost — the pump consumes them off the queue, notify
        # raises, the items are not re-queued.
        assert recorded_contents == ["a", "b", "gap-1", "gap-2", "c"], (
            f"link-down events lost forever: mirror saw {recorded_contents}, "
            f"worker has {worker_contents}"
        )


# ---------------------------------------------------------------------------
# BUG candidate C: _op_resume on the worker adopts a session but never
# subscribes a SessionEventTee on the resumed creature(s).  Future
# events emitted by the resumed creature reach the worker's store but
# never the mirror.
# ---------------------------------------------------------------------------


class TestResumeOnWorkerDoesNotTeeFutureEvents:
    async def test_op_resume_does_not_install_tee(self, tmp_path):
        """``TerrariumSessionAdapter._op_resume`` calls
        ``engine.adopt_session(...)`` directly — it does NOT route
        through ``TerrariumRuntimeAdapter`` and therefore never calls
        ``WorkerSessionAttacher.attach(creature_id)``.  The resumed
        graph has no Tee installed; subsequent ``append_event`` on the
        adopted store never reaches the host's mirror.
        """
        from kohakuterrarium.laboratory.adapters._worker_session import (
            WorkerSessionAttacher,
        )

        engine = Terrarium(session_dir=str(tmp_path / "worker-sessions"))
        node = _RecordingNode()
        attacher = WorkerSessionAttacher(
            engine, node, session_dir=tmp_path / "worker-sessions"
        )

        # Pre-create a .kohakutr file ready to adopt.  We simulate a
        # pushed mirror file: meta + one historical event.
        seed_path = tmp_path / "seed.kohakutr"
        seed = SessionStore(str(seed_path))
        seed.init_meta(
            session_id="seed-graph",
            config_type="agent",
            config_path=str(_write_dummy_agent_cfg(tmp_path, "alice")),
            pwd=str(tmp_path),
            agents=["alice"],
        )
        seed.append_event("alice", "user_message", {"content": "seed"})
        seed.flush()
        seed.close()

        # Install the SessionAdapter, then drive its resume path
        # directly (bypassing the wire).
        from kohakuterrarium.laboratory.adapters.terrarium_session import (
            TerrariumSessionAdapter,
        )

        adapter = TerrariumSessionAdapter(engine, node)
        try:
            # _op_resume goes through engine.adopt_session, but we
            # cannot fully drive adopt_session without an LLM seam +
            # an on-disk agent config that bootstrap can build.  The
            # interesting invariant is testable without the full
            # roundtrip: a freshly-adopted creature should appear in
            # ``attacher`` so its events flow to the controller.
            # If adopt_session DID call attacher.attach() the
            # invariant would hold even without going through
            # _op_resume; the bug is that nobody calls it.
            #
            # Simulate the engine's post-adopt state minimally:
            # _op_resume returns a session_id but no Tee is registered
            # under attacher's bookkeeping.
            assert attacher._graph_tees == {}, "starting precondition: no Tee installed"
            # We synthesise the adopted-session state the way
            # adopt_session would leave it — a SessionStore in
            # engine._session_stores keyed by graph_id.
            adopted_store = SessionStore(str(seed_path))
            engine._session_stores["seed-graph"] = adopted_store

            # The post-resume invariant we want: attacher.attach was
            # called for every creature in the adopted graph, OR
            # _op_resume itself installs a Tee.  Today neither happens.
            assert attacher._graph_tees.get("seed-graph") is not None, (
                "after _op_resume the worker's resumed graph has no "
                "SessionEventTee — future events on the adopted store "
                "never reach the host mirror"
            )
        finally:
            adapter.detach()


def _write_dummy_agent_cfg(root: Path, name: str) -> Path:
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "config.yaml").write_text(
        f"name: {name}\n"
        "system_prompt: hello\n"
        "llm_profile: openai/gpt-4-test\n"
        "model: gpt-4\nprovider: openai\n"
        "input:\n  type: cli\noutput:\n  type: stdout\n",
        encoding="utf-8",
    )
    return cdir / "config.yaml"


# ---------------------------------------------------------------------------
# BUG candidate D: session_coord.apply_merge opens a SECOND
# SessionStore on the kept-graph's existing file (because
# new_graph_ids[0] == one of the old_graph_ids when ``connect`` merges
# two pre-existing persisted graphs), then copies old_stores[0]'s
# events into it.  Result: every event in the kept graph appears
# TWICE in the merged store.
# ---------------------------------------------------------------------------


class TestApplyMergeDuplicatesKeptGraphEvents:
    def test_keep_graph_events_not_duplicated(self, tmp_path):
        """A merge of graph ``a`` (with one event) and graph ``b``
        (with one event) into kept graph ``a`` must produce exactly
        two events under the merged store.

        Today ``apply_merge`` opens a new SessionStore at the kept
        graph's existing path and calls ``copy_events_into`` — which
        APPENDS via ``append_event`` — for every old store including
        ``a`` itself.  The result is ``a``'s events written into
        ``a``'s file a second time (three events end up in the merged
        store, not two).
        """
        session_dir = tmp_path / "engine-sessions"
        session_dir.mkdir()

        # Stand up a minimal Terrarium with a session_dir so
        # ``_store_path_for`` returns a real Path (the bug only fires
        # on the persisted path).  No creatures: we drive the merge
        # via session_coord directly with a hand-built TopologyDelta.
        engine = Terrarium(session_dir=str(session_dir))

        # Two persisted stores at paths matching their graph_ids.
        store_a = SessionStore(str(session_dir / "graph_a.kohakutr"))
        store_a.append_event("alice", "user_message", {"content": "from-a"})
        store_a.flush()
        store_b = SessionStore(str(session_dir / "graph_b.kohakutr"))
        store_b.append_event("bob", "user_message", {"content": "from-b"})
        store_b.flush()

        engine._session_stores["graph_a"] = store_a
        engine._session_stores["graph_b"] = store_b

        delta = TopologyDelta(
            kind="merge",
            old_graph_ids=["graph_a", "graph_b"],
            new_graph_ids=["graph_a"],
            affected_creatures=set(),
        )
        apply_merge(engine, delta)

        merged = engine._session_stores["graph_a"]
        alice_events = merged.get_events("alice")
        bob_events = merged.get_events("bob")
        # Each pre-merge agent should appear exactly once.  Today
        # alice's event is written twice into the same file (the merge
        # opens a second wrapper on graph_a's existing path).
        assert len(alice_events) == 1, (
            f"alice events duplicated after merge: {len(alice_events)} rows "
            f"(expected 1).  apply_merge copies the kept-graph store "
            f"into a SessionStore opened on the same path."
        )
        assert len(bob_events) == 1, (
            f"bob events count after merge: {len(bob_events)} rows " f"(expected 1)"
        )


# ---------------------------------------------------------------------------
# BUG candidate E: _op_history on the worker has a stale-event_id
# filter.  The adapter accepts ``since: int`` and filters local events
# by ``event_id > since``.  But ``since`` comes from the controller,
# which read it from the MIRROR (where event_ids are re-stamped — see
# bug A).  So a ``since`` value the controller derives from its
# mirror's event_id never lines up with the worker's event_ids,
# producing either silent under-fetch or over-fetch.
# ---------------------------------------------------------------------------


class TestHistorySinceFilterIsCrossNodeMeaningful:
    async def test_since_filter_uses_consistent_event_id(self, tmp_path):
        """If the mirror records a worker event with event_id=K_mirror,
        then a follow-up ``terrarium.session/history`` request with
        ``since=K_mirror`` from the controller MUST return only the
        events the worker has with event_id > K_mirror as seen on the
        controller side — i.e. the two views must agree on what
        event_id labels each row.

        Today they don't: the worker's event_id and the mirror's
        event_id are independent monotonic counters.
        """
        # Worker side: append 3 events.
        worker_store = SessionStore(tmp_path / "worker.kohakutr")
        # Simulate the meta init the worker-session attacher would do.
        worker_store.init_meta(
            session_id="sess-1",
            config_type="agent",
            config_path="/cfg",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        # Mirror writer + a hand-fed dispatch of every worker event.
        node = _RecordingNode()
        writer = SessionMirrorWriter(node, tmp_path / "mirror")
        try:
            for content in ("e1", "e2", "e3"):
                key, _ = worker_store.append_event(
                    "alice", "user_message", {"content": content}
                )
                data = dict(worker_store.events[key])
                msg = AppMessage(
                    namespace=NAMESPACE,
                    type="event",
                    body={"session_id": "sess-1", "key": key, "data": data},
                    sender_node="worker-1",
                    request_id=None,
                    in_reply_to=None,
                )
                await writer._dispatch(msg)

            mirror_store = writer.store_for("sess-1")
            mirror_events = mirror_store.get_events("alice")
            worker_events = worker_store.get_events("alice")

            # Cross-node invariant: the two stores agree on event_id
            # labeling so a since-filter works.  This is the
            # foundational shape ``_op_history``'s ``since`` parameter
            # depends on.
            mirror_ids = [e["event_id"] for e in mirror_events]
            worker_ids = [e["event_id"] for e in worker_events]
            assert mirror_ids == worker_ids, (
                f"mirror event_ids {mirror_ids} disagree with worker "
                f"event_ids {worker_ids}; ``since=event_id`` from the "
                "controller side cannot reach the right worker row"
            )
        finally:
            writer.close()
