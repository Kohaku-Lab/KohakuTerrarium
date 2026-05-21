"""Cross-node channel-broadcast / output-wire forwarding audit.

Focused, single-purpose e2e tests that probe specific failure modes in
``laboratory/adapters/terrarium_broadcast.py``,
``laboratory/adapters/terrarium_output_wire.py``, and
``terrarium/multi_node_service.py``'s cross-node subscription dance.

These complement (do NOT replace) the existing ``test_multinode_real``
suite — they isolate the **forwarding fabric** as the unit under test
instead of wrapping it inside a fat journey.

Uses :class:`RealLabWorker` (in-process workers) so each test can inspect
the worker's live ``Terrarium`` engine + session stores directly,
asserting behaviour rather than shape.  Behaviour asserts only:

- bravo's local session store records the cross-node broadcast
  (channel history visibility, bug #155).
- bravo's listen-trigger fires (cross-node delivery, the load-bearing
  property).
- alpha completing a turn fires an output-wire delivery on bravo across
  workers (TerrariumOutputWireAdapter.inject path).
- a rapid burst of sends preserves ordering on the receiver.
- the cross-node forwarder doesn't loop a message back to itself via
  the replicated channel on w2.
- ``_cross_subs`` bookkeeping survives a worker drop without leaking.
"""

import asyncio
import json
from pathlib import Path

import pytest

from kohakuterrarium.testing.llm import ScriptEntry

from tests.e2e._lab_harness import (
    OP_TIMEOUT,
    RealLabHost,
    RealLabWorker,
    install_scripted_llm,
)


def _send_channel_tool_call(channel: str, message: str) -> str:
    """Build a bracket-format ``send_channel`` tool call payload."""
    return (
        "[/send_channel]\n"
        f"@@channel={channel}\n"
        f"@@message={message}\n"
        "[send_channel/]"
    )


async def _drive_one_turn(host, graph_id: str, creature_id: str, user_text: str):
    """Drive one chat turn via the WS, drain frames until idle."""
    url = f"/ws/sessions/{graph_id}/creatures/{creature_id}/chat"
    frames: list[dict] = []
    async with host.api_ws(url) as ws:
        await ws.send(json.dumps({"type": "input", "content": user_text}))
        deadline = asyncio.get_event_loop().time() + OP_TIMEOUT * 2
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                break
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                continue
            frames.append(frame)
            if frame.get("type") == "idle":
                break
    return frames


pytestmark = pytest.mark.timeout(120)


def _write_creature_config(root: Path, name: str, system_prompt: str) -> Path:
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "config.yaml").write_text(
        f"name: {name}\n"
        f"system_prompt: {system_prompt!r}\n"
        "llm_profile: openai/gpt-4-test\n"
        "model: gpt-4\n"
        "provider: openai\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n",
        encoding="utf-8",
    )
    return cdir


async def _setup_cross_node_a_to_b(host, w1_name, w2_name, cfg_a, cfg_b, channel):
    """Spawn alpha@w1, bravo@w2, declare ``channel`` on alpha's graph,
    wire alpha→channel (send) + channel→bravo (listen).  Returns
    ``(graph_a, a_id, a_name, graph_b, b_id, b_name)``."""
    sa = (
        await asyncio.wait_for(
            host.http.post(
                "/api/sessions/active/creature",
                json={"config_path": str(cfg_a), "on_node": w1_name},
            ),
            timeout=OP_TIMEOUT * 2,
        )
    ).json()
    sb = (
        await asyncio.wait_for(
            host.http.post(
                "/api/sessions/active/creature",
                json={"config_path": str(cfg_b), "on_node": w2_name},
            ),
            timeout=OP_TIMEOUT * 2,
        )
    ).json()
    graph_a, a_id, a_name = (
        sa["session_id"],
        sa["creatures"][0]["creature_id"],
        sa["creatures"][0].get("name"),
    )
    graph_b, b_id, b_name = (
        sb["session_id"],
        sb["creatures"][0]["creature_id"],
        sb["creatures"][0].get("name"),
    )
    assert graph_a != graph_b
    r_ch = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_a}/channels",
            json={"name": channel},
        ),
        timeout=OP_TIMEOUT,
    )
    assert r_ch.status_code == 200, r_ch.text
    r_send = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
            json={"channel": channel, "direction": "send"},
        ),
        timeout=OP_TIMEOUT,
    )
    assert r_send.status_code == 200, r_send.text
    r_listen = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_b}/creatures/{b_id}/wire",
            json={"channel": channel, "direction": "listen"},
        ),
        timeout=OP_TIMEOUT,
    )
    assert r_listen.status_code == 200, r_listen.text
    return graph_a, a_id, a_name, graph_b, b_id, b_name


# ---------------------------------------------------------------------------
# 1. Channel history visibility on the RECEIVER side after cross-node send.
# ---------------------------------------------------------------------------


async def test_cross_node_broadcast_lands_in_receiver_channel_store(
    tmp_path, monkeypatch
):
    """After alpha@w1 sends on ``ch1``, bravo@w2's session store must
    record the channel message under bravo's graph.

    This is the property bug #155 reports failing: the worker hosting the
    receiver shows an empty channel history because the cross-node
    forwarder either skips the persistence path or writes to the wrong
    graph_id.  Asserts directly against the worker's
    ``SessionStore.get_channel_messages`` so the API gap (no history on
    GET /channels/{ch}) doesn't mask the underlying behaviour.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(
        monkeypatch,
        script=[
            ScriptEntry(
                response=_send_channel_tool_call("ch1", "hello from alpha"),
                match="please ping",
            ),
            ScriptEntry(response="ok", match="hello from alpha"),
            ScriptEntry(response="ok"),
        ],
    )
    cfg_a = _write_creature_config(tmp_path, "alpha", "You are alpha.")
    cfg_b = _write_creature_config(tmp_path, "bravo", "You are bravo.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
            RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
        ):
            # Wait for both workers to register on the host.
            await RealLabHost._wait_for(
                lambda: {w1.node_id, w2.node_id}
                <= set(host.host_engine.alive_clients()),
                "both workers join",
            )
            graph_a, a_id, _a_name, graph_b, b_id, _b_name = (
                await _setup_cross_node_a_to_b(
                    host, w1.node_id, w2.node_id, cfg_a, cfg_b, "ch1"
                )
            )

            # Drive alpha's LLM → emits the send_channel tool call.
            await _drive_one_turn(host, graph_a, a_id, "please ping bravo")

            # Give the cross-node notify + inject task a moment to land.
            await asyncio.sleep(1.0)

            # The worker hosting the receiver must have persisted the
            # message under the receiver's graph.  Direct store inspection
            # — bypasses the topology API gap that doesn't expose history.
            assert w2.engine is not None
            store_b = w2.engine._session_stores.get(graph_b)
            assert store_b is not None, (
                f"bravo's session store missing for graph_b={graph_b!r}; "
                f"available: {list(w2.engine._session_stores.keys())}"
            )
            msgs = store_b.get_channel_messages("ch1")
            contents = [m.get("content", "") for m in msgs]
            assert any("hello from alpha" in str(c) for c in contents), (
                "cross-node broadcast did not land in bravo's channel "
                f"history (bug #155).  messages: {msgs}"
            )


# ---------------------------------------------------------------------------
# 2. Burst ordering — 5 rapid sends arrive on bravo in order, no dupes.
# ---------------------------------------------------------------------------


async def test_cross_node_burst_preserves_order_no_dupes(tmp_path, monkeypatch):
    """Five rapid channel sends from alpha must arrive on bravo's side
    in the same order, with no duplicates and no losses.

    The persistence callback schedules each forward as
    ``asyncio.create_task(broadcast.forward_send(...))`` — concurrent
    tasks can interleave, but the receiver's store must end up with the
    same sequence the sender produced.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    # Build alpha's reply: ONE turn emits five send_channel tool calls
    # back-to-back.  The framework dispatches them in parse order, all
    # within the same turn, and each goes through the persistence →
    # forward_send pipeline.
    burst_payload = "\n".join(
        _send_channel_tool_call("burst", f"msg-{i}") for i in range(5)
    )
    install_scripted_llm(
        monkeypatch,
        script=[
            ScriptEntry(response=burst_payload, match="please burst"),
            ScriptEntry(response="ok"),
        ],
    )
    cfg_a = _write_creature_config(tmp_path, "alpha", "You are alpha.")
    cfg_b = _write_creature_config(tmp_path, "bravo", "You are bravo.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
            RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
        ):
            await RealLabHost._wait_for(
                lambda: {w1.node_id, w2.node_id}
                <= set(host.host_engine.alive_clients()),
                "both workers join",
            )
            graph_a, a_id, _a_name, graph_b, _b_id, _b_name = (
                await _setup_cross_node_a_to_b(
                    host, w1.node_id, w2.node_id, cfg_a, cfg_b, "burst"
                )
            )

            # Drive alpha → emits 5 send_channel calls.
            await _drive_one_turn(host, graph_a, a_id, "please burst")

            # Let the forward tasks drain.
            await asyncio.sleep(2.0)

            store_b = w2.engine._session_stores.get(graph_b)
            assert store_b is not None
            msgs = store_b.get_channel_messages("burst")
            contents = [m.get("content", "") for m in msgs]
            seen = [c for c in contents if isinstance(c, str) and c.startswith("msg-")]
            # Order + count + no dupes.
            assert seen == ["msg-0", "msg-1", "msg-2", "msg-3", "msg-4"], (
                "cross-node burst did not arrive in order without dupes; "
                f"observed: {seen}\nall contents: {contents}"
            )


# ---------------------------------------------------------------------------
# 3. Self-echo prevention — w2 receives an injected message and must
#    NOT re-forward it back to w1 (the ``_injected`` flag guard).
# ---------------------------------------------------------------------------


async def test_cross_node_inject_does_not_re_forward(tmp_path, monkeypatch):
    """When w2 replays an injected message into its local channel, the
    persistence callback must skip the re-forward (``_injected`` flag).

    Observable property: w1's session store under alpha's graph records
    alpha's own send (exactly once) — there's no echo round-trip from
    w2 that would land back as a *second* message under alpha's graph.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(
        monkeypatch,
        script=[
            ScriptEntry(
                response=_send_channel_tool_call("echo", "single"),
                match="please send single",
            ),
            ScriptEntry(response="ok"),
        ],
    )
    cfg_a = _write_creature_config(tmp_path, "alpha", "You are alpha.")
    cfg_b = _write_creature_config(tmp_path, "bravo", "You are bravo.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
            RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
        ):
            await RealLabHost._wait_for(
                lambda: {w1.node_id, w2.node_id}
                <= set(host.host_engine.alive_clients()),
                "both workers join",
            )
            graph_a, a_id, _a_name, _graph_b, _b_id, _b_name = (
                await _setup_cross_node_a_to_b(
                    host, w1.node_id, w2.node_id, cfg_a, cfg_b, "echo"
                )
            )

            # Drive alpha to send ONE message via the bracket tool call.
            await _drive_one_turn(host, graph_a, a_id, "please send single")

            await asyncio.sleep(1.0)

            store_a = w1.engine._session_stores.get(graph_a)
            assert store_a is not None
            msgs = store_a.get_channel_messages("echo")
            singles = [
                m
                for m in msgs
                if isinstance(m.get("content", ""), str)
                and "single" in m.get("content", "")
            ]
            assert len(singles) == 1, (
                "alpha's graph saw the same message more than once — the "
                f"cross-node inject re-forwarded back to sender.  messages: {msgs}"
            )


# ---------------------------------------------------------------------------
# 4. Cross-node output wire firing — alpha completes a turn, bravo's
#    receiver-side TerrariumOutputWireAdapter.inject runs.
# ---------------------------------------------------------------------------


async def test_cross_node_output_wire_fires_on_target_worker(tmp_path, monkeypatch):
    """alpha@w1 → b@w2 output wire: alpha completing a turn must produce
    an inbound delivery on bravo (observable as bravo's LLM being called
    with alpha's content rendered into a user-role message).

    The forwarder path:
      alpha._finalize → output_wiring resolver miss → forwarder.peer_for_target
      → forwarder.forward_event → host relay → w2._op_inject →
      target_agent._process_event.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    # bravo's reply when its triggered turn runs.
    holder = install_scripted_llm(
        monkeypatch,
        script=[
            ScriptEntry(response="hello bravo from alpha", match="greet alpha"),
            ScriptEntry(response="bravo got pinged"),
        ],
    )
    cfg_a = _write_creature_config(tmp_path, "alpha", "You are alpha.")
    cfg_b = _write_creature_config(tmp_path, "bravo", "You are bravo.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
            RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
        ):
            await RealLabHost._wait_for(
                lambda: {w1.node_id, w2.node_id}
                <= set(host.host_engine.alive_clients()),
                "both workers join",
            )
            sa = (
                await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": w1.node_id},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
            ).json()
            sb = (
                await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_b), "on_node": w2.node_id},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
            ).json()
            graph_a, a_id = sa["session_id"], sa["creatures"][0]["creature_id"]
            _graph_b, b_id = sb["session_id"], sb["creatures"][0]["creature_id"]

            # Cross-worker direct output wire alpha → bravo.
            r_wire = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/wiring/{graph_a}/creatures/{a_id}/outputs",
                    json={
                        "to": b_id,
                        "with_content": True,
                        "prompt_format": "simple",
                        "allow_self_trigger": False,
                    },
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert r_wire.status_code == 200, r_wire.text

            # Drive alpha's turn — its output should reach bravo via the
            # cross-node output-wire forwarder.
            chat_url = f"/ws/sessions/{graph_a}/creatures/{a_id}/chat"
            async with host.api_ws(chat_url) as ws:
                await ws.send(json.dumps({"type": "input", "content": "greet alpha"}))
                # Drain a few frames so the turn finalises and the wiring
                # emit fires.
                deadline = asyncio.get_event_loop().time() + OP_TIMEOUT * 2
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        break
                    try:
                        frame = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if frame.get("type") == "idle":
                        break

            # Wait for the cross-node delivery to drive bravo's turn.
            await asyncio.sleep(1.0)

            # Behaviour assert: bravo's scripted LLM was invoked with a
            # user-role payload containing alpha's content.  This is the
            # *only* way bravo's controller could have been driven —
            # there's no other input wired to bravo.
            _ = holder.get("scripted") or holder
            # `install_scripted_llm` patches all sites to one ScriptedLLM
            # per provider call; we observe the cross-node delivery by
            # finding the receiver's LLM call log carries alpha's text.
            # ScriptedLLM exposes call_log on the instance; we can't get
            # the exact instance without monkey-instrumenting, so use
            # the per-creature scratchpad/event log instead.
            assert w2.engine is not None
            bravo_creature = next(
                (c for c in w2.engine.list_creatures() if c.creature_id == b_id),
                None,
            )
            assert bravo_creature is not None, "bravo missing from w2 engine"
            # Inspect bravo's controller conversation — the wiring
            # delivery becomes a triggered turn whose user-role message
            # carries the rendered prompt.
            agent = bravo_creature.agent
            history = agent.conversation_history
            joined = " ".join(
                str(m.get("content", "")) for m in history if isinstance(m, dict)
            )
            assert "hello bravo from alpha" in joined, (
                "bravo's conversation has no inbound wire delivery from "
                f"alpha across worker boundary.  history: {history!r}"
            )


# ---------------------------------------------------------------------------
# 5. _cross_subs leak on worker drop.
# ---------------------------------------------------------------------------


async def test_cross_subs_cleared_when_worker_drops(tmp_path, monkeypatch):
    """When a worker disconnects, the host's ``_cross_subs`` registry
    must not retain entries pointing at the gone worker.

    Property: after w2 drops, no entry in
    ``service._cross_subs`` references ``w2`` (either as ``my_node`` or
    ``peer_node``).  Today ``drop_remote`` clears the ``_home`` map and
    the ``_remotes`` dict but leaves ``_cross_subs`` untouched —
    subsequent re-connects then re-record on top of stale entries.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["a", "b"])
    cfg_a = _write_creature_config(tmp_path, "alpha", "You are alpha.")
    cfg_b = _write_creature_config(tmp_path, "bravo", "You are bravo.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1:
            await RealLabHost._wait_for(
                lambda: w1.node_id in set(host.host_engine.alive_clients()),
                "w1 join",
            )
            async with RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2:
                await RealLabHost._wait_for(
                    lambda: w2.node_id in set(host.host_engine.alive_clients()),
                    "w2 join",
                )
                await _setup_cross_node_a_to_b(
                    host, w1.node_id, w2.node_id, cfg_a, cfg_b, "drop_ch"
                )
                # ``get_service_legacy`` because we're driving the
                # cluster from outside an HTTP request; the new
                # HTTP-scoped ``get_service`` needs an HTTPConnection
                # for per-user routing resolution.
                from kohakuterrarium.api.deps import get_service_legacy

                service = get_service_legacy()
                # Sanity: a cross-sub entry was recorded for the wire we
                # just set up.
                assert any(
                    w2.node_id in key
                    for key in getattr(service, "_cross_subs", {}).keys()
                ), (
                    "no cross-sub recorded for the wire we just set up; "
                    f"_cross_subs: {getattr(service, '_cross_subs', {})}"
                )
            # w2 has now dropped.  Give the host a moment to process the
            # disconnect.
            await asyncio.sleep(0.5)
            leftover = [
                key
                for key in getattr(service, "_cross_subs", {}).keys()
                if w2.node_id in key
            ]
            assert not leftover, (
                "_cross_subs leaked entries for the dropped worker; "
                f"leftover: {leftover}"
            )
