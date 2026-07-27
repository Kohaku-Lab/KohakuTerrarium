"""Graph-bound Laboratory output-wire routing tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_output_wire import (
    TerrariumOutputWireAdapter,
)


class _Messenger:
    def __init__(self, node_id: str, *, response=None):
        self.node_id = node_id
        self.response = response or {"delivered": True}
        self.request = AsyncMock(return_value=self.response)
        self.handlers = {}

    def register_handler(self, namespace, handler):
        self.handlers[namespace] = handler

    def unregister_handler(self, namespace):
        self.handlers.pop(namespace, None)


class _Agent:
    def __init__(self):
        self._running = True
        self._process_event = AsyncMock()


class _Creature:
    def __init__(self, cid: str, name: str, graph_id: str):
        self.creature_id = cid
        self.name = name
        self.graph_id = graph_id
        self.agent = _Agent()


def _msg(sender: str, body: dict) -> AppMessage:
    return AppMessage(
        sender_node=sender,
        namespace="terrarium.output_wire",
        type="inject",
        body=body,
        request_id="req-1",
        in_reply_to=None,
    )


def _body(**overrides):
    body = {
        "target_name": "target-id",
        "event": {
            "source": "source-id",
            "content": "hello",
            "metadata": {},
        },
        "source_creature_id": "source-id",
        "source_graph_id": "graph-a",
        "hop": 0,
        "peer": "worker-a",
    }
    body.update(overrides)
    return body


class TestLifecycle:
    def test_attach_and_detach(self):
        messenger = _Messenger("worker-a")
        engine = SimpleNamespace(_output_wire_adapter=None, _creatures={})
        adapter = TerrariumOutputWireAdapter(engine, messenger)
        assert engine._output_wire_adapter is adapter
        assert "terrarium.output_wire" in messenger.handlers
        adapter.detach()
        assert engine._output_wire_adapter is None
        assert "terrarium.output_wire" not in messenger.handlers


class TestGraphScopedCache:
    def test_live_cache_reference_survives_refresh(self):
        messenger = _Messenger("_host")
        cache = {}
        adapter = TerrariumOutputWireAdapter(
            SimpleNamespace(_creatures={}), messenger, name_cache=cache
        )
        cache["worker"] = {("graph-a", "node-a", "id-a")}
        assert adapter.peer_for_target("worker", graph_id="graph-a") == "node-a"

    def test_duplicate_name_is_not_last_writer(self):
        messenger = _Messenger("_host")
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        adapter.set_name_cache(
            {
                "worker": {
                    ("graph-a", "node-a", "id-a"),
                    ("graph-b", "node-b", "id-b"),
                }
            }
        )
        assert adapter.peer_for_target("worker") is None
        assert adapter.peer_for_target("worker", graph_id="graph-a") == "node-a"


class TestForwardEnvelope:
    async def test_requires_source_graph_and_runtime_id(self):
        messenger = _Messenger("worker-a")
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        assert await adapter.forward_event(target_name="target-id", event={}) is False
        messenger.request.assert_not_awaited()

    async def test_request_ack_controls_delivered_result(self):
        messenger = _Messenger("worker-a", response={"delivered": False})
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        delivered = await adapter.forward_event(
            target_name="target-id",
            event={"content": "hello"},
            source_creature_id="source-id",
            source_graph_id="graph-a",
        )
        assert delivered is False
        destination, msg_type, body = messenger.request.await_args.args
        assert (destination, msg_type) == ("_host", "inject")
        assert body["source_creature_id"] == "source-id"
        assert body["source_graph_id"] == "graph-a"
        assert body["hop"] == 0
        assert body["peer"] == "worker-a"


class TestHostRelay:
    async def test_relay_propagates_receiver_failure(self):
        messenger = _Messenger("_host", response={"delivered": False})
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        adapter.set_name_cache(
            {
                "source-id": {("graph-a", "worker-a", "source-id")},
                "target-id": {("graph-a", "worker-b", "target-id")},
            }
        )
        result = await adapter._dispatch(_msg("worker-a", _body()))
        assert result == {"delivered": False}
        destination, msg_type, body = messenger.request.await_args.args
        assert (destination, msg_type) == ("worker-b", "inject")
        assert body["target_graph_id"] == "graph-a"
        assert body["target_creature_id"] == "target-id"
        assert body["hop"] == 1
        assert body["peer"] == "_host"

    async def test_forged_source_identity_fails_closed(self):
        messenger = _Messenger("_host")
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        adapter.set_name_cache(
            {
                "source-id": {("graph-a", "worker-a", "source-id")},
                "target-id": {("graph-a", "worker-b", "target-id")},
            }
        )
        result = await adapter._dispatch(
            _msg("worker-forged", _body(peer="worker-forged"))
        )
        assert result == {"delivered": False, "error": "forged source identity"}
        messenger.request.assert_not_awaited()

    async def test_forged_event_context_source_fails_closed(self):
        messenger = _Messenger("_host")
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        adapter.set_name_cache(
            {
                "source-id": {("graph-a", "worker-a", "source-id")},
                "target-id": {("graph-a", "worker-b", "target-id")},
            }
        )
        result = await adapter._dispatch(
            _msg(
                "worker-a",
                _body(event={"context": {"source": "forged-id"}}),
            )
        )
        assert result == {"delivered": False, "error": "forged event source"}
        messenger.request.assert_not_awaited()

    async def test_cross_worker_local_graph_ids_share_one_cluster(self):
        messenger = _Messenger("_host")
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        adapter.set_name_cache(
            {
                "source-id": {("graph-a", "worker-a", "source-id")},
                "target-id": {("graph-b", "worker-b", "target-id")},
            }
        )
        adapter.set_cluster_members(lambda graph_id: {"graph-a", "graph-b"})
        result = await adapter._dispatch(_msg("worker-a", _body()))
        assert result == {"delivered": True}
        assert messenger.request.await_args.args[0] == "worker-b"

    async def test_ambiguous_same_graph_target_fails_closed(self):
        messenger = _Messenger("_host")
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        adapter.set_name_cache(
            {
                "source-id": {("graph-a", "worker-a", "source-id")},
                "worker": {
                    ("graph-a", "node-a", "id-a"),
                    ("graph-a", "node-b", "id-b"),
                },
            }
        )
        result = await adapter._dispatch(_msg("worker-a", _body(target_name="worker")))
        assert result["delivered"] is False
        messenger.request.assert_not_awaited()

    async def test_forged_peer_and_excess_hop_fail_closed(self):
        messenger = _Messenger("_host")
        adapter = TerrariumOutputWireAdapter(SimpleNamespace(_creatures={}), messenger)
        adapter.set_name_cache({"source-id": {("graph-a", "worker-a", "source-id")}})
        forged = await adapter._dispatch(_msg("worker-a", _body(peer="worker-forged")))
        excess = await adapter._dispatch(_msg("worker-a", _body(hop=2)))
        assert forged["delivered"] is False
        assert excess["delivered"] is False
        messenger.request.assert_not_awaited()


class TestWorkerDelivery:
    async def test_delivery_acknowledges_enqueue_without_waiting_for_full_turn(self):
        messenger = _Messenger("worker-b")
        target = _Creature("target-id", "worker", "graph-a")
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_process(event):
            entered.set()
            await release.wait()

        target.agent._process_event = blocking_process
        adapter = TerrariumOutputWireAdapter(
            SimpleNamespace(_creatures={target.creature_id: target}), messenger
        )

        result = await asyncio.wait_for(
            adapter._dispatch(
                _msg(
                    "_host",
                    _body(
                        peer="_host",
                        hop=1,
                        target_graph_id="graph-a",
                        target_creature_id="target-id",
                    ),
                )
            ),
            timeout=0.2,
        )
        assert result == {"delivered": True}
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        release.set()
        await asyncio.sleep(0)

    async def test_stopped_target_is_not_reported_as_delivered(self):
        messenger = _Messenger("worker-b")
        target = _Creature("target-id", "worker", "graph-a")
        target.agent._running = False
        adapter = TerrariumOutputWireAdapter(
            SimpleNamespace(_creatures={target.creature_id: target}), messenger
        )
        result = await adapter._dispatch(
            _msg(
                "_host",
                _body(
                    peer="_host",
                    hop=1,
                    target_graph_id="graph-a",
                    target_creature_id="target-id",
                ),
            )
        )
        assert result == {"delivered": False, "error": "target not running"}
        target.agent._process_event.assert_not_awaited()

    async def test_exact_id_same_graph_is_queued_before_success(self):
        messenger = _Messenger("worker-b")
        target = _Creature("target-id", "worker", "graph-a")
        foreign = _Creature("foreign-id", "worker", "graph-b")
        engine = SimpleNamespace(
            _creatures={
                target.creature_id: target,
                foreign.creature_id: foreign,
            }
        )
        adapter = TerrariumOutputWireAdapter(engine, messenger)
        result = await adapter._dispatch(
            _msg(
                "_host",
                _body(
                    peer="_host",
                    hop=1,
                    target_graph_id="graph-a",
                    target_creature_id="target-id",
                ),
            )
        )
        assert result == {"delivered": True}
        await asyncio.sleep(0)
        target.agent._process_event.assert_awaited_once()
        foreign.agent._process_event.assert_not_awaited()

    async def test_worker_rejects_non_host_relay(self):
        messenger = _Messenger("worker-b")
        target = _Creature("target-id", "worker", "graph-a")
        adapter = TerrariumOutputWireAdapter(
            SimpleNamespace(_creatures={target.creature_id: target}), messenger
        )
        result = await adapter._dispatch(
            _msg(
                "worker-forged",
                _body(
                    peer="worker-forged",
                    hop=1,
                    target_graph_id="graph-a",
                    target_creature_id="target-id",
                ),
            )
        )
        assert result == {"delivered": False, "error": "invalid relay peer"}
        target.agent._process_event.assert_not_awaited()

    async def test_graph_mismatch_and_unknown_target_fail_closed(self):
        messenger = _Messenger("worker-b")
        target = _Creature("target-id", "worker", "graph-b")
        engine = SimpleNamespace(_creatures={target.creature_id: target})
        adapter = TerrariumOutputWireAdapter(engine, messenger)
        mismatch = await adapter._dispatch(
            _msg(
                "_host",
                _body(
                    peer="_host",
                    hop=1,
                    target_graph_id="graph-a",
                    target_creature_id="target-id",
                ),
            )
        )
        unknown = await adapter._dispatch(
            _msg(
                "_host",
                _body(
                    target_name="missing",
                    peer="_host",
                    hop=1,
                    target_graph_id="graph-a",
                    target_creature_id="missing",
                ),
            )
        )
        assert mismatch["delivered"] is False
        assert unknown["delivered"] is False
        target.agent._process_event.assert_not_awaited()
