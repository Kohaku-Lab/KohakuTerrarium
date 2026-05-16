"""Unit tests for :mod:`kohakuterrarium.studio.sessions.creature_chat`."""

from types import SimpleNamespace


from kohakuterrarium.studio.sessions import creature_chat as chat_mod


class _FakeAgent:
    def __init__(
        self,
        *,
        regenerate_returns=None,
        edit_returns=True,
        rewind_called=None,
        conversation=None,
        events=None,
        live_jobs=None,
        processing=False,
        session_store=None,
        name="alice",
    ):
        self._regenerate_returns = regenerate_returns
        self._edit_returns = edit_returns
        self._rewind_called = rewind_called if rewind_called is not None else []
        self.conversation_history = conversation or []
        self._direct_job_meta = {jid: object() for jid in (live_jobs or [])}
        self._processing_task = "task" if processing else None
        self.session_store = session_store
        # Records of forwarded calls for assertion.
        self.regenerate_calls: list[dict] = []
        self.edit_calls: list[dict] = []

    async def regenerate_last_response(self, *, turn_index=None, branch_view=None):
        self.regenerate_calls.append(
            {"turn_index": turn_index, "branch_view": branch_view}
        )
        return self._regenerate_returns

    async def edit_and_rerun(
        self, idx, content, *, turn_index=None, user_position=None, branch_view=None
    ):
        self.edit_calls.append(
            {
                "idx": idx,
                "content": content,
                "turn_index": turn_index,
                "user_position": user_position,
                "branch_view": branch_view,
            }
        )
        return self._edit_returns

    async def rewind_to(self, idx):
        self._rewind_called.append(idx)


class _FakeCreature:
    def __init__(self, agent=None, name="alice", chat_chunks=None):
        self.agent = agent or _FakeAgent()
        self.name = name
        self._chunks = chat_chunks or ["hi"]

    async def chat(self, message):
        for c in self._chunks:
            yield c


class _FakeStore:
    def __init__(self, *, events=None, channel_messages=None, raise_on=None):
        self._events = events or []
        self._channel = channel_messages or []
        self._raise = raise_on or {}

    def get_resumable_events(self, agent_name, live_job_ids=None):
        if "get_resumable_events" in self._raise:
            raise self._raise["get_resumable_events"]
        return list(self._events)

    def get_channel_messages(self, channel):
        if "get_channel_messages" in self._raise:
            raise self._raise["get_channel_messages"]
        return list(self._channel)


# ── chat ──────────────────────────────────────────────────────


class _FakeService:
    """Stands in for a ``TerrariumService`` — records the chat call and
    streams the scripted chunks, the way ``LocalTerrariumService`` /
    ``MultiNodeTerrariumService`` do (routing by the creature's home
    node)."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.chat_calls: list[tuple[str, object]] = []

    def chat(self, creature_id, message):
        self.chat_calls.append((creature_id, message))

        async def _stream():
            for c in self._chunks:
                yield c

        return _stream()


class TestChat:
    async def test_streams_chunks_via_service(self):
        """``chat`` delegates to ``service.chat`` — the Protocol method
        that routes by the creature's home node — NOT host-engine
        ``find_creature`` resolution, so a worker-hosted creature is
        reachable too (regression guard for B-e2e-multinode-studio-1)."""
        service = _FakeService(["a", "b"])
        out = []
        async for chunk in chat_mod.chat(service, "g", "c", "hi"):
            out.append(chunk)
        assert out == ["a", "b"]
        # The creature_id + message were forwarded to the service.
        assert service.chat_calls == [("c", "hi")]


# ── regenerate ────────────────────────────────────────────────


class _ServiceWithMutators:
    """Records calls to ``regenerate`` / ``edit_message`` / ``rewind``,
    standing in for the routing surface that a worker-hosted creature
    needs (CF-11). Pre-CF-11 these helpers used ``as_engine(service)``
    and 404'd on worker creatures; the regression guard asserts the
    forwarded args land on the service Protocol, not on a host engine
    ``find_creature``."""

    def __init__(self, *, edit_result: bool = True) -> None:
        self.regenerate_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.rewind_calls: list[tuple[str, int]] = []
        self._edit_result = edit_result

    async def regenerate(self, creature_id, *, turn_index=None, branch_view=None):
        self.regenerate_calls.append(
            {
                "creature_id": creature_id,
                "turn_index": turn_index,
                "branch_view": branch_view,
            }
        )

    async def edit_message(
        self,
        creature_id,
        msg_idx,
        content,
        *,
        turn_index=None,
        user_position=None,
        branch_view=None,
    ):
        self.edit_calls.append(
            {
                "creature_id": creature_id,
                "msg_idx": msg_idx,
                "content": content,
                "turn_index": turn_index,
                "user_position": user_position,
                "branch_view": branch_view,
            }
        )
        return self._edit_result

    async def rewind(self, creature_id, msg_idx):
        self.rewind_calls.append((creature_id, msg_idx))


class TestRegenerate:
    async def test_forwards_turn_index_and_branch_view_via_service(self):
        """CF-11: regenerate must route through ``service.regenerate``
        with the creature_id, turn_index, and branch_view forwarded
        verbatim — host-engine ``find_creature`` would 404 on a
        worker-hosted creature."""
        service = _ServiceWithMutators()
        await chat_mod.regenerate(service, "g", "c", turn_index=2, branch_view={1: 1})
        assert service.regenerate_calls == [
            {"creature_id": "c", "turn_index": 2, "branch_view": {1: 1}}
        ]


# ── edit_message ──────────────────────────────────────────────


class TestEditMessage:
    async def test_returns_edit_result_and_forwards_args_via_service(self):
        """CF-11: edit_message must reach the worker via the service
        Protocol with every kwarg preserved."""
        service = _ServiceWithMutators(edit_result=True)
        out = await chat_mod.edit_message(
            service,
            "g",
            "c",
            3,
            "new content",
            turn_index=1,
            user_position=2,
            branch_view={0: 1},
        )
        assert out is True
        assert service.edit_calls == [
            {
                "creature_id": "c",
                "msg_idx": 3,
                "content": "new content",
                "turn_index": 1,
                "user_position": 2,
                "branch_view": {0: 1},
            }
        ]


# ── rewind ────────────────────────────────────────────────────


class TestRewind:
    async def test_calls_service_rewind(self):
        """CF-11: rewind routes through ``service.rewind`` so worker
        creatures aren't looked up against the host engine."""
        service = _ServiceWithMutators()
        await chat_mod.rewind(service, "g", "c", 5)
        assert service.rewind_calls == [("c", 5)]


# ── history ───────────────────────────────────────────────────


class TestHistoryCreature:
    def test_basic_via_session_store(self, monkeypatch):
        store = _FakeStore(events=[{"type": "user_message"}])
        agent = _FakeAgent(session_store=store, conversation=[{"role": "user"}])
        creature = _FakeCreature(agent=agent)
        monkeypatch.setattr(chat_mod, "find_creature", lambda eng, sid, cid: creature)
        out = chat_mod.history(SimpleNamespace(), "g", "alice")
        assert out["creature_id"] == "alice"
        assert out["events"] == [{"type": "user_message"}]
        assert out["messages"] == [{"role": "user"}]

    def test_no_session_store_falls_back_to_lifecycle_store(self, monkeypatch):
        agent = _FakeAgent(session_store=None)
        creature = _FakeCreature(agent=agent)
        monkeypatch.setattr(chat_mod, "find_creature", lambda eng, sid, cid: creature)
        fallback = _FakeStore(events=[{"type": "fallback"}])
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: fallback)
        # The creature's graph is resolved by a direct local engine walk
        # now (no ``find_session_for_creature`` indirection) — the fake
        # engine exposes a graph whose creature_ids include "alice".
        eng = SimpleNamespace(
            list_graphs=lambda: [SimpleNamespace(graph_id="g1", creature_ids={"alice"})]
        )
        out = chat_mod.history(eng, "g", "alice")
        assert out["events"] == [{"type": "fallback"}]

    def test_session_store_failure_falls_back(self, monkeypatch):
        store = _FakeStore(raise_on={"get_resumable_events": RuntimeError("dead")})
        agent = _FakeAgent(session_store=store)
        creature = _FakeCreature(agent=agent)
        monkeypatch.setattr(chat_mod, "find_creature", lambda eng, sid, cid: creature)
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: None)
        # No graph matches → the local walk falls back to session_id "g".
        eng = SimpleNamespace(list_graphs=lambda: [])
        out = chat_mod.history(eng, "g", "alice")
        # Initial fetch raised → events list became empty → no fallback found → still empty.
        assert out["events"] == []

    def test_is_processing(self, monkeypatch):
        agent = _FakeAgent(processing=True)
        creature = _FakeCreature(agent=agent)
        monkeypatch.setattr(chat_mod, "find_creature", lambda eng, sid, cid: creature)
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: None)
        eng = SimpleNamespace(list_graphs=lambda: [])
        out = chat_mod.history(eng, "g", "alice")
        assert out["is_processing"] is True

    def test_lifecycle_fallback_store_failure_yields_empty_events(self, monkeypatch):
        # No agent store at all → lifecycle fallback store is consulted,
        # but ITS get_resumable_events raises. The handler must swallow
        # the failure and surface an empty event list, not propagate.
        agent = _FakeAgent(session_store=None)
        creature = _FakeCreature(agent=agent)
        monkeypatch.setattr(chat_mod, "find_creature", lambda eng, sid, cid: creature)
        broken = _FakeStore(
            raise_on={"get_resumable_events": RuntimeError("fallback dead")}
        )
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: broken)
        eng = SimpleNamespace(
            list_graphs=lambda: [SimpleNamespace(graph_id="g1", creature_ids={"alice"})]
        )
        out = chat_mod.history(eng, "g", "alice")
        assert out["events"] == []


class TestChannelHistoryLastResortScan:
    def test_wildcard_session_scans_active_stores(self, monkeypatch):
        # The direct session store lookup misses (e.g. the "_" wildcard),
        # so _channel_history walks every live store and picks the first
        # one that actually holds the channel.
        empty = _FakeStore(channel_messages=[])
        holder = _FakeStore(
            channel_messages=[{"sender": "bob", "content": "hey", "ts": 7.0}]
        )
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: None)
        monkeypatch.setattr(chat_mod, "list_session_stores", lambda: [empty, holder])
        out = chat_mod.history(SimpleNamespace(), "_", "ch:chat")
        # The message from the holder store surfaces in the payload.
        assert len(out["events"]) == 1
        assert out["events"][0]["sender"] == "bob"
        assert out["events"][0]["content"] == "hey"

    def test_last_resort_scan_skips_stores_that_raise(self, monkeypatch):
        # A store in the registry whose get_channel_messages raises must
        # be skipped, not abort the scan — the next good store wins.
        broken = _FakeStore(raise_on={"get_channel_messages": RuntimeError("boom")})
        good = _FakeStore(
            channel_messages=[{"sender": "alice", "content": "ok", "ts": 1.0}]
        )
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: None)
        monkeypatch.setattr(chat_mod, "list_session_stores", lambda: [broken, good])
        out = chat_mod.history(SimpleNamespace(), "_", "ch:chat")
        assert len(out["events"]) == 1
        assert out["events"][0]["sender"] == "alice"

    def test_last_resort_scan_finds_nothing(self, monkeypatch):
        # No live store holds the channel → empty events, no error.
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: None)
        monkeypatch.setattr(
            chat_mod, "list_session_stores", lambda: [_FakeStore(channel_messages=[])]
        )
        out = chat_mod.history(SimpleNamespace(), "_", "ch:chat")
        assert out["events"] == []


class TestHistoryChannel:
    def test_no_store(self, monkeypatch):
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: None)
        out = chat_mod.history(SimpleNamespace(), "g", "ch:chat")
        assert out["creature_id"] == "ch:chat"
        assert out["events"] == []

    def test_with_messages(self, monkeypatch):
        store = _FakeStore(
            channel_messages=[{"sender": "alice", "content": "hi", "ts": 100.0}]
        )
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: store)
        out = chat_mod.history(SimpleNamespace(), "g", "ch:chat")
        assert len(out["events"]) == 1
        assert out["events"][0]["type"] == "channel_message"
        assert out["events"][0]["sender"] == "alice"

    def test_channel_lookup_failure(self, monkeypatch):
        store = _FakeStore(raise_on={"get_channel_messages": RuntimeError("boom")})
        monkeypatch.setattr(chat_mod, "get_session_store", lambda sid: store)
        out = chat_mod.history(SimpleNamespace(), "g", "ch:chat")
        # Empty events on failure.
        assert out["events"] == []


# ── branches ──────────────────────────────────────────────────


class TestBranches:
    def test_basic(self, monkeypatch):
        # Build an agent with branched events.
        store = _FakeStore(
            events=[
                {
                    "type": "user_message",
                    "event_id": 1,
                    "turn_index": 1,
                    "branch_id": 1,
                },
                {
                    "type": "user_message",
                    "event_id": 2,
                    "turn_index": 1,
                    "branch_id": 2,
                },
            ]
        )
        agent = _FakeAgent(session_store=store)
        creature = _FakeCreature(agent=agent)
        monkeypatch.setattr(chat_mod, "find_creature", lambda eng, sid, cid: creature)
        out = chat_mod.branches(SimpleNamespace(), "g", "alice")
        assert out["creature_id"] == "alice"
        # turn 1 has branches [1, 2].
        assert out["turns"][0]["branches"] == [1, 2]
