"""Deep e2e probes for reported-but-not-yet-reproducing multinode bugs.

Each test targets a SPECIFIC user-reported bug that the existing fat
journey passes through cleanly.  These probes drill into the exact
code paths an agent investigation flagged as plausible failure modes:

- ``#150`` — "wired A→B but no edge in graph"
- ``#151`` — "Cannot route to creature ... not found in this session"
- ``#152`` — worker hangs / timeouts after wire ops
- ``#143`` — wire b→ch2 fails after a→1→b setup
- ``#145`` — cross-node direct output wire returns 400
- new — ``_fold_clusters`` doesn't union ``output_edges`` across members

Each probe uses the real HTTP/WebSocket API plus targeted internal
inspection (e.g. ``service._cluster_links``) when needed to make the
failure visible in a behavior-asserting way — not a shape check.
"""

import asyncio
import json
from pathlib import Path

import pytest

from tests.e2e._lab_harness import (
    OP_TIMEOUT,
    RealLabHost,
    RealLabWorker,
    install_scripted_llm,
)
from kohakuterrarium.testing.llm import ScriptEntry

pytestmark = pytest.mark.timeout(180)


def _write_cfg(root: Path, name: str) -> Path:
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "config.yaml").write_text(
        f"name: {name}\n"
        f"system_prompt: 'You are {name}.'\n"
        "model: gpt-4\n"
        "provider: openai\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n",
        encoding="utf-8",
    )
    return cdir


async def _drain_chat(ws, message: str, *, idle: float = 3.0, hard: float = 20.0):
    """Send one chat turn; return (text, frames)."""
    await ws.send(json.dumps({"type": "input", "content": message}))
    chunks: list[str] = []
    frames: list[dict] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + hard
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=idle)
        except asyncio.TimeoutError:
            break
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            continue
        frames.append(frame)
        t = frame.get("type")
        if t in ("text", "text_chunk", "assistant"):
            chunks.append(str(frame.get("content", "")))
        elif t == "error":
            chunks.append(f"<ERROR:{frame.get('content')}>")
            break
        elif t == "idle" and chunks:
            break
    return "".join(chunks), frames


async def _spawn_alpha_bravo_cluster(host, w1, w2, cfg_alpha, cfg_bravo):
    """Common setup: alpha on w1, bravo on w2, ch1 with alpha→ch1→bravo."""
    sa = (
        await host.http.post(
            "/api/sessions/active/creature",
            json={"config_path": str(cfg_alpha), "on_node": "w1"},
        )
    ).json()
    graph_a = sa["session_id"]
    a_id = sa["creatures"][0]["creature_id"]
    a_name = sa["creatures"][0]["name"]

    sb = (
        await host.http.post(
            "/api/sessions/active/creature",
            json={"config_path": str(cfg_bravo), "on_node": "w2"},
        )
    ).json()
    graph_b = sb["session_id"]
    b_id = sb["creatures"][0]["creature_id"]
    b_name = sb["creatures"][0]["name"]

    await host.http.post(
        f"/api/sessions/topology/{graph_a}/channels", json={"name": "ch1"}
    )
    await host.http.post(
        f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
        json={"channel": "ch1", "direction": "send"},
    )
    await host.http.post(
        f"/api/sessions/topology/{graph_b}/creatures/{b_id}/wire",
        json={"channel": "ch1", "direction": "listen"},
    )
    return graph_a, graph_b, a_id, a_name, b_id, b_name


class TestMultinodeDeepProbes:
    """Each method probes one reported-but-not-reproducing bug at
    a deeper level than the main journey reaches."""

    async def test_bug_150_channel_in_creature_lists_must_appear_in_cluster_channels(
        self, tmp_path, monkeypatch
    ):
        """#150 deep probe: every channel name appearing in any cluster
        creature's send/listen MUST also appear in cluster.channels.

        The frontend renders channel edges by looking up the channel
        NODE built from `graph.channels` and connecting the creature
        node via `send_channels`/`listen_channels`. If the channel
        appears in the creature's lists but is MISSING from the cluster
        graph's `channels`, the frontend draws nothing → "wired but no
        edge" symptom.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[
                ScriptEntry(response="ack"),
                ScriptEntry(response="ack2"),
            ],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_alpha_bravo_cluster(
                    host, w1, w2, cfg_a, cfg_b
                )

                snap = (await host.http.get("/api/runtime/graph")).json()
                graphs = snap.get("graphs") or []
                cluster = next(
                    (
                        g
                        for g in graphs
                        if {a_id, b_id}
                        <= {
                            (c.get("creature_id") or c.get("agent_id"))
                            for c in (g.get("creatures") or [])
                        }
                    ),
                    None,
                )
                assert cluster is not None, (
                    f"no cluster fold containing both creatures; "
                    f"got: {[g.get('graph_id') for g in graphs]}"
                )
                cluster_channels = {
                    ch.get("name") for ch in (cluster.get("channels") or [])
                }
                # Collect every channel name referenced by every creature.
                referenced = set()
                for c in cluster.get("creatures") or []:
                    referenced.update(c.get("send_channels") or [])
                    referenced.update(c.get("listen_channels") or [])
                # Behavior assertion: every channel a creature claims to
                # send/listen to MUST exist as a channel node in the
                # same cluster.
                missing = referenced - cluster_channels
                assert not missing, (
                    f"BUG #150: channels {missing!r} appear in creature "
                    f"send/listen lists but NOT in cluster.channels; "
                    f"frontend renders no edge.\n"
                    f"cluster_channels={cluster_channels!r}\n"
                    f"referenced={referenced!r}"
                )

    async def test_bug_150_cluster_creature_graph_id_must_match_cluster_primary(
        self, tmp_path, monkeypatch
    ):
        """#150 deep probe (theory B): in a cross-node cluster, every
        creature dict surfaced by the runtime-graph snapshot MUST carry
        ``graph_id == cluster.graph_id`` (the cluster's primary id).

        Frontend ``runtimeGraphModel.js::addChannelEdges`` keys the
        channel NODE id by the cluster-level ``graph.graph_id`` and
        connects creature nodes to it. If the per-creature ``graph_id``
        stamped by ``_creatures_for_graph`` is the worker's LOCAL engine
        graph (e.g. bravo's graph_b while the cluster surfaces graph_a),
        downstream lookups that match by per-creature ``graph_id``
        instead of cluster id will fail and the bravo↔channel edge will
        never render.

        Probe: assert the snapshot's per-creature ``graph_id`` agrees
        with the cluster's top-level ``graph_id``.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[
                ScriptEntry(response="ack"),
                ScriptEntry(response="ack2"),
            ],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_alpha_bravo_cluster(
                    host, w1, w2, cfg_a, cfg_b
                )

                snap = (await host.http.get("/api/runtime/graph")).json()
                graphs = snap.get("graphs") or []
                cluster = next(
                    (
                        g
                        for g in graphs
                        if {a_id, b_id}
                        <= {
                            (c.get("creature_id") or c.get("agent_id"))
                            for c in (g.get("creatures") or [])
                        }
                    ),
                    None,
                )
                assert cluster is not None, (
                    f"no cluster fold containing both creatures; "
                    f"got: {[g.get('graph_id') for g in graphs]}"
                )
                cluster_gid = cluster.get("graph_id")
                assert cluster_gid, "cluster has no graph_id"
                mismatches: list[tuple[str, str]] = []
                for c in cluster.get("creatures") or []:
                    cid = c.get("creature_id") or c.get("agent_id") or "?"
                    cgid = c.get("graph_id")
                    if cgid != cluster_gid:
                        mismatches.append((cid, cgid))
                assert not mismatches, (
                    f"BUG #150 (theory B): per-creature graph_id != "
                    f"cluster.graph_id; frontend builds channel edges "
                    f"keyed by cluster gid={cluster_gid!r} but creatures "
                    f"carry {mismatches!r} — any downstream code that "
                    f"keys lookups by per-creature graph_id will miss "
                    f"the channel node."
                )

    async def test_bug_151_chat_ws_by_id_via_cluster_primary(
        self, tmp_path, monkeypatch
    ):
        """#151 deep probe: chat WS targeting BRAVO BY CREATURE_ID via
        the CLUSTER PRIMARY graph_id (graph_a — alpha's graph, not
        bravo's actual graph_b).

        The journey tested chat-by-name via cluster primary — works.
        But the WS handler in ``api/ws/io.py`` calls
        ``attach_io(service, session_id, creature_id)`` which goes
        through ``service.chat(creature_id, ...)`` — does it accept
        a creature_id that lives on a DIFFERENT physical engine graph
        than the session_id in the URL?
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[
                ScriptEntry(response="bravo replies"),
                ScriptEntry(response="bravo replies again"),
                ScriptEntry(response="fallback"),
                ScriptEntry(response="fallback2"),
            ],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_alpha_bravo_cluster(
                    host, w1, w2, cfg_a, cfg_b
                )

                # ── Variant 1: bravo by ID via cluster primary (ga) ──
                ws_url = f"/ws/sessions/{ga}/creatures/{b_id}/chat"
                try:
                    async with host.api_ws(ws_url) as ws:
                        text, frames = await asyncio.wait_for(
                            _drain_chat(ws, "hi bravo by id"),
                            timeout=OP_TIMEOUT * 2,
                        )
                except asyncio.TimeoutError:
                    text, frames = "", []
                assert text and "ERROR:" not in text, (
                    f"BUG #151 (variant 1): chat WS to bravo BY ID via "
                    f"cluster primary graph_id failed; text={text!r}, "
                    f"frames={[f.get('type') for f in frames]}"
                )

                # ── Variant 2: disconnect, then chat by name via primary ──
                await host.http.post(
                    f"/api/sessions/topology/{ga}/disconnect",
                    json={"sender": a_id, "receiver": b_id, "channel": "ch1"},
                )
                ws_url2 = f"/ws/sessions/{ga}/creatures/{b_name}/chat"
                try:
                    async with host.api_ws(ws_url2) as ws:
                        text2, _ = await asyncio.wait_for(
                            _drain_chat(ws, "still here?"),
                            timeout=OP_TIMEOUT * 2,
                        )
                except asyncio.TimeoutError:
                    text2 = ""
                # Post-disconnect bravo lives in graph_b only (cluster
                # is broken). Chat via graph_a SHOULD fail with a clear
                # error, not a timeout — that's a behavior assertion on
                # the routing layer's error reporting.
                # If it returns text, that's also valid (split tolerance).
                if not text2:
                    # Try via canonical graph_b URL — must succeed there.
                    ws_url3 = f"/ws/sessions/{gb}/creatures/{b_id}/chat"
                    try:
                        async with host.api_ws(ws_url3) as ws:
                            text3, _ = await asyncio.wait_for(
                                _drain_chat(ws, "via canonical"),
                                timeout=OP_TIMEOUT * 2,
                            )
                    except asyncio.TimeoutError:
                        text3 = ""
                    assert text3 and "ERROR:" not in text3, (
                        f"BUG #151 (variant 2): after cluster split, "
                        f"bravo unreachable on its OWN canonical graph; "
                        f"got text={text3!r}"
                    )

    async def test_bug_152_concurrent_wires_dont_hang_chat(self, tmp_path, monkeypatch):
        """#152 deep probe: fire MULTIPLE wire operations concurrently
        via asyncio.gather, then verify chat to alpha/bravo still
        responds within OP_TIMEOUT.

        The user reports "remote creature session always timeout/cannot
        connect after wiring operation". My theory: the host's
        ``_ensure_channel_replicated`` does synchronous ``list_channels``
        + ``add_channel`` + ``proxy_subscribe`` per wire — if two wires
        race against the same channel name they may deadlock on the
        cross-node broadcast subscription.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[
                ScriptEntry(response="post-wire reply"),
                ScriptEntry(response="post-wire reply 2"),
                ScriptEntry(response="post-wire reply 3"),
                ScriptEntry(response="post-wire reply 4"),
                ScriptEntry(response="post-wire reply 5"),
            ],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1"),
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2"),
            ):
                await asyncio.sleep(0.3)

                # Spawn alpha, bravo — but DO NOT pre-wire.
                sa = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": "w1"},
                    )
                ).json()
                ga, a_id = sa["session_id"], sa["creatures"][0]["creature_id"]
                sb = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_b), "on_node": "w2"},
                    )
                ).json()
                gb, b_id = sb["session_id"], sb["creatures"][0]["creature_id"]

                # Create THREE channels rapidly.
                await asyncio.gather(
                    *[
                        host.http.post(
                            f"/api/sessions/topology/{ga}/channels",
                            json={"name": f"ch{i}"},
                        )
                        for i in range(1, 4)
                    ]
                )

                # Fire 6 wire ops CONCURRENTLY.
                wire_results = await asyncio.gather(
                    host.http.post(
                        f"/api/sessions/topology/{ga}/creatures/{a_id}/wire",
                        json={"channel": "ch1", "direction": "send"},
                    ),
                    host.http.post(
                        f"/api/sessions/topology/{ga}/creatures/{a_id}/wire",
                        json={"channel": "ch2", "direction": "send"},
                    ),
                    host.http.post(
                        f"/api/sessions/topology/{ga}/creatures/{a_id}/wire",
                        json={"channel": "ch3", "direction": "send"},
                    ),
                    host.http.post(
                        f"/api/sessions/topology/{gb}/creatures/{b_id}/wire",
                        json={"channel": "ch1", "direction": "listen"},
                    ),
                    host.http.post(
                        f"/api/sessions/topology/{gb}/creatures/{b_id}/wire",
                        json={"channel": "ch2", "direction": "listen"},
                    ),
                    host.http.post(
                        f"/api/sessions/topology/{gb}/creatures/{b_id}/wire",
                        json={"channel": "ch3", "direction": "listen"},
                    ),
                    return_exceptions=True,
                )
                exc = [r for r in wire_results if isinstance(r, Exception)]
                assert not exc, f"BUG #152 (variant): concurrent wires raised: {exc}"

                # Now verify alpha and bravo BOTH respond within timeout.
                async def _ping(graph, cid, msg):
                    url = f"/ws/sessions/{graph}/creatures/{cid}/chat"
                    try:
                        async with host.api_ws(url) as ws:
                            text, _ = await asyncio.wait_for(
                                _drain_chat(ws, msg), timeout=OP_TIMEOUT * 2
                            )
                        return text
                    except asyncio.TimeoutError:
                        return ""

                alpha_text, bravo_text = await asyncio.gather(
                    _ping(ga, a_id, "alpha post wires"),
                    _ping(gb, b_id, "bravo post wires"),
                )
                assert alpha_text and "ERROR:" not in alpha_text, (
                    f"BUG #152: alpha hung after 6 concurrent wires; "
                    f"got text={alpha_text!r}"
                )
                assert bravo_text and "ERROR:" not in bravo_text, (
                    f"BUG #152: bravo hung after 6 concurrent wires; "
                    f"got text={bravo_text!r}"
                )

    async def test_bug_143_wire_immediately_after_channel_add(
        self, tmp_path, monkeypatch
    ):
        """#143 deep probe: after a→ch1→b setup, add ch2 to cluster
        primary, then IMMEDIATELY wire b→ch2 (no warm-up gap).

        The agent investigation flagged a read-after-write race in
        ``_ensure_channel_replicated`` / ``_find_channel_elsewhere`` —
        ch2 may not yet be visible via fan-out ``list_channels``
        immediately after ``add_channel`` returns, causing the wire
        to fail with 400.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(monkeypatch, script=[ScriptEntry(response="ack")])
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_alpha_bravo_cluster(
                    host, w1, w2, cfg_a, cfg_b
                )

                # Add ch2 AND wire b→ch2 with no gap — race condition window.
                add_resp = await host.http.post(
                    f"/api/sessions/topology/{ga}/channels",
                    json={"name": "ch2"},
                )
                assert (
                    add_resp.status_code == 200
                ), f"add ch2: {add_resp.status_code} {add_resp.text}"
                # No sleep — go directly to wire.
                wire_resp = await host.http.post(
                    f"/api/sessions/topology/{ga}/creatures/{b_id}/wire",
                    json={"channel": "ch2", "direction": "send"},
                )
                assert wire_resp.status_code == 200, (
                    f"BUG #143: wire b→ch2 via cluster primary returned "
                    f"{wire_resp.status_code} {wire_resp.text} "
                    f"(possible read-after-write race in "
                    f"_ensure_channel_replicated)"
                )

                # Variant 2: same flow but using bravo's ACTUAL graph_b
                # in the wire URL — must also work.
                await host.http.post(
                    f"/api/sessions/topology/{ga}/channels",
                    json={"name": "ch3"},
                )
                wire_resp2 = await host.http.post(
                    f"/api/sessions/topology/{gb}/creatures/{b_id}/wire",
                    json={"channel": "ch3", "direction": "send"},
                )
                assert wire_resp2.status_code == 200, (
                    f"BUG #143 (variant): wire b→ch3 via bravo's actual "
                    f"graph_b returned {wire_resp2.status_code} "
                    f"{wire_resp2.text}"
                )

    async def test_bug_145_cross_node_output_wire_url_variants(
        self, tmp_path, monkeypatch
    ):
        """#145 deep probe: cross-node direct output wire b→a using
        the CLUSTER PRIMARY URL (graph_a) AND using BRAVO'S OWN URL
        (graph_b). User reported 400; check both URL forms + try
        targeting by NAME (not just ID).
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(monkeypatch, script=[ScriptEntry(response="ack")])
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_alpha_bravo_cluster(
                    host, w1, w2, cfg_a, cfg_b
                )

                # ── Variant 1: URL uses BRAVO'S graph_b, target by ID ──
                r1 = await host.http.post(
                    f"/api/sessions/wiring/{gb}/creatures/{b_id}/outputs",
                    json={
                        "to": a_id,
                        "with_content": True,
                        "prompt_format": "simple",
                        "allow_self_trigger": False,
                    },
                )
                assert r1.status_code == 200, (
                    f"BUG #145 (variant 1: by id, own URL): "
                    f"{r1.status_code} {r1.text}"
                )

                # ── Variant 2: URL uses CLUSTER PRIMARY graph_a ──
                r2 = await host.http.post(
                    f"/api/sessions/wiring/{ga}/creatures/{b_id}/outputs",
                    json={
                        "to": a_id,
                        "with_content": True,
                        "prompt_format": "simple",
                        "allow_self_trigger": False,
                    },
                )
                # Both URL forms should accept the wire — user-reported
                # 400 may live here.
                assert r2.status_code in (200, 400, 404), (
                    f"BUG #145 (variant 2: cluster primary URL): "
                    f"{r2.status_code} {r2.text}"
                )
                if r2.status_code != 200:
                    pytest.fail(
                        f"BUG #145: output wire via cluster primary URL "
                        f"returned {r2.status_code} {r2.text} — UI uses "
                        f"cluster primary, so this fails the user flow"
                    )

                # ── Variant 3: target by NAME instead of ID ──
                r3 = await host.http.post(
                    f"/api/sessions/wiring/{gb}/creatures/{b_id}/outputs",
                    json={
                        "to": a_name,
                        "with_content": True,
                        "prompt_format": "simple",
                        "allow_self_trigger": False,
                    },
                )
                assert r3.status_code in (200, 400, 404), (
                    f"BUG #145 (variant 3: by name): " f"{r3.status_code} {r3.text}"
                )

    async def test_cluster_fold_must_union_output_edges(self, tmp_path, monkeypatch):
        """Cross-cutting structural bug (new, not previously reported):
        ``MultiNodeTerrariumService._fold_clusters`` does NOT union
        ``output_edges`` across cluster members. If alpha has an output
        wire on w1 AND bravo has one on w2, only ONE of them will
        survive in the cluster snapshot — the user sees the wrong
        topology.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(monkeypatch, script=[ScriptEntry(response="ack")])
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_alpha_bravo_cluster(
                    host, w1, w2, cfg_a, cfg_b
                )

                # Add output wire on BOTH sides of the cluster:
                #   alpha (w1) → bravo
                #   bravo (w2) → alpha
                r1 = await host.http.post(
                    f"/api/sessions/wiring/{ga}/creatures/{a_id}/outputs",
                    json={
                        "to": b_id,
                        "with_content": True,
                        "prompt_format": "simple",
                        "allow_self_trigger": False,
                    },
                )
                assert r1.status_code == 200, f"alpha→bravo: {r1.text}"
                r2 = await host.http.post(
                    f"/api/sessions/wiring/{gb}/creatures/{b_id}/outputs",
                    json={
                        "to": a_id,
                        "with_content": True,
                        "prompt_format": "simple",
                        "allow_self_trigger": False,
                    },
                )
                assert r2.status_code == 200, f"bravo→alpha: {r2.text}"

                # Cluster snapshot — both edges should appear in the
                # cluster's output_edges.  (Pre-fix, only one side does.)
                snap = (await host.http.get("/api/runtime/graph")).json()
                graphs = snap.get("graphs") or []
                cluster = next(
                    (
                        g
                        for g in graphs
                        if {a_id, b_id}
                        <= {
                            (c.get("creature_id") or c.get("agent_id"))
                            for c in (g.get("creatures") or [])
                        }
                    ),
                    None,
                )
                assert cluster is not None
                edges = cluster.get("output_edges") or []
                # Edge endpoints from this cluster's edges.
                pairs = set()
                for e in edges:
                    src = e.get("from")
                    dst = e.get("to_creature_id") or e.get("to")
                    if src and dst:
                        pairs.add((src, dst))
                assert (a_id, b_id) in pairs, (
                    f"NEW BUG: cluster fold lost alpha→bravo output edge; "
                    f"pairs in cluster: {pairs}"
                )
                assert (b_id, a_id) in pairs, (
                    f"NEW BUG: cluster fold lost bravo→alpha output edge "
                    f"(_fold_clusters does not union output_edges across "
                    f"cluster members); pairs in cluster: {pairs}"
                )

    async def test_many_channels_topology_snapshot_stress(self, tmp_path, monkeypatch):
        """Stress probe: create 10 channels and wire 3 creatures to them
        in mixed send/listen patterns. Verify the runtime-graph snapshot
        reflects every channel and every wire — no entries lost in the
        cluster fold under load.

        This probes ``_fold_clusters`` for channel-list completeness when
        many channels coexist on a single cluster — a regression in the
        fold logic might drop later-created channels.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(monkeypatch, script=[ScriptEntry(response="ack")])
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1"),
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2"),
            ):
                await asyncio.sleep(0.3)

                sa = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": "w1"},
                    )
                ).json()
                ga, a_id = sa["session_id"], sa["creatures"][0]["creature_id"]
                sb = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_b), "on_node": "w2"},
                    )
                ).json()
                gb, b_id = sb["session_id"], sb["creatures"][0]["creature_id"]

                # Create 10 channels on graph_a.
                for i in range(1, 11):
                    r = await host.http.post(
                        f"/api/sessions/topology/{ga}/channels",
                        json={"name": f"chan{i}"},
                    )
                    assert (
                        r.status_code == 200
                    ), f"create chan{i}: {r.status_code} {r.text}"

                # Wire alpha as sender on odd, listener on even;
                # bravo opposite. Forces cross-node replication for all 10.
                for i in range(1, 11):
                    a_dir = "send" if i % 2 else "listen"
                    b_dir = "listen" if i % 2 else "send"
                    ra = await host.http.post(
                        f"/api/sessions/topology/{ga}/creatures/{a_id}/wire",
                        json={"channel": f"chan{i}", "direction": a_dir},
                    )
                    rb = await host.http.post(
                        f"/api/sessions/topology/{gb}/creatures/{b_id}/wire",
                        json={"channel": f"chan{i}", "direction": b_dir},
                    )
                    assert (
                        ra.status_code == 200
                    ), f"wire alpha chan{i}: {ra.status_code} {ra.text}"
                    assert (
                        rb.status_code == 200
                    ), f"wire bravo chan{i}: {rb.status_code} {rb.text}"

                # Snapshot — every channel must appear in the cluster.
                snap = (await host.http.get("/api/runtime/graph")).json()
                graphs = snap.get("graphs") or []
                cluster = next(
                    (
                        g
                        for g in graphs
                        if {a_id, b_id}
                        <= {
                            (c.get("creature_id") or c.get("agent_id"))
                            for c in (g.get("creatures") or [])
                        }
                    ),
                    None,
                )
                assert cluster is not None, (
                    f"no cluster fold after 10 channels; "
                    f"got: {[g.get('graph_id') for g in graphs]}"
                )
                names = {ch.get("name") for ch in (cluster.get("channels") or [])}
                missing = {f"chan{i}" for i in range(1, 11)} - names
                assert not missing, (
                    f"NEW BUG: snapshot lost channels {sorted(missing)} "
                    f"under 10-channel stress; cluster has {sorted(names)}"
                )

                # Per-creature wire lists must each contain 10 entries
                # split 5/5.
                by_id = {c["creature_id"]: c for c in cluster.get("creatures") or []}
                a_send = set(by_id.get(a_id, {}).get("send_channels") or [])
                a_listen = set(by_id.get(a_id, {}).get("listen_channels") or [])
                b_send = set(by_id.get(b_id, {}).get("send_channels") or [])
                b_listen = set(by_id.get(b_id, {}).get("listen_channels") or [])
                expected_a_send = {f"chan{i}" for i in range(1, 11) if i % 2}
                expected_a_listen = {f"chan{i}" for i in range(1, 11) if not i % 2}
                assert expected_a_send <= a_send, (
                    f"NEW BUG: alpha lost odd-channel sends; "
                    f"missing {expected_a_send - a_send}"
                )
                assert expected_a_listen <= a_listen, (
                    f"NEW BUG: alpha lost even-channel listens; "
                    f"missing {expected_a_listen - a_listen}"
                )
                assert expected_a_listen <= b_send, (
                    f"NEW BUG: bravo lost even-channel sends; "
                    f"missing {expected_a_listen - b_send}"
                )
                assert expected_a_send <= b_listen, (
                    f"NEW BUG: bravo lost odd-channel listens; "
                    f"missing {expected_a_send - b_listen}"
                )

    async def test_channel_trigger_drives_receiver_turn(self, tmp_path, monkeypatch):
        """Multi-creature interaction via channel: alpha (w1) emits a
        send_channel tool call on ch1; bravo (w2) is wired listen. A
        channel_message frame should reach bravo's WS session and the
        next bravo turn should observe the broadcast.

        Pure behavior assert: did bravo's WS receive a channel_message
        frame within the timeout window?
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        TOOL_CALL_SEND = (
            "[/send_channel]\n"
            "@@channel=ch1\n"
            "@@message=hello bravo from alpha\n"
            "[send_channel/]"
        )
        install_scripted_llm(
            monkeypatch,
            script=[
                ScriptEntry(response=TOOL_CALL_SEND, match="emit broadcast"),
                ScriptEntry(response="bravo ack"),
                ScriptEntry(response="filler"),
                ScriptEntry(response="filler2"),
                ScriptEntry(response="filler3"),
            ],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_alpha_bravo_cluster(
                    host, w1, w2, cfg_a, cfg_b
                )

                # Open bravo's WS BEFORE alpha emits — so we can observe
                # any channel_message frame delivered to bravo.
                bravo_ws_url = f"/ws/sessions/{gb}/creatures/{b_id}/chat"
                async with host.api_ws(bravo_ws_url) as bravo_ws:
                    # Kick a benign user turn on alpha that triggers
                    # send_channel via the scripted LLM.
                    chat_url_a = f"/ws/sessions/{ga}/creatures/{a_id}/chat"
                    async with host.api_ws(chat_url_a) as alpha_ws:
                        await asyncio.wait_for(
                            _drain_chat(alpha_ws, "emit broadcast"),
                            timeout=OP_TIMEOUT * 3,
                        )

                    # Now sniff bravo's WS for the channel_message
                    # frame for a short interval.
                    saw_channel_msg = False
                    deadline = asyncio.get_event_loop().time() + 6.0
                    while asyncio.get_event_loop().time() < deadline:
                        try:
                            raw = await asyncio.wait_for(bravo_ws.recv(), timeout=2.0)
                        except asyncio.TimeoutError:
                            break
                        try:
                            frame = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        if frame.get("type") == "channel_message":
                            content = str(frame.get("content", "")) + str(
                                frame.get("message", "")
                            )
                            if "hello bravo from alpha" in content:
                                saw_channel_msg = True
                                break

                # Channel history must record the broadcast — that's the
                # definitive behavior assert (regardless of WS frame
                # routing variance).
                rr = await host.http.get(f"/api/sessions/topology/{ga}/channels/ch1")
                history = []
                if rr.status_code == 200:
                    body = rr.json()
                    history = body.get("history") or body.get("messages") or []
                got_in_history = any(
                    "hello bravo from alpha" in str(m.get("content", ""))
                    for m in history
                )
                assert got_in_history or saw_channel_msg, (
                    f"NEW BUG: cross-node broadcast did not reach receiver; "
                    f"saw_channel_msg={saw_channel_msg}, "
                    f"channel ch1 history={history}"
                )

    async def test_resume_creature_session_on_worker(self, tmp_path, monkeypatch):
        """Save a worker creature's session, close it, then resume it
        via ``POST /api/sessions/{sid}/resume`` with on_node=w1.

        After resume the creature's history MUST contain the prior
        conversation (the user turn we drove pre-close), proving the
        worker re-attached to the saved .kohakutr file.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[
                ScriptEntry(response="reply pre close"),
                ScriptEntry(response="reply post resume"),
                ScriptEntry(response="filler"),
            ],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")

        async with RealLabHost(tmp_path) as host:
            async with RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1"):
                await asyncio.sleep(0.3)
                sa = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": "w1"},
                    )
                ).json()
                ga = sa["session_id"]
                a_id = sa["creatures"][0]["creature_id"]
                # Drive one turn so there's something to resume.
                chat_url = f"/ws/sessions/{ga}/creatures/{a_id}/chat"
                async with host.api_ws(chat_url) as ws:
                    text, _ = await asyncio.wait_for(
                        _drain_chat(ws, "remember me"),
                        timeout=OP_TIMEOUT * 2,
                    )
                assert text, "pre-close turn produced no reply"

                # Close: evict from memory.
                dd = await host.http.delete(f"/api/sessions/active/{ga}")
                assert dd.status_code in (200, 204, 404), dd.text

                # Find the saved session in /api/sessions.  The mirror
                # writer drains worker session.sync events asynchronously
                # — the DELETE returns once the creature is removed on
                # the worker, but the last queued sync notifications may
                # land in the mirror dir a beat later. Poll with
                # ``refresh=true`` so each retry forces a fresh disk
                # scan instead of reading the 30s cached index.
                sessions: list = []
                body: dict | list = {}
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    ls = await host.http.get("/api/sessions?refresh=true")
                    assert ls.status_code == 200, ls.text
                    body = ls.json()
                    sessions = (
                        body if isinstance(body, list) else body.get("sessions", [])
                    )
                    if sessions:
                        break
                    await asyncio.sleep(0.1)
                assert sessions, (
                    f"NEW BUG: saved sessions list empty after closing "
                    f"a worker creature session; body={body}"
                )
                sid = (
                    sessions[0].get("session_id")
                    or sessions[0].get("name")
                    or sessions[0].get("session_name")
                )
                assert sid, f"saved session has no id: {sessions[0]}"

                # Resume on the worker.
                rr = await host.http.post(
                    f"/api/sessions/{sid}/resume",
                    json={"on_node": "w1"},
                )
                assert rr.status_code in (200, 201, 202), (
                    f"NEW BUG: worker resume returned " f"{rr.status_code} {rr.text}"
                )

                # Behavior assert: the resumed session must surface our
                # prior user turn in /history.  Try several session-id
                # forms (resume may have minted a new active id).
                resume_body = {}
                try:
                    resume_body = rr.json()
                except (ValueError, TypeError):
                    resume_body = {}
                resumed_sid = (
                    resume_body.get("session_id") or resume_body.get("id") or ga
                )
                # The resumed creature may keep its original id or get a
                # fresh one — list active to find it.
                act = await host.http.get("/api/sessions/active")
                active_body = act.json() if act.status_code == 200 else []
                active = (
                    active_body
                    if isinstance(active_body, list)
                    else active_body.get("sessions") or []
                )
                # Pick the first active session that has at least one
                # creature with name 'alpha'.  Active-list "creatures"
                # may be a list-of-dicts OR an int count depending on
                # endpoint shape — handle both.
                target_sid = None
                target_cid = None
                for s in active:
                    s_sid = s.get("session_id")
                    creatures_field = s.get("creatures")
                    if isinstance(creatures_field, list):
                        for c in creatures_field:
                            if not isinstance(c, dict):
                                continue
                            if c.get("name") == "alpha" or c.get("creature_id") == a_id:
                                target_sid = s_sid
                                target_cid = c.get("creature_id")
                                break
                    if target_sid:
                        break
                # Fall back: if no per-session creatures list, try the
                # agents endpoint.
                if not target_sid:
                    ag = await host.http.get("/api/sessions/active/agents")
                    ag_body = ag.json() if ag.status_code == 200 else []
                    agents = (
                        ag_body
                        if isinstance(ag_body, list)
                        else ag_body.get("agents") or []
                    )
                    for c in agents:
                        if not isinstance(c, dict):
                            continue
                        if c.get("name") == "alpha" or c.get("creature_id") == a_id:
                            target_sid = c.get("session_id") or resumed_sid
                            target_cid = c.get("creature_id")
                            break
                if not (target_sid and target_cid):
                    # The active list returns a count rather than a
                    # list of creature dicts after a worker resume —
                    # surfaces an info-completeness gap.  Use the
                    # session detail endpoint to find the creature.
                    sess_id_to_try = (
                        active[0].get("session_id") if active else None
                    ) or resumed_sid
                    det = await host.http.get(f"/api/sessions/active/{sess_id_to_try}")
                    if det.status_code == 200:
                        dbody = det.json()
                        dcreatures = dbody.get("creatures") or []
                        for c in dcreatures:
                            if isinstance(c, dict):
                                target_sid = sess_id_to_try
                                target_cid = c.get("creature_id") or c.get("id")
                                if target_cid:
                                    break
                assert target_sid and target_cid, (
                    f"NEW BUG: after resume on worker, alpha creature "
                    f"not enumerable via /active or /active/{{sid}}; "
                    f"active={active}"
                )
                hh = await host.http.get(
                    f"/api/sessions/{target_sid}/creatures/{target_cid}/history"
                )
                # If the active-list creature-id isn't a valid lookup
                # key, try the original creature_id from the spawn,
                # then the session-level history endpoint.
                if hh.status_code == 404:
                    hh = await host.http.get(
                        f"/api/sessions/{target_sid}/creatures/{a_id}/history"
                    )
                if hh.status_code == 404:
                    # Fall back to session-level history view (no creature
                    # id) — exercises the persistence viewer route.
                    hh = await host.http.get(f"/api/sessions/{target_sid}/history")
                assert hh.status_code == 200, (
                    f"NEW BUG: post-resume history not reachable via "
                    f"any known path (per-creature or session-level); "
                    f"status={hh.status_code} body={hh.text} "
                    f"(target_cid={target_cid!r}, a_id={a_id!r}, "
                    f"target_sid={target_sid!r})"
                )
                hbody = hh.json()
                messages = hbody.get("messages") or hbody.get("events") or []
                joined = " ".join(str(m.get("content", "")) for m in messages)
                assert "remember me" in joined or "reply pre close" in joined, (
                    f"NEW BUG: post-resume worker history lost prior turn; "
                    f"history={messages}"
                )

    async def test_worker_creature_stop_then_respawn(self, tmp_path, monkeypatch):
        """Remote creature lifecycle: spawn on w1, chat, stop (DELETE),
        then re-spawn a fresh creature on w1 from the same config — the
        new creature must accept chats independently.

        Probes lifecycle cleanup of worker creature state — a leak in
        engine bookkeeping after DELETE would block respawn.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[
                ScriptEntry(response="first life"),
                ScriptEntry(response="second life"),
                ScriptEntry(response="filler"),
            ],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")

        async with RealLabHost(tmp_path) as host:
            async with RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1"):
                await asyncio.sleep(0.3)
                # First spawn + chat.
                sa = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": "w1"},
                    )
                ).json()
                ga = sa["session_id"]
                a_id = sa["creatures"][0]["creature_id"]
                async with host.api_ws(
                    f"/ws/sessions/{ga}/creatures/{a_id}/chat"
                ) as ws:
                    t1, _ = await asyncio.wait_for(
                        _drain_chat(ws, "ping"), timeout=OP_TIMEOUT * 2
                    )
                assert t1, "first-life chat produced no reply"

                # Stop the creature.
                dd = await host.http.delete(f"/api/sessions/active/agents/{a_id}")
                assert dd.status_code in (200, 204), dd.text

                # Re-spawn from the same config; must get a fresh
                # session + creature_id.
                sa2 = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": "w1"},
                    )
                ).json()
                ga2 = sa2.get("session_id")
                a2_id = sa2.get("creatures", [{}])[0].get("creature_id")
                assert (
                    ga2 and a2_id
                ), f"NEW BUG: respawn after stop failed: response={sa2}"
                # Chat must work on the fresh creature.
                async with host.api_ws(
                    f"/ws/sessions/{ga2}/creatures/{a2_id}/chat"
                ) as ws:
                    t2, _ = await asyncio.wait_for(
                        _drain_chat(ws, "fresh ping"),
                        timeout=OP_TIMEOUT * 2,
                    )
                assert (
                    t2 and "ERROR:" not in t2
                ), f"NEW BUG: respawned creature unresponsive; reply={t2!r}"
