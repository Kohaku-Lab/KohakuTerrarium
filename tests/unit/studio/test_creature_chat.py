"""Unit tests for :mod:`kohakuterrarium.studio.sessions.creature_chat`."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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

    def __init__(
        self,
        *,
        regenerate_result: dict | None = None,
        edit_result: dict | None = None,
        history_result: dict | None = None,
        branches_result: dict | None = None,
    ) -> None:
        self.regenerate_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.rewind_calls: list[tuple[str, int]] = []
        self._regenerate_result = regenerate_result or {"ok": True}
        self._edit_result = edit_result or {"ok": True}
        self._history_result = history_result or {"events": []}
        self._branches_result = branches_result or []

    async def regenerate(
        self, creature_id, *, turn_index=None, branch_view=None, request_id=None
    ):
        self.regenerate_calls.append(
            {
                "creature_id": creature_id,
                "turn_index": turn_index,
                "branch_view": branch_view,
                "request_id": request_id,
            }
        )
        return self._regenerate_result

    async def edit_message(
        self,
        creature_id,
        msg_idx,
        content,
        *,
        turn_index=None,
        user_position=None,
        branch_view=None,
        request_id=None,
    ):
        self.edit_calls.append(
            {
                "creature_id": creature_id,
                "msg_idx": msg_idx,
                "content": content,
                "turn_index": turn_index,
                "user_position": user_position,
                "branch_view": branch_view,
                "request_id": request_id,
            }
        )
        return self._edit_result

    async def rewind(self, creature_id, msg_idx):
        self.rewind_calls.append((creature_id, msg_idx))

    async def chat_history(self, creature_id):
        return self._history_result

    async def chat_branches(self, creature_id):
        return self._branches_result


class TestRegenerate:
    async def test_forwards_turn_index_and_branch_view_via_service(self):
        """CF-11: regenerate must route through ``service.regenerate``
        with the creature_id, turn_index, and branch_view forwarded
        verbatim — host-engine ``find_creature`` would 404 on a
        worker-hosted creature."""
        service = _ServiceWithMutators()
        out = await chat_mod.regenerate(
            service,
            "g",
            "c",
            turn_index=2,
            branch_view={1: 1},
            request_id="regen-1",
        )
        assert out == {"ok": True}
        assert service.regenerate_calls == [
            {
                "creature_id": "c",
                "turn_index": 2,
                "branch_view": {1: 1},
                "request_id": "regen-1",
            }
        ]


# ── edit_message ──────────────────────────────────────────────


class TestEditMessage:
    async def test_returns_edit_result_and_forwards_args_via_service(self):
        """CF-11: edit_message must reach the worker via the service
        Protocol with every kwarg preserved."""
        service = _ServiceWithMutators(edit_result={"ok": True, "event_id": 7})
        out = await chat_mod.edit_message(
            service,
            "g",
            "c",
            3,
            "new content",
            turn_index=1,
            user_position=2,
            branch_view={0: 1},
            request_id="edit-1",
        )
        assert out == {"ok": True, "event_id": 7}
        assert service.edit_calls == [
            {
                "creature_id": "c",
                "msg_idx": 3,
                "content": "new content",
                "turn_index": 1,
                "user_position": 2,
                "branch_view": {0: 1},
                "request_id": "edit-1",
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
    async def test_returns_service_dto_unchanged(self):
        payload = {
            "creature_id": "alice",
            "events": [{"type": "user_message", "agent_id": "alice"}],
            "messages": [{"role": "user", "content": "hi"}],
            "is_processing": True,
        }
        service = _ServiceWithMutators(history_result=payload)

        out = await chat_mod.history(service, "remote-session", "alice")

        assert out is payload


class TestHistoryChannel:
    async def test_uses_service_boundary(self):
        service = SimpleNamespace(
            channel_history=AsyncMock(
                return_value=[{"sender": "alice", "content": "hi", "timestamp": 100.0}]
            )
        )
        out = await chat_mod.history(service, "g", "ch:chat")
        assert out["creature_id"] == "ch:chat"
        assert out["events"] == [
            {
                "type": "channel_message",
                "channel": "chat",
                "sender": "alice",
                "content": "hi",
                "ts": 100.0,
            }
        ]
        service.channel_history.assert_awaited_once_with("g", "chat")

    async def test_missing_channel_is_empty(self):
        service = SimpleNamespace(channel_history=AsyncMock(side_effect=KeyError("x")))
        out = await chat_mod.history(service, "g", "ch:chat")
        assert out["events"] == []


# ── branches ──────────────────────────────────────────────────


class TestBranches:
    async def test_returns_service_dto_unchanged(self):
        payload = [
            {
                "turn_index": 1,
                "branches": [
                    {"branch_id": 1},
                    {"branch_id": 2},
                ],
                "latest_branch": 2,
            }
        ]
        service = _ServiceWithMutators(branches_result=payload)

        out = await chat_mod.branches(service, "remote-session", "alice")

        assert out is payload
