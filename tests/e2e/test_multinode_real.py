"""Real-deployment-like multi-node e2e journeys.

Unlike the older ``test_multinode_*`` suites — which hand-wired a
``MultiNodeTerrariumService`` over ``InProcTransport`` — this suite boots
the **actual** stack: a real :func:`create_app` lab-host under real
uvicorn, real :class:`HostEngine` over a real WebSocket, and real
:class:`ClientConnector` workers. Every operation is driven through the
HTTP / WebSocket API exactly as the Vue frontend drives it.

The standalone (single-process) path is the behavior reference: a
creature spawned on a worker must behave the same as a local creature —
same naming, same chat, same model switch, same stop semantics. Where it
doesn't, the assertion fails and names the bug.

Each test wraps its operations in :func:`asyncio.wait_for`
(:data:`OP_TIMEOUT`) so a loop-blocking deadlock surfaces as a
``TimeoutError`` naming the stuck step, instead of hanging the run.
"""

import asyncio
import json
from pathlib import Path

import pytest

from kohakuterrarium.testing.llm import ScriptEntry

from tests.e2e._lab_harness import (
    OP_TIMEOUT,
    RealLabHost,
    RealLabSubprocessWorker,
    RealLabWorker,
    install_scripted_llm,
)

pytestmark = pytest.mark.timeout(120)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_creature_config(
    root: Path,
    name: str,
    system_prompt: str,
    *,
    subagents: list[str] | None = None,
) -> Path:
    """Write a realistic on-disk creature config dir.

    A real creature authored for ``kt run`` carries ``input: cli`` — the
    web/lab spawn path is expected to suppress that input loop and let
    the UI attach over WebSocket instead. The test deliberately uses a
    real input type (not ``none``) so a worker that wrongly boots the
    config's own IO is caught.

    ``subagents`` — when given, each name is declared as a ``builtin``
    sub-agent on the creature so the worker builds the full VERTICAL
    composition (controller → sub-agent) at spawn time.
    """
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    text = (
        f"name: {name}\n"
        f"system_prompt: {system_prompt!r}\n"
        "llm_profile: openai/gpt-4-test\n"
        "model: gpt-4\n"
        "provider: openai\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n"
    )
    if subagents:
        text += "subagents:\n"
        for sa in subagents:
            text += f"  - name: {sa}\n    type: builtin\n"
    (cdir / "config.yaml").write_text(text, encoding="utf-8")
    return cdir


async def _drain_chat_ws(
    ws,
    message: str,
    *,
    idle_timeout: float = 4.0,
    hard_timeout: float = 45.0,
) -> str:
    """Send one user turn over the chat WS and collect the streamed reply.

    The chat WS emits ``text`` frames for assistant output and an
    ``idle`` frame each time an ``inject_input`` returns. A plain turn
    streams ``text`` then ``idle``. A sub-agent dispatch can emit an
    *early* ``idle`` (the dispatch turn produces no text) before the
    wrap-up turn streams its reply — so ``idle`` is only treated as
    terminal once a reply has actually been collected. Otherwise the
    helper keeps reading, bounded by ``idle_timeout`` (max gap between
    frames) and ``hard_timeout`` (absolute ceiling). A worker deadlock
    surfaces as the outer ``wait_for`` in the test.
    """
    await ws.send(json.dumps({"type": "input", "content": message}))
    chunks: list[str] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + hard_timeout
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=idle_timeout)
        except asyncio.TimeoutError:
            break
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            continue
        ftype = frame.get("type")
        if ftype in ("text", "text_chunk", "assistant"):
            chunks.append(str(frame.get("content", "")))
        elif ftype == "error":
            raise AssertionError(f"chat WS error frame: {frame.get('content')}")
        elif ftype == "idle" and chunks:
            break
    return "".join(chunks)


@pytest.fixture(autouse=True)
def _isolate_kt_config_dir(tmp_path, monkeypatch):
    """Give every multi-node journey its own ``KT_CONFIG_DIR``.

    ``KT_CONFIG_DIR`` is the single source of truth for the per-user
    config root — the ``config://`` / ``recipe://`` / ``package://``
    file scopes AND every identity store (API keys, LLM profiles, MCP
    servers, Codex tokens, UI prefs) resolve under it. Without a
    per-test override these journeys would read and *write* the
    operator's real ``~/.kohakuterrarium/`` — polluting local config.
    """
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "kt-config"))


# ---------------------------------------------------------------------------
# Harness smoke test — proves the real stack boots and a worker connects.
# ---------------------------------------------------------------------------


async def test_harness_boots_host_and_worker(tmp_path, monkeypatch):
    """The real lab-host boots, a real worker connects over real WebSocket.

    This is the foundation every other test stands on: if the real
    ``create_app(lab-host)`` + real ``HostEngine`` + real
    ``ClientConnector`` can't even establish a connection, nothing else
    is meaningful. No creatures yet — just the transport handshake.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)

    async with RealLabHost(tmp_path) as host:
        # The API is live and answers a basic read.
        resp = await asyncio.wait_for(
            host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
        )
        assert resp.status_code == 200

        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            # The host sees the worker as a connected client.
            await RealLabHost._wait_for(
                lambda: "worker-1" in {c for c in host.host_engine.alive_clients()},
                "worker-1 join",
            )
            assert worker.node_id == "worker-1"


# ---------------------------------------------------------------------------
# Subprocess-harness smoke — proves the real OS-subprocess worker boots,
# joins the host, and shuts down cleanly.  This is the foundation for every
# concurrency / RPC-stall / identity-isolation reproduction below: those
# bugs only manifest with a real cross-process boundary.
# ---------------------------------------------------------------------------


async def test_subprocess_harness_boots_host_and_worker(tmp_path, monkeypatch):
    """A real ``python -m kohakuterrarium lab-client`` subprocess joins.

    Verifies:
      1. The subprocess actually launches (no import-time crash).
      2. The worker dials the host's WebSocket and the host adds it to
         ``alive_clients()``.
      3. The worker has its own ``KT_CONFIG_DIR`` and ``KT_SESSION_DIR``
         that exist on disk and are distinct from the host's.
      4. Clean shutdown via ``terminate()`` (returncode 0 means the
         worker's signal handler took the path, not SIGKILL).
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "sub-worker-1", host.lab_ws_url, tmp_path / "sub-worker-1"
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            assert "sub-worker-1" in set(host.host_engine.alive_clients())
            # Per-worker identity isolation actually present on disk:
            assert worker.kt_config_dir.exists()
            assert worker.kt_session_dir.exists()
            assert worker.kt_config_dir != tmp_path / "kt-config"
        # After __aexit__, the worker process must be gone.
        assert worker.returncode is not None, (
            "worker did not exit after __aexit__; stderr: "
            f"{worker.dump_stderr()[:2000]}"
        )


async def test_cross_node_user_named_channel_wires_both_sides(tmp_path, monkeypatch):
    """User-named channel must survive the cross-node wire path.

    Repro of the "VERY BAD" cross-node channel bug:

      * Spawn creature ``A`` on worker-1.
      * Spawn creature ``B`` on worker-2.
      * User declares channel ``my_channel`` in A's graph
        (POST /api/sessions/topology/{graph_A}/channels).
      * User wires ``A → my_channel`` on A's graph — succeeds today.
      * User wires ``my_channel → B`` on B's graph — **today returns 400**
        because ``my_channel`` doesn't exist on worker-2's graph, and
        :meth:`MultiNodeTerrariumService.wire_creature` simply forwards
        to the receiver's home node without replicating the channel.

    Expected behavior: wire succeeds, and listing channels on both
    graphs shows ``my_channel`` — the user's named channel survives the
    cross-node boundary instead of being orphaned and replaced by an
    auto-named ``a_to_b`` via the frontend's connect-fallback.

    Runs on the real **subprocess** harness so two workers really do
    have separate engines / processes / event loops — the bug cannot
    manifest in the shared-loop in-process harness because every
    "node" sees the same channel registry.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["A reply", "B reply"])
    cfg_a = _write_creature_config(tmp_path, "alpha", "You are alpha.")
    cfg_b = _write_creature_config(tmp_path, "bravo", "You are bravo.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabSubprocessWorker(
                "w1", host.lab_ws_url, tmp_path / "w1", script=["A reply"]
            ) as w1,
            RealLabSubprocessWorker(
                "w2", host.lab_ws_url, tmp_path / "w2", script=["B reply"]
            ) as w2,
        ):
            await w1.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            await w2.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            # --- spawn A on w1, B on w2 --------------------------------
            spawn_a = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_a), "on_node": "w1"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert spawn_a.status_code == 200, (
                f"spawn A failed: {spawn_a.text}\n"
                f"w1 stderr: {w1.dump_stderr()[:1500]}"
            )
            sa = spawn_a.json()
            graph_a = sa["session_id"]
            a_id = sa["creatures"][0]["creature_id"]

            spawn_b = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_b), "on_node": "w2"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert spawn_b.status_code == 200, (
                f"spawn B failed: {spawn_b.text}\n"
                f"w2 stderr: {w2.dump_stderr()[:1500]}"
            )
            sb = spawn_b.json()
            graph_b = sb["session_id"]
            b_id = sb["creatures"][0]["creature_id"]
            assert (
                graph_a != graph_b
            ), "creatures on different workers must start in different graphs"

            # --- declare 'my_channel' in A's graph ---------------------
            ch_resp = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/channels",
                    json={"name": "my_channel", "description": "user-named"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert (
                ch_resp.status_code == 200
            ), f"add_channel failed on graph_a: {ch_resp.text}"

            # --- wire A → my_channel (same graph — should work) --------
            wire_a = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
                    json={"channel": "my_channel", "direction": "send"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert (
                wire_a.status_code == 200
            ), f"same-graph wire failed (sanity check): {wire_a.text}"

            # --- wire my_channel → B (cross-graph — THE BUG) -----------
            wire_b = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_b}/creatures/{b_id}/wire",
                    json={"channel": "my_channel", "direction": "listen"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert wire_b.status_code == 200, (
                f"cross-graph wire of user-named channel failed (THE BUG): "
                f"{wire_b.text}\nw2 stderr: {w2.dump_stderr()[:1500]}"
            )

            # --- both graphs must surface my_channel -------------------
            list_a = await asyncio.wait_for(
                host.http.get(f"/api/sessions/topology/{graph_a}/channels"),
                timeout=OP_TIMEOUT,
            )
            list_b = await asyncio.wait_for(
                host.http.get(f"/api/sessions/topology/{graph_b}/channels"),
                timeout=OP_TIMEOUT,
            )
            assert list_a.status_code == 200 and list_b.status_code == 200
            names_a = {c["name"] for c in list_a.json()}
            names_b = {c["name"] for c in list_b.json()}
            assert (
                "my_channel" in names_a
            ), f"my_channel missing from graph_a after cross-wire: {names_a}"
            assert "my_channel" in names_b, (
                f"my_channel was not replicated to graph_b after cross-wire — "
                f"the channel name is dead cross-node.  graph_b channels: {names_b}"
            )


async def test_worker_graphs_report_their_worker_node_id_not_host(
    tmp_path, monkeypatch
):
    """Worker-hosted graphs must report their own ``node_id`` in the
    snapshot — NOT ``"_host"``.

    Regression for: "in graph editor it shows those graph as 'host'
    which is weird".  The worker's own snapshot reports ``_host``
    because, from the worker's side, IT is the host of its engine.
    The host-side aggregator (in ``MultiNodeService.runtime_graph_snapshot``
    via ``RemoteTerrariumService``) must rewrite that to the worker's
    actual lab ``node_id`` so the graph editor renders the correct
    site chip.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["hi"])
    cfg = _write_creature_config(tmp_path, "nodeid_test", "You are nodeid_test.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "node-w", host.lab_ws_url, tmp_path / "node-w", script=["hi"]
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            spawn = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg), "on_node": "node-w"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert spawn.status_code == 200, spawn.text
            graph_id = spawn.json()["session_id"]

            snap = await asyncio.wait_for(
                host.http.get("/api/runtime/graph"), timeout=OP_TIMEOUT
            )
            assert snap.status_code == 200, snap.text
            graphs = snap.json().get("graphs", [])
            this_graph = next(
                (g for g in graphs if g.get("graph_id") == graph_id), None
            )
            assert (
                this_graph is not None
            ), f"spawned graph missing from snapshot: {graphs}"
            assert this_graph.get("node_id") == "node-w", (
                "worker graph reports wrong node_id; the host-side aggregator "
                f"did not rewrite the worker's self-reported '_host'.  "
                f"node_id={this_graph.get('node_id')!r}"
            )


async def test_wire_new_channel_after_cross_node_setup(tmp_path, monkeypatch):
    """After a→ch1→b cross-node, creating a new channel + wiring b → ch2
    must succeed.

    User report: "after a→1→b then try to b→2 failed".  Sequence:

      1. spawn a on w1, b on w2.
      2. create channel ch1 in a's graph; wire a→ch1 + ch1→b
         (channel cross-node replicates ch1 onto b's graph).
      3. create channel ch2 in the cluster.
      4. wire b → ch2 (either direction).  Currently 400s.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["a", "b"])
    cfg_a = _write_creature_config(tmp_path, "cw_a", "You are A.")
    cfg_b = _write_creature_config(tmp_path, "cw_b", "You are B.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabSubprocessWorker(
                "cw1", host.lab_ws_url, tmp_path / "cw1", script=["a"]
            ) as w1,
            RealLabSubprocessWorker(
                "cw2", host.lab_ws_url, tmp_path / "cw2", script=["b"]
            ) as w2,
        ):
            await w1.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            await w2.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            sa = (
                await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": "cw1"},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
            ).json()
            graph_a = sa["session_id"]
            a_id = sa["creatures"][0]["creature_id"]
            sb = (
                await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_b), "on_node": "cw2"},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
            ).json()
            graph_b = sb["session_id"]
            b_id = sb["creatures"][0]["creature_id"]

            # Step 2: a→ch1→b cross-node.
            await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/channels",
                    json={"name": "ch1"},
                ),
                timeout=OP_TIMEOUT,
            )
            await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
                    json={"channel": "ch1", "direction": "send"},
                ),
                timeout=OP_TIMEOUT,
            )
            r = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_b}/creatures/{b_id}/wire",
                    json={"channel": "ch1", "direction": "listen"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert r.status_code == 200, r.text

            # Step 3: create ch2 on the CLUSTER PRIMARY graph (graph_a
            # since lex-smallest wins) — the frontend renders a single
            # cluster and posts to its representative graph_id.
            r_ch2 = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/channels",
                    json={"name": "ch2"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert r_ch2.status_code == 200, r_ch2.text

            # Step 4: wire b → ch2, using the CLUSTER PRIMARY graph_id
            # in the URL (this is what the frontend posts from the
            # cluster view).  Pre-fix the service used the URL's graph_id
            # verbatim for the worker call, so worker-2 (which has its
            # own graph_b, not graph_a) raised "graph not found" → 400.
            r_wire = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/creatures/{b_id}/wire",
                    json={"channel": "ch2", "direction": "send"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert r_wire.status_code == 200, (
                f"wiring b → ch2 after cross-node setup failed: "
                f"{r_wire.status_code} {r_wire.text}\n"
                f"w2 stderr: {w2.dump_stderr()[:2000]}"
            )


async def test_cross_node_direct_output_wire(tmp_path, monkeypatch):
    """Direct creature→creature output wire across workers must succeed.

    User report: after a→1→b (channel cross-node, works), the user
    tries to wire ``b output → a`` DIRECTLY (creature→creature output
    wire, not via a channel) — 400.  ``a`` and ``b`` live on
    different workers.

    Property: the POST /sessions/wiring/{sid}/creatures/{cid}/outputs
    succeeds with 200 even when the target creature lives on a
    different worker — output-wiring entries are stored on the
    SOURCE creature's agent; emit-time routing is handled by the
    host-relay path (#138).
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["a reply", "b reply"])
    cfg_a = _write_creature_config(tmp_path, "ow_a", "You are A.")
    cfg_b = _write_creature_config(tmp_path, "ow_b", "You are B.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabSubprocessWorker(
                "ow1", host.lab_ws_url, tmp_path / "ow1", script=["a reply"]
            ) as w1,
            RealLabSubprocessWorker(
                "ow2", host.lab_ws_url, tmp_path / "ow2", script=["b reply"]
            ) as w2,
        ):
            await w1.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            await w2.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            spawn_a = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_a), "on_node": "ow1"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert spawn_a.status_code == 200, spawn_a.text
            sa = spawn_a.json()
            a_id = sa["creatures"][0]["creature_id"]

            spawn_b = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_b), "on_node": "ow2"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert spawn_b.status_code == 200, spawn_b.text
            sb = spawn_b.json()
            graph_a = sa["session_id"]
            graph_b = sb["session_id"]
            b_id = sb["creatures"][0]["creature_id"]

            # Pre-state matching user's report: first do a→1→b
            # (channel cross-node wire) before the direct output-wire.
            ch_resp = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/channels",
                    json={"name": "ch1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert ch_resp.status_code == 200, ch_resp.text
            wire_a = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
                    json={"channel": "ch1", "direction": "send"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert wire_a.status_code == 200, wire_a.text
            wire_b = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_b}/creatures/{b_id}/wire",
                    json={"channel": "ch1", "direction": "listen"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert wire_b.status_code == 200, wire_b.text

            # Now the user-reported step: direct b → a output wire
            # across workers (after a→1→b is set up).
            resp = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/wiring/{graph_b}/creatures/{b_id}/outputs",
                    json={
                        "to": a_id,
                        "with_content": True,
                        "prompt_format": "simple",
                        "allow_self_trigger": False,
                    },
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert resp.status_code == 200, (
                f"cross-node creature→creature output wire failed: "
                f"{resp.status_code} {resp.text}\n"
                f"w2 stderr: {w2.dump_stderr()[:2000]}"
            )
            body = resp.json()
            assert body.get("status") == "wired"
            assert body.get("edge_id")


async def test_cross_node_wire_renders_as_single_cluster_graph(tmp_path, monkeypatch):
    """Two workers + cross-node wire = ONE graph in the runtime snapshot.

    The "Laboratory makes N terrariums look like 1" UX invariant.  After
    the user wires creature B on worker-2 to a user-named channel that
    lives on creature A's graph on worker-1, the runtime-graph snapshot
    must return ONE cluster graph (with both creatures + the shared
    channel) — NOT two separate graphs.  Pre-fix the graph editor
    rendered two disconnected nodes and the user-created channel
    appeared to be ignored.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["A reply", "B reply"])
    cfg_a = _write_creature_config(tmp_path, "cluster_a", "You are A.")
    cfg_b = _write_creature_config(tmp_path, "cluster_b", "You are B.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabSubprocessWorker(
                "cw1", host.lab_ws_url, tmp_path / "cw1", script=["A reply"]
            ) as w1,
            RealLabSubprocessWorker(
                "cw2", host.lab_ws_url, tmp_path / "cw2", script=["B reply"]
            ) as w2,
        ):
            await w1.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            await w2.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            spawn_a = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_a), "on_node": "cw1"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert spawn_a.status_code == 200, spawn_a.text
            sa = spawn_a.json()
            graph_a, a_id = sa["session_id"], sa["creatures"][0]["creature_id"]

            spawn_b = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_b), "on_node": "cw2"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert spawn_b.status_code == 200, spawn_b.text
            sb = spawn_b.json()
            graph_b, b_id = sb["session_id"], sb["creatures"][0]["creature_id"]

            # Pre-condition: with no cross-link yet, the snapshot has
            # both engine graphs as separate entries.
            snap_before = await asyncio.wait_for(
                host.http.get("/api/runtime/graph"), timeout=OP_TIMEOUT
            )
            assert snap_before.status_code == 200, snap_before.text
            graph_ids_before = {
                g["graph_id"] for g in snap_before.json().get("graphs", [])
            }
            assert {graph_a, graph_b} <= graph_ids_before

            # User-named channel + cross-node wire.
            await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/channels",
                    json={"name": "cluster_channel"},
                ),
                timeout=OP_TIMEOUT,
            )
            await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
                    json={"channel": "cluster_channel", "direction": "send"},
                ),
                timeout=OP_TIMEOUT,
            )
            wire_b = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{graph_b}/creatures/{b_id}/wire",
                    json={"channel": "cluster_channel", "direction": "listen"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert wire_b.status_code == 200, wire_b.text

            # Post-condition: the snapshot folds both engine graphs
            # into ONE cluster graph.
            snap_after = await asyncio.wait_for(
                host.http.get("/api/runtime/graph"), timeout=OP_TIMEOUT
            )
            assert snap_after.status_code == 200
            graphs = snap_after.json().get("graphs", [])
            # Find the cluster that contains both creatures.
            cluster = next(
                (g for g in graphs if {a_id, b_id} <= set(g.get("creature_ids", []))),
                None,
            )
            assert cluster is not None, (
                "cross-node wire did not produce a cluster graph spanning "
                f"both creatures.  Snapshot graphs: {graphs}"
            )
            assert (
                cluster.get("is_cluster") is True
            ), f"cluster missing is_cluster marker: {cluster}"
            members = {
                (m.get("node_id"), m.get("graph_id"))
                for m in cluster.get("members") or []
            }
            assert members == {
                ("cw1", graph_a),
                ("cw2", graph_b),
            }, f"cluster members mismatch: {members}"
            # The user's named channel survives — no auto-named "a_to_b".
            ch_names = {c.get("name") for c in cluster.get("channels") or []}
            assert (
                "cluster_channel" in ch_names
            ), f"user-named channel lost in cluster: {ch_names}"


async def test_spawn_response_carries_resolved_model(tmp_path, monkeypatch):
    """The session spawn must surface the resolved model name.

    Regression for "session shows 'no model'" bug: even when the host's
    default profile has a token and creation succeeds, the UI was
    rendering "no model" because the spawn response's ``creatures[0]``
    had no ``model`` field — UI fell back to requiring manual selection.

    Property: spawn response includes ``creatures[0].model`` equal to
    the model the agent actually resolved.  Works for worker-hosted
    spawns too — the wire-pack for :class:`CreatureInfo` carries the
    model across nodes.
    """
    import json

    import yaml

    host_cfg = tmp_path / "kt-config"
    host_cfg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))

    script_file = host_cfg / "model_test.json"
    script_file.write_text(json.dumps({"script": ["modelled"]}), encoding="utf-8")
    (host_cfg / "llm_profiles.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 3,
                "backends": {
                    "fake_test": {
                        "backend_type": "fake_test",
                        "base_url": "",
                        "api_key_env": "",
                    }
                },
                "presets": {
                    "fake_test": {
                        "modeltest": {
                            "model": "fake-shiny-model",
                            "max_context": 4096,
                            "max_output": 256,
                            "extra_body": {"script_path": str(script_file)},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (host_cfg / "api_keys.yaml").write_text(
        yaml.safe_dump({"fake_test": "sk-test"}), encoding="utf-8"
    )

    cfg_dir = tmp_path / "creature_modeltest"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(
        "name: modeltest\n"
        "system_prompt: 'You are modeltest.'\n"
        "llm: fake_test/modeltest\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n",
        encoding="utf-8",
    )

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "model-w",
            host.lab_ws_url,
            tmp_path / "model-w",
            use_test_llm_seam=False,
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "model-w"},
                ),
                timeout=OP_TIMEOUT * 4,
            )
            assert resp.status_code == 200, (
                f"spawn failed: {resp.text}\n"
                f"worker stderr: {worker.dump_stderr()[:2000]}"
            )
            body = resp.json()
            creature = body["creatures"][0]
            assert creature.get("model") == "fake-shiny-model", (
                f"spawn response did not carry the resolved model — UI "
                f"will show 'no model'.  creature dict: {creature}"
            )


async def test_subprocess_creation_succeeds_with_no_key(tmp_path, monkeypatch):
    """Creature creation must succeed even when no credentials are set.

    Regression for: "TO be fair you should never CHECK token in
    creation, as user can select model in runtime!".  Spawning a
    creature whose profile has no api key (or no profile at all)
    must NOT fail — the agent boots with a deferred provider, the
    user can pick a model later via switch_model.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))

    cfg_dir = tmp_path / "creature_nokey"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    # Profile name that resolves to nothing — empty config means the
    # agent build hits the "no LLM model configured" path.
    (cfg_dir / "config.yaml").write_text(
        "name: nokey\n"
        "system_prompt: 'You are nokey.'\n"
        "llm: nonexistent/ghost\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n",
        encoding="utf-8",
    )

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "nokey-w",
            host.lab_ws_url,
            tmp_path / "nokey-w",
            use_test_llm_seam=False,
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "nokey-w"},
                ),
                timeout=OP_TIMEOUT * 4,
            )
            assert resp.status_code == 200, (
                "creature creation failed when no LLM was configured — the "
                "agent build should defer the LLM provider instead.  "
                f"Response: {resp.text}\n"
                f"worker stderr: {worker.dump_stderr()[:2000]}"
            )
            body = resp.json()
            # Spawn succeeded — model is empty (deferred) but the
            # session exists and is addressable.
            assert body["creatures"], body
            assert body["creatures"][0].get("model", "") == "", (
                "deferred provider should report empty model; "
                f"creature: {body['creatures'][0]}"
            )


async def test_subprocess_worker_credential_lookup_via_host_identity(
    tmp_path, monkeypatch
):
    """The worker MUST fetch the api key from the host's identity store.

    Repro for the user's report:

        ``API key not found for profile 'defprofile' (worker mode)``

    Property under test: when a creature spawns on a subprocess worker
    using a profile defined ONLY on the host (no key / no profile in
    the worker's ``KT_CONFIG_DIR``), the worker resolves the profile
    + api-key end-to-end via the ``studio.identity`` RPC + the
    :class:`IdentityCache` sync resolver.  A regression here surfaces
    as a ``ValueError("API key not found …")`` raised inside
    :func:`bootstrap.llm._create_from_profile` on the worker.

    Uses the ``fake_test`` backend so the profile resolution path runs
    through ``get_api_key`` exactly like a real provider would — the
    in-process ``ScriptedLLM`` monkeypatch bypasses that chain by
    design and cannot reproduce the bug.
    """
    import json

    import yaml

    host_cfg = tmp_path / "kt-config"
    host_cfg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))

    script_file = host_cfg / "fake_script.json"
    script_file.write_text(
        json.dumps({"script": ["credentials-OK reply"]}), encoding="utf-8"
    )
    # Register ``fake_test`` as both a user backend (so ``backend_type``
    # survives ``_resolve_preset``, which overrides the preset's
    # backend_type from the backend's setting) and a provider under
    # which a preset is declared.
    (host_cfg / "llm_profiles.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 3,
                "backends": {
                    "fake_test": {
                        "backend_type": "fake_test",
                        "base_url": "",
                        "api_key_env": "",
                    }
                },
                "presets": {
                    "fake_test": {
                        "credtest": {
                            "model": "fake-echo",
                            "max_context": 4096,
                            "max_output": 256,
                            "extra_body": {"script_path": str(script_file)},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (host_cfg / "api_keys.yaml").write_text(
        yaml.safe_dump({"fake_test": "sk-host-only-key"}), encoding="utf-8"
    )

    cfg_dir = tmp_path / "creature_credtest"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(
        "name: credtest\n"
        "system_prompt: 'You are credtest.'\n"
        "llm: fake_test/credtest\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n",
        encoding="utf-8",
    )

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "cred-w",
            host.lab_ws_url,
            tmp_path / "cred-w",
            use_test_llm_seam=False,
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            spawn = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "cred-w"},
                ),
                timeout=OP_TIMEOUT * 4,
            )
            assert spawn.status_code == 200, (
                f"spawn failed (rc={worker.returncode}): {spawn.text}\n"
                f"worker stderr: {worker.dump_stderr()[:3000]}"
            )
            sj = spawn.json()
            sid, cid = sj["session_id"], sj["creatures"][0]["creature_id"]

            async with host.api_ws(f"/ws/sessions/{sid}/creatures/{cid}/chat") as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "hello"), timeout=OP_TIMEOUT * 4
                )

            assert "credentials-OK reply" in reply, (
                f"expected scripted reply via fake_test backend, got {reply!r}\n"
                "Most likely the worker failed the credential lookup and the "
                "agent returned an error message instead.\n"
                f"worker stderr: {worker.dump_stderr()[:3000]}"
            )


async def test_model_switch_does_not_break_session_sync_mirror(
    tmp_path, monkeypatch, caplog
):
    """Switching the model on a worker creature must not trigger a
    ``session-sync mirror: append failed`` log on the host.

    Reproduction the user hit live:

        POST /api/sessions/.../creatures/{name}/model 200
        [...session.sync] ERROR session-sync mirror: append failed
            for {graph}/{name}:e000000

    The model-switch path emits a ``session_info`` ``OutputEvent``
    whose metadata carries the new identifier; the worker's
    SessionEventTee forwards that to the host where SessionMirrorWriter
    tries to append it.  Some payload field doesn't round-trip cleanly
    through the wire / kohakuvault packer.
    """
    import json
    import logging

    import yaml

    host_cfg = tmp_path / "kt-config"
    host_cfg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))

    script_a = host_cfg / "model_a.json"
    script_a.write_text(json.dumps({"script": ["from A"]}), encoding="utf-8")
    script_b = host_cfg / "model_b.json"
    script_b.write_text(json.dumps({"script": ["from B"]}), encoding="utf-8")
    (host_cfg / "llm_profiles.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 3,
                "backends": {
                    "fake_test": {
                        "backend_type": "fake_test",
                        "base_url": "",
                        "api_key_env": "",
                    }
                },
                "presets": {
                    "fake_test": {
                        "model_a": {
                            "model": "fake-a",
                            "max_context": 4096,
                            "max_output": 256,
                            "extra_body": {"script_path": str(script_a)},
                        },
                        "model_b": {
                            "model": "fake-b",
                            "max_context": 4096,
                            "max_output": 256,
                            "extra_body": {"script_path": str(script_b)},
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (host_cfg / "api_keys.yaml").write_text(
        yaml.safe_dump({"fake_test": "sk-host"}), encoding="utf-8"
    )

    cfg_dir = tmp_path / "creature_switch"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    # Hyphenated name matches the user's reported case ("rolling-thicket")
    # — agent keys with a hyphen are a slightly different path through
    # ``_agent_from_key`` and the kohakuvault scoped dict.
    (cfg_dir / "config.yaml").write_text(
        "name: rolling-thicket\n"
        "system_prompt: 'You are rolling-thicket.'\n"
        "llm: fake_test/model_a\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n",
        encoding="utf-8",
    )

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "switch-w",
            host.lab_ws_url,
            tmp_path / "switch-w",
            use_test_llm_seam=False,
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            with caplog.at_level(
                logging.WARNING, logger="kohakuterrarium.session.sync"
            ):
                spawn = await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_dir), "on_node": "switch-w"},
                    ),
                    timeout=OP_TIMEOUT * 4,
                )
                assert spawn.status_code == 200, spawn.text
                sj = spawn.json()
                sid, cid = sj["session_id"], sj["creatures"][0]["creature_id"]

                # Switch model FIRST — user's e000000 error indicated the
                # bug fires when the model-switch event is the creature's
                # very first event (no chat yet).
                switch_resp = await asyncio.wait_for(
                    host.http.post(
                        f"/api/sessions/{sid}/creatures/{cid}/model",
                        json={"model": "fake_test/model_b"},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
                assert switch_resp.status_code == 200, switch_resp.text

                # Then a chat turn so a real event passes through too.
                async with host.api_ws(
                    f"/ws/sessions/{sid}/creatures/{cid}/chat"
                ) as ws:
                    await asyncio.wait_for(
                        _drain_chat_ws(ws, "hi"), timeout=OP_TIMEOUT * 2
                    )

                await asyncio.sleep(0.6)

            failures = [
                r
                for r in caplog.records
                if "session-sync mirror" in r.getMessage()
                and (
                    "append failed" in r.getMessage()
                    or "meta apply failed" in r.getMessage()
                )
            ]
            assert not failures, (
                "model switch triggered SessionMirrorWriter append failures: "
                f"{[r.getMessage() for r in failures]}\n"
                f"first traceback: {failures[0].exc_text if failures else ''}\n"
                f"worker stderr: {worker.dump_stderr()[:2500]}"
            )


async def test_session_sync_mirror_no_append_failures(tmp_path, monkeypatch, caplog):
    """No ``session-sync mirror: append failed`` log after a real creature
    spawn + chat over the subprocess wire.

    Repro for the user-reported error:

        ``session-sync mirror: append failed for graph_…/<name>:e000000``

    The error fires when the host's :class:`SessionMirrorWriter` cannot
    append a worker-emitted event to its local mirror store.  The
    operator sees it logged right after creature creation.  The mirror
    is best-effort, so the worker-side state is fine — but the
    controller's read-side (history, viewer, fork) loses events, which
    is a real correctness regression downstream.

    Subprocess harness is the faithful path: the bug needs the real
    msgpack-over-wire round-trip for the event payload.
    """
    import logging

    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["mirror reply"])
    cfg_dir = _write_creature_config(tmp_path, "humble_porch", "You are humble_porch.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "mirror-w", host.lab_ws_url, tmp_path / "mirror-w", script=["mirror reply"]
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            with caplog.at_level(
                logging.WARNING, logger="kohakuterrarium.session.sync"
            ):
                spawn = await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_dir), "on_node": "mirror-w"},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
                assert spawn.status_code == 200, spawn.text
                sj = spawn.json()
                sid, cid = sj["session_id"], sj["creatures"][0]["creature_id"]

                # Drive a real chat turn so a controller-bound event
                # ("user_input", text chunks, ...) flows through the
                # mirror writer — the first event is e000000.
                async with host.api_ws(
                    f"/ws/sessions/{sid}/creatures/{cid}/chat"
                ) as ws:
                    await asyncio.wait_for(
                        _drain_chat_ws(ws, "hello"), timeout=OP_TIMEOUT * 2
                    )

                # Give the host's mirror writer a moment to drain the
                # last few events queued during the chat turn.
                await asyncio.sleep(0.5)

            failures = [
                r
                for r in caplog.records
                if "session-sync mirror" in r.getMessage()
                and (
                    "append failed" in r.getMessage()
                    or "meta apply failed" in r.getMessage()
                )
            ]
            assert not failures, (
                "SessionMirrorWriter logged append/meta failures during "
                f"normal creature spawn+chat over the wire: "
                f"{[r.getMessage() for r in failures]}\n"
                f"first traceback: {failures[0].exc_text if failures else ''}\n"
                f"worker stderr: {worker.dump_stderr()[:1500]}"
            )


async def test_session_sync_mirror_survives_multi_turn_load(
    tmp_path, monkeypatch, caplog
):
    """Strong guard for the session-sync mirror: drive multi-turn load
    + a model switch + a topology event, then assert no
    ``session-sync mirror: append failed`` / ``meta apply failed``
    surfaced in the host's logs.

    Each event type exercises a different payload shape that the
    mirror writer has to round-trip through kohakuvault.  A regression
    in any of them shows up here before users hit it in production.
    """
    import logging

    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(
        monkeypatch,
        script=["first reply", "second reply", "third reply"],
    )
    cfg_dir = _write_creature_config(tmp_path, "ledger", "You are ledger.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "mirror-load",
            host.lab_ws_url,
            tmp_path / "mirror-load",
            script=["first reply", "second reply", "third reply"],
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)
            with caplog.at_level(
                logging.WARNING, logger="kohakuterrarium.session.sync"
            ):
                spawn = await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_dir), "on_node": "mirror-load"},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
                assert spawn.status_code == 200, spawn.text
                sj = spawn.json()
                sid, cid = sj["session_id"], sj["creatures"][0]["creature_id"]

                # Three real chat turns — fan out user_input events,
                # assistant text chunks, and turn-complete markers.
                for prompt in ("alpha", "bravo", "charlie"):
                    async with host.api_ws(
                        f"/ws/sessions/{sid}/creatures/{cid}/chat"
                    ) as ws:
                        await asyncio.wait_for(
                            _drain_chat_ws(ws, prompt), timeout=OP_TIMEOUT * 2
                        )

                await asyncio.sleep(0.5)  # let mirror drain

            failures = [
                r
                for r in caplog.records
                if "session-sync mirror" in r.getMessage()
                and (
                    "append failed" in r.getMessage()
                    or "meta apply failed" in r.getMessage()
                )
            ]
            assert not failures, (
                "SessionMirrorWriter logged failures under multi-turn load: "
                f"{[r.getMessage() for r in failures]}\n"
                f"first traceback: {failures[0].exc_text if failures else ''}\n"
                f"worker stderr: {worker.dump_stderr()[:1500]}"
            )


async def test_subprocess_worker_spawn_and_chat_round_trip(tmp_path, monkeypatch):
    """End-to-end smoke through a real subprocess worker.

    Drives the **full** real path — spawn via HTTP, chat via WebSocket —
    against a worker that is an actual OS subprocess.  Verifies:

      1. The host can spawn a creature on the subprocess worker
         (``studio.deploy.push_creature_bundle`` + ``add_creature``
         survive the process boundary).
      2. The chat WS streams the scripted reply back — the
         ``KT_TEST_LLM_SCRIPT`` seam works inside the subprocess.

    If this passes, the harness is ready for cross-process bug
    reproductions.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["sub-scout reporting in"])
    cfg_dir = _write_creature_config(tmp_path, "sub_scout", "You are sub_scout.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabSubprocessWorker(
            "sub-worker-1",
            host.lab_ws_url,
            tmp_path / "sub-worker-1",
            script=["sub-scout reporting in"],
        ) as worker:
            await worker.wait_for_join(host, timeout=OP_TIMEOUT * 4)

            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "sub-worker-1"},
                ),
                timeout=OP_TIMEOUT * 2,
            )
            assert resp.status_code == 200, (
                f"spawn failed (rc={worker.returncode}): {resp.text}\n"
                f"worker stderr: {worker.dump_stderr()[:2000]}"
            )
            session = resp.json()
            session_id = session["session_id"]
            assert session["creatures"], f"spawn returned no creatures: {session}"
            creature_entry = session["creatures"][0]
            creature_id = creature_entry["creature_id"]
            assert creature_entry.get("home_node") == "sub-worker-1"

            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "report"), timeout=OP_TIMEOUT * 2
                )
            assert "sub-scout reporting in" in reply, (
                f"chat reply {reply!r} missing scripted text — the "
                "KT_TEST_LLM_SCRIPT seam did not take effect inside the "
                f"subprocess.  Worker stderr: {worker.dump_stderr()[:2000]}"
            )


# ---------------------------------------------------------------------------
# Headline journey — spawn / chat / model / stop a creature ON A WORKER.
# This is the single most important multi-node behavior: a creature on a
# worker must behave identically to a local creature (the standalone
# path is the reference). Every step is wrapped in wait_for so a
# loop-blocking deadlock names the stuck operation instead of hanging.
# ---------------------------------------------------------------------------


async def test_spawn_chat_model_stop_creature_on_worker(tmp_path, monkeypatch):
    """The full per-creature lifecycle, driven through the real API,
    against a creature that lives ON A WORKER.

    Behavior contract (standalone is the reference):

    1. spawn (POST /api/sessions/active/creature, on_node=worker-1) →
       the creature lands on the WORKER's engine, and the host runs no
       agent of its own (only workers run agents).
    2. naming → the creature's name is exactly the configured name,
       not a mangled id.
    3. listing (GET /api/sessions/active) → the creature shows up.
    4. chat (WS /ws/sessions/{sid}/creatures/{cid}/chat) → a user turn
       streams the scripted reply back.
    5. model switch (POST .../model) → succeeds and is reflected.
    6. stop (DELETE /api/sessions/active/agents/{cid}) → the creature
       is gone from the worker's engine; the call does not deadlock.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["scout reporting in"])
    cfg_dir = _write_creature_config(tmp_path, "scout", "You are the scout.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )

            # --- 1. spawn on the worker --------------------------------
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session = resp.json()
            session_id = session["session_id"]
            # The session handle carries its creatures inline; the real
            # (engine-assigned) creature_id lives there, not top-level.
            assert session["creatures"], f"spawn returned no creatures: {session}"
            creature_entry = session["creatures"][0]
            creature_id = creature_entry["creature_id"]
            # The spawn handle must report the worker as the home node —
            # not "_host". A creature spawned on a worker is NOT host-local.
            assert creature_entry.get("home_node") == "worker-1", (
                f"spawn handle home_node {creature_entry.get('home_node')!r} "
                "!= 'worker-1' — remote spawn rendered as host-local"
            )

            # The creature runs on the WORKER's engine — only workers run
            # agents in multi-node mode.
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "creature appears on worker engine",
            )
            worker_ids = {c.creature_id for c in worker.engine.list_creatures()}
            assert creature_id in worker_ids, (
                f"creature {creature_id!r} not on worker engine "
                f"(worker has {worker_ids})"
            )

            # --- 2. naming — exactly the configured name ---------------
            worker_creature = worker.engine.get_creature(creature_id)
            assert worker_creature.name == "scout", (
                f"worker creature name {worker_creature.name!r} != configured "
                "'scout' — naming not threaded through the spawn path"
            )

            # --- 3. listing surfaces it --------------------------------
            listed = await asyncio.wait_for(
                host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
            )
            assert listed.status_code == 200
            names = json.dumps(listed.json())
            assert "scout" in names, f"scout missing from active list: {names}"

            # --- 4. chat over the WS streams the scripted reply --------
            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "report"), timeout=OP_TIMEOUT
                )
            assert "scout reporting in" in reply, (
                f"chat reply {reply!r} missing scripted text — chat not "
                "routed to the worker creature"
            )

            # --- 5. model switch routes to the worker ------------------
            model_resp = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/{session_id}/creatures/{creature_id}/model",
                    json={"model": "openai/gpt-4o-mini"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert (
                model_resp.status_code == 200
            ), f"model switch failed: {model_resp.text}"

            # --- 6. stop — no deadlock, creature gone from the worker --
            stop_resp = await asyncio.wait_for(
                host.http.delete(f"/api/sessions/active/agents/{creature_id}"),
                timeout=OP_TIMEOUT,
            )
            assert stop_resp.status_code in (200, 204), f"stop failed: {stop_resp.text}"
            await RealLabHost._wait_for(
                lambda: creature_id
                not in {c.creature_id for c in worker.engine.list_creatures()},
                "creature removed from worker engine after stop",
            )


# ---------------------------------------------------------------------------
# Worker disconnect — the reported bug: "worker disconnected, the creature
# is still marked running, and trying to stop it never works." Correct
# behavior: when a worker leaves, its creatures must stop being reported
# as live, the API must stay responsive, and a stop of the gone creature
# must return cleanly (not hang / deadlock).
# ---------------------------------------------------------------------------


async def test_worker_disconnect_drops_creature_and_keeps_api_responsive(
    tmp_path, monkeypatch
):
    """A worker that disconnects must not leave a zombie creature.

    1. spawn a creature on worker-1.
    2. worker-1 disconnects (clean leave — the host gets a LEFT).
    3. the host's active-creature list must STOP reporting it (the bug:
       it stays "running" forever).
    4. the API must stay responsive — a follow-up read returns promptly,
       proving the disconnect didn't wedge the event loop.
    5. stopping the now-gone creature must return cleanly (a 404 is
       fine — a hang / 500 / deadlock is not).
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["ok"])
    cfg_dir = _write_creature_config(tmp_path, "ghost", "You are ghost.")

    async with RealLabHost(tmp_path) as host:
        worker = RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1")
        await worker.__aenter__()
        worker_closed = False
        try:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            spawned = resp.json()
            session_id = spawned["session_id"]
            creature_id = spawned["creatures"][0]["creature_id"]

            # The session is live and listed against worker-1.
            listed = await asyncio.wait_for(
                host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
            )
            live_ids = {s["session_id"] for s in listed.json()}
            assert (
                session_id in live_ids
            ), f"session {session_id!r} not in active list {listed.json()}"

            # --- worker-1 leaves --------------------------------------
            await worker.__aexit__(None, None, None)
            worker_closed = True
            await RealLabHost._wait_for(
                lambda: "worker-1" not in set(host.host_engine.alive_clients()),
                "host registers worker-1 LEFT",
            )

            # --- the API must stay responsive -------------------------
            stats = await asyncio.wait_for(
                host.http.get("/api/sessions/stats"), timeout=OP_TIMEOUT
            )
            assert stats.status_code == 200, "API wedged after worker disconnect"

            # --- the session must no longer be reported as live ------
            listed_after = await asyncio.wait_for(
                host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
            )
            after_ids = {s["session_id"] for s in listed_after.json()}
            assert session_id not in after_ids, (
                f"session {session_id!r} still listed as live after its "
                f"worker disconnected — zombie session: {listed_after.json()}"
            )

            # --- stopping the gone creature must return cleanly -------
            stop_resp = await asyncio.wait_for(
                host.http.delete(f"/api/sessions/active/agents/{creature_id}"),
                timeout=OP_TIMEOUT,
            )
            assert stop_resp.status_code in (200, 204, 404), (
                f"stop of a gone creature returned {stop_resp.status_code} "
                f"(expected a clean 200/204/404): {stop_resp.text}"
            )
        finally:
            if not worker_closed:
                await worker.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Goal #3: local is local, multi-node is multi-node — NEVER MIX. In
# lab-host mode the host is a coordinator (Studio + lab server); ONLY a
# worker process may run an agent. Spawning a creature with no worker
# target must be rejected, not silently run on the host.
# ---------------------------------------------------------------------------


async def test_lab_host_runs_no_agents(tmp_path, monkeypatch):
    """In lab-host mode the host must not host creatures of its own.

    A spawn with no ``on_node`` (or ``on_node="_host"``) has nowhere
    legitimate to run — the host process is a coordinator, not an agent
    runtime. It must be rejected with a clear error, NOT silently
    spawned on a host-local engine (the dual local/remote path is the
    root of the multi-node deadlocks).
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_dir = _write_creature_config(tmp_path, "stray", "You are stray.")

    async with RealLabHost(tmp_path) as host:
        # No worker connected — and even so, a host-targeted spawn must fail.
        resp = await asyncio.wait_for(
            host.http.post(
                "/api/sessions/active/creature",
                json={"config_path": str(cfg_dir)},  # no on_node → host
            ),
            timeout=OP_TIMEOUT,
        )
        assert resp.status_code >= 400, (
            "lab-host accepted a host-local creature spawn — only worker "
            f"processes may run agents (got {resp.status_code}: {resp.text})"
        )

        # Explicit on_node="_host" is equally illegal in lab-host mode.
        resp_explicit = await asyncio.wait_for(
            host.http.post(
                "/api/sessions/active/creature",
                json={"config_path": str(cfg_dir), "on_node": "_host"},
            ),
            timeout=OP_TIMEOUT,
        )
        assert (
            resp_explicit.status_code >= 400
        ), "lab-host accepted on_node='_host' — the host runs no agents"

        # The active list must be empty — nothing ran on the host.
        listed = await asyncio.wait_for(
            host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
        )
        assert (
            listed.json() == []
        ), f"host has live sessions in lab-host mode: {listed.json()}"


# ---------------------------------------------------------------------------
# The reported deadlock: with creatures across nodes, "doing some
# operation like stop creature" wedges the whole system. Correct
# behavior: stopping one worker's creature leaves the other worker's
# creature — and the API — fully responsive.
# ---------------------------------------------------------------------------


async def test_two_workers_stop_one_keeps_everything_responsive(tmp_path, monkeypatch):
    """Two creatures on two workers; stopping one must not wedge the rest.

    1. spawn creature-a on worker-1, creature-b on worker-2.
    2. chat with BOTH (proves both are independently live).
    3. stop creature-a.
    4. the API stays responsive AND creature-b is still chat-able —
       no cross-node deadlock from the stop.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    # Content-matched script: each creature has its own ScriptedLLM
    # instance, so position-indexed scripts would all start at entry 0.
    # ``match`` keys the reply to the user message instead.
    install_scripted_llm(
        monkeypatch,
        script=[
            ScriptEntry("reply-a", match="hi a"),
            ScriptEntry("reply-b2", match="still there"),
            ScriptEntry("reply-b", match="hi b"),
        ],
    )
    cfg_a = _write_creature_config(tmp_path, "alpha", "You are alpha.")
    cfg_b = _write_creature_config(tmp_path, "beta", "You are beta.")

    async with RealLabHost(tmp_path) as host:
        # The two workers are driven entirely through the host API — no
        # direct handle needed, just their live connections.
        async with (
            RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"),
            RealLabWorker("worker-2", host.lab_ws_url, tmp_path / "worker-2"),
        ):
            await RealLabHost._wait_for(
                lambda: {"worker-1", "worker-2"}
                <= set(host.host_engine.alive_clients()),
                "both workers join",
            )

            async def _spawn(cfg_dir, node):
                r = await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_dir), "on_node": node},
                    ),
                    timeout=OP_TIMEOUT,
                )
                assert r.status_code == 200, f"spawn on {node} failed: {r.text}"
                body = r.json()
                return body["session_id"], body["creatures"][0]["creature_id"]

            sid_a, cid_a = await _spawn(cfg_a, "worker-1")
            sid_b, cid_b = await _spawn(cfg_b, "worker-2")

            async def _chat(sid, cid, msg):
                path = f"/ws/sessions/{sid}/creatures/{cid}/chat"
                async with host.api_ws(path) as ws:
                    return await asyncio.wait_for(
                        _drain_chat_ws(ws, msg), timeout=OP_TIMEOUT
                    )

            # Both creatures are independently live.
            assert "reply-a" in await _chat(sid_a, cid_a, "hi a")
            assert "reply-b" in await _chat(sid_b, cid_b, "hi b")

            # --- stop creature-a on worker-1 --------------------------
            stop = await asyncio.wait_for(
                host.http.delete(f"/api/sessions/active/agents/{cid_a}"),
                timeout=OP_TIMEOUT,
            )
            assert stop.status_code in (200, 204), f"stop a failed: {stop.text}"

            # --- the API stays responsive -----------------------------
            listed = await asyncio.wait_for(
                host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
            )
            assert listed.status_code == 200, "API wedged after cross-node stop"
            live = {s["session_id"] for s in listed.json()}
            assert sid_a not in live, "stopped creature-a still listed"
            assert sid_b in live, "creature-b vanished when creature-a stopped"

            # --- creature-b is still fully chat-able ------------------
            assert "reply-b2" in await _chat(sid_b, cid_b, "still there?"), (
                "creature-b unresponsive after creature-a was stopped — "
                "cross-node operation wedged an unrelated worker"
            )


# ---------------------------------------------------------------------------
# Naming — the reported bug: "the remote node agent never follows the
# user's naming when added from the frontend". The frontend sends a
# ``name`` in the spawn payload; the worker creature must adopt it.
# ---------------------------------------------------------------------------


async def test_frontend_name_is_threaded_to_worker_creature(tmp_path, monkeypatch):
    """An explicit ``name`` in the spawn payload reaches the worker creature.

    The frontend's creature-create form sends ``{config_path, on_node,
    name}``. The worker creature's display name must be the name the
    user typed — not the config's own ``name`` and not a mangled id.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    # Config's own name is "configname"; the user types something else.
    cfg_dir = _write_creature_config(tmp_path, "configname", "You are it.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={
                        "config_path": str(cfg_dir),
                        "on_node": "worker-1",
                        "name": "user-chosen-name",
                    },
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            entry = resp.json()["creatures"][0]
            assert entry["name"] == "user-chosen-name", (
                f"spawn handle name {entry['name']!r} != the user's "
                "'user-chosen-name' — naming not threaded from the payload"
            )
            # And the worker's actual creature carries that name.
            creature_id = entry["creature_id"]
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "creature on worker engine",
            )
            wc = worker.engine.get_creature(creature_id)
            assert (
                wc.name == "user-chosen-name"
            ), f"worker creature name {wc.name!r} != 'user-chosen-name'"


# ---------------------------------------------------------------------------
# Worker IO — the reported bug: "a creature created on a worker still
# directly starts the default IO and has no IO attach, the user can't
# interact". A creature spawned through the API/lab path must NOT boot
# its config's own input loop (``input: cli``); the controller attaches
# over the chat WebSocket instead — exactly as a host-local creature
# spawned through the API does.
# ---------------------------------------------------------------------------


async def test_worker_creature_does_not_self_start_config_io(tmp_path, monkeypatch):
    """A worker creature spawned via the API does not run its config IO.

    The creature config declares ``input: cli``. A creature authored
    for ``kt run`` carries that, but when spawned through the web/lab
    path the runtime must suppress the config's own input loop — the
    controller drives input over the attach WebSocket. If the worker
    boots the cli loop, the creature is double-driven and the WS attach
    fights the stdin loop.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["io check reply"])
    cfg_dir = _write_creature_config(tmp_path, "ioagent", "You are io.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "creature on worker engine",
            )

            # The worker creature's agent must NOT be running a cli
            # input module — the API-spawn path suppresses config IO so
            # the attach WebSocket is the sole input driver.
            agent = worker.engine.get_creature(creature_id).agent
            input_mod = getattr(agent, "input", None) or getattr(
                agent, "input_module", None
            )
            input_type = type(input_mod).__name__ if input_mod is not None else None
            assert input_type in (None, "NoneInput"), (
                f"worker creature booted its config IO ({input_type}) — "
                "API-spawned creatures must have input suppressed for attach"
            )

            # And the attach WS is the working input path — a turn over
            # the WebSocket streams the reply.
            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "ping"), timeout=OP_TIMEOUT
                )
            assert "io check reply" in reply, (
                "attach WebSocket is not the working input path for the "
                "worker creature"
            )


# ---------------------------------------------------------------------------
# Runtime graph — the macro-shell graph view. ``GET /api/runtime/graph``
# must show creatures that live on workers; if it only walks the host
# engine the multi-node graph editor renders empty.
# ---------------------------------------------------------------------------


async def test_runtime_graph_includes_worker_creatures(tmp_path, monkeypatch):
    """``GET /api/runtime/graph`` surfaces worker-hosted creatures.

    The graph editor reads this snapshot. In lab-host mode the host
    engine hosts nothing — so a snapshot that only walks the host
    engine renders an empty graph even though workers have creatures.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_dir = _write_creature_config(tmp_path, "grapher", "You are graphed.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            creature_id = resp.json()["creatures"][0]["creature_id"]
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "creature on worker engine",
            )

            graph_resp = await asyncio.wait_for(
                host.http.get("/api/runtime/graph"), timeout=OP_TIMEOUT
            )
            assert (
                graph_resp.status_code == 200
            ), f"runtime graph failed: {graph_resp.text}"
            blob = json.dumps(graph_resp.json())
            assert creature_id in blob, (
                f"worker creature {creature_id!r} missing from the runtime "
                f"graph snapshot — graph editor would render empty: {blob}"
            )


# ---------------------------------------------------------------------------
# Interrupt — a per-creature control op. Interrupting a worker creature
# must route to its home node, not 404 on the host engine.
# ---------------------------------------------------------------------------


async def test_interrupt_worker_creature_routes_to_home(tmp_path, monkeypatch):
    """``POST .../interrupt`` reaches a worker-hosted creature.

    Interrupt is a per-creature control op; like stop/chat/model it
    must route by the creature's home node. On the host engine the
    worker creature doesn't exist, so a host-only lookup 404s.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_dir = _write_creature_config(tmp_path, "interruptme", "You are it.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "creature on worker engine",
            )

            # Interrupt routes to the worker — a clean response, not a
            # 404 from a host-engine lookup and not a hang.
            base = f"/api/sessions/{session_id}/creatures/{creature_id}"
            intr = await asyncio.wait_for(
                host.http.post(f"{base}/interrupt"), timeout=OP_TIMEOUT
            )
            assert intr.status_code in (200, 204), (
                f"interrupt did not route to the worker creature: "
                f"{intr.status_code} {intr.text}"
            )

            # The creature is still alive + chat-able after the interrupt.
            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "still alive?"), timeout=OP_TIMEOUT
                )
            assert reply, "worker creature unresponsive after interrupt"


# ---------------------------------------------------------------------------
# The per-creature management surface — every read/mutate endpoint the
# macro-shell inspector panel hits. On a worker creature each one must
# route to the home node and return the same shape a host-local creature
# does (the standalone path is the reference). One fat journey.
# ---------------------------------------------------------------------------


async def test_worker_creature_management_surface(tmp_path, monkeypatch):
    """Every per-creature inspector endpoint works against a worker creature.

    Drives, in one journey: env / system-prompt / triggers reads,
    scratchpad patch + read-back, plugin list + toggle, module list,
    history, chat-branches. Each routes through the service to the
    worker; a host-only ``as_engine`` reach-in would 404 every one.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["managed reply"])
    cfg_dir = _write_creature_config(tmp_path, "managed", "You are managed.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]
            base = f"/api/sessions/{session_id}/creatures/{creature_id}"

            async def _get(path, what):
                r = await asyncio.wait_for(
                    host.http.get(f"{base}{path}"), timeout=OP_TIMEOUT
                )
                assert r.status_code == 200, (
                    f"{what} ({path}) failed for worker creature: "
                    f"{r.status_code} {r.text}"
                )
                return r.json()

            # --- read surface — env / system-prompt / triggers --------
            env = await _get("/env", "env")
            assert env.get("pwd"), f"env missing pwd: {env}"
            sysprompt = await _get("/system-prompt", "system-prompt")
            assert "managed" in str(
                sysprompt
            ), f"system-prompt not the worker creature's: {sysprompt}"
            triggers = await _get("/triggers", "triggers")
            assert triggers == [], f"unexpected triggers: {triggers}"

            # --- scratchpad patch + read-back -------------------------
            patch = await asyncio.wait_for(
                host.http.patch(
                    f"{base}/scratchpad",
                    json={"updates": {"focus": "multi-node"}},
                ),
                timeout=OP_TIMEOUT,
            )
            assert (
                patch.status_code == 200
            ), f"scratchpad patch failed on worker: {patch.text}"
            pad = await _get("/scratchpad", "scratchpad")
            assert (
                pad.get("focus") == "multi-node"
            ), f"scratchpad patch did not reach the worker creature: {pad}"

            # --- plugin list + module list ----------------------------
            plugins = await _get("/plugins", "plugins")
            assert isinstance(plugins, list)
            modules = await _get("/modules", "modules")
            assert isinstance(modules.get("modules", modules), (list, dict))

            # --- history + branches -----------------------------------
            history = await _get("/history", "history")
            assert (
                "messages" in history or "events" in history
            ), f"history payload malformed: {history}"
            await _get("/branches", "branches")


# ---------------------------------------------------------------------------
# Plugin toggle — a per-creature *mutation* that must take effect ON THE
# WORKER. A host-engine reach-in would 404; a no-op would silently leave
# the plugin in its old state.
# ---------------------------------------------------------------------------


async def test_plugin_toggle_on_worker_creature(tmp_path, monkeypatch):
    """Toggling a plugin on a worker creature actually flips its state.

    The creature carries the builtin ``sandbox`` plugin. A toggle
    routed to the worker must flip ``enabled`` and the change must be
    observable on a follow-up read — proving the mutation reached the
    worker's real Agent, not a host-side phantom.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_dir = _write_creature_config(tmp_path, "plugged", "You are plugged.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]
            base = f"/api/sessions/{session_id}/creatures/{creature_id}"

            async def _modules():
                r = await asyncio.wait_for(
                    host.http.get(f"{base}/modules"), timeout=OP_TIMEOUT
                )
                assert r.status_code == 200, f"modules read failed: {r.text}"
                body = r.json()
                mods = body.get("modules", body) if isinstance(body, dict) else body
                return {m["name"]: m for m in mods if isinstance(m, dict)}

            mods = await _modules()
            assert "sandbox" in mods, (
                f"worker creature has no 'sandbox' plugin to toggle: " f"{sorted(mods)}"
            )
            before = mods["sandbox"].get("enabled")

            # Toggle it through the unified module-toggle route.
            toggled = await asyncio.wait_for(
                host.http.post(f"{base}/modules/plugin/sandbox/toggle"),
                timeout=OP_TIMEOUT,
            )
            assert toggled.status_code == 200, (
                f"plugin toggle did not route to the worker: "
                f"{toggled.status_code} {toggled.text}"
            )

            # The change is observable on the worker creature.
            after = (await _modules())["sandbox"].get("enabled")
            assert after != before, (
                f"sandbox 'enabled' did not flip on the worker creature "
                f"(before={before}, after={after}) — toggle was a no-op"
            )


# ---------------------------------------------------------------------------
# Channels / topology on a worker session — add a channel via the API
# and list it back. The add is service-routed; if the list/inspect
# endpoints reach into the host engine instead, a worker session's
# channels are write-only — invisible to the graph editor.
# ---------------------------------------------------------------------------


async def test_channel_add_and_list_on_worker_session(tmp_path, monkeypatch):
    """A channel added to a worker session is listed back by the API.

    ``POST .../channels`` and ``GET .../channels`` must agree — both
    routing to the worker that hosts the graph. A list endpoint that
    walks the host engine would show nothing for a worker session even
    right after a successful add.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_dir = _write_creature_config(tmp_path, "channeler", "You route.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            topo = f"/api/sessions/topology/{session_id}"

            # Add a channel — service-routed, reaches the worker.
            add = await asyncio.wait_for(
                host.http.post(
                    f"{topo}/channels",
                    json={"name": "relay", "description": "worker channel"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert add.status_code in (
                200,
                201,
            ), f"add channel on worker session failed: {add.text}"

            # List it back — must route to the worker too, not the host
            # engine.  This is the asymmetry that makes a worker
            # session's channels write-only.
            listed = await asyncio.wait_for(
                host.http.get(f"{topo}/channels"), timeout=OP_TIMEOUT
            )
            assert listed.status_code == 200, (
                f"list channels on worker session failed: "
                f"{listed.status_code} {listed.text}"
            )
            names = json.dumps(listed.json())
            assert "relay" in names, (
                f"channel 'relay' added to the worker session is not listed "
                f"back — list endpoint reaches the host engine, not the "
                f"worker: {names}"
            )


# ---------------------------------------------------------------------------
# Connect two creatures that live on the SAME worker — the channel /
# graph-merge path. ``connect`` must route to the worker, merge the two
# singleton graphs there, and the resulting channel must be listable.
# ---------------------------------------------------------------------------


async def test_connect_two_creatures_on_one_worker(tmp_path, monkeypatch):
    """``connect`` wires two same-worker creatures into one graph.

    Spawn two creatures on worker-1 (each a singleton graph), then
    ``POST .../connect`` them. The connect must route to the worker,
    merge the graphs on the worker, and the new channel must show up
    in the merged session's channel list.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_a = _write_creature_config(tmp_path, "sender", "You send.")
    cfg_b = _write_creature_config(tmp_path, "receiver", "You receive.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )

            async def _spawn(cfg_dir):
                r = await asyncio.wait_for(
                    host.http.post(
                        "/api/sessions/active/creature",
                        json={
                            "config_path": str(cfg_dir),
                            "on_node": "worker-1",
                        },
                    ),
                    timeout=OP_TIMEOUT,
                )
                assert r.status_code == 200, f"spawn failed: {r.text}"
                b = r.json()
                return b["session_id"], b["creatures"][0]["creature_id"]

            sid_a, cid_a = await _spawn(cfg_a)
            sid_b, cid_b = await _spawn(cfg_b)
            await RealLabHost._wait_for(
                lambda: len(worker.engine.list_creatures()) == 2,
                "both creatures on worker engine",
            )

            # Connect them — routes to the worker, merges the graphs.
            conn = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{sid_a}/connect",
                    json={"sender": cid_a, "receiver": cid_b},
                ),
                timeout=OP_TIMEOUT,
            )
            assert conn.status_code == 200, (
                f"connect of two same-worker creatures failed: "
                f"{conn.status_code} {conn.text}"
            )

            # The two creatures now share ONE graph on the worker.
            await RealLabHost._wait_for(
                lambda: len({c.graph_id for c in worker.engine.list_creatures()}) == 1,
                "creatures merged into one graph on the worker",
            )
            merged_gid = next(
                iter({c.graph_id for c in worker.engine.list_creatures()})
            )

            # The connect channel is listable on the merged session.
            chans = await asyncio.wait_for(
                host.http.get(f"/api/sessions/topology/{merged_gid}/channels"),
                timeout=OP_TIMEOUT,
            )
            assert (
                chans.status_code == 200
            ), f"channel list on merged worker session failed: {chans.text}"
            assert chans.json(), (
                "connect created no listable channel on the merged worker "
                f"session: {chans.json()}"
            )


# ---------------------------------------------------------------------------
# Resume — must NOT adopt a session into the host engine in lab-host
# mode (goal #3: host runs no agents). A host-targeted resume has to be
# rejected; resume belongs on a worker.
# ---------------------------------------------------------------------------


async def test_resume_into_host_rejected_in_lab_host_mode(tmp_path, monkeypatch):
    """``POST .../resume`` with the default host target is rejected.

    The resume request defaults to ``on_node="_host"``. In lab-host
    mode that would adopt the saved session into the host's own engine
    — running an agent on the host. It must be rejected with a clear
    error before any path resolution, so the operator is steered to
    resume on a worker instead.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)

    async with RealLabHost(tmp_path) as host:
        # Default body → on_node="_host" → must be rejected in lab-host
        # mode, BEFORE the 404 path-resolution (it's a 400, not a 404).
        resp = await asyncio.wait_for(
            host.http.post("/api/sessions/any-session/resume"),
            timeout=OP_TIMEOUT,
        )
        assert resp.status_code == 400, (
            f"host-targeted resume not rejected in lab-host mode: "
            f"{resp.status_code} {resp.text}"
        )
        assert (
            "host" in resp.text.lower()
        ), f"resume rejection message unclear: {resp.text}"

        # Explicit on_node="_host" is equally rejected.
        resp_explicit = await asyncio.wait_for(
            host.http.post(
                "/api/sessions/any-session/resume", json={"on_node": "_host"}
            ),
            timeout=OP_TIMEOUT,
        )
        assert (
            resp_explicit.status_code == 400
        ), "explicit on_node='_host' resume not rejected in lab-host mode"


# ---------------------------------------------------------------------------
# Persistence read surface for a worker session — history + memory
# search. A worker creature's events are mirrored to the host; the
# session-viewer / memory endpoints must read that mirror, not 404.
# ---------------------------------------------------------------------------


async def test_worker_session_history_and_memory_search(tmp_path, monkeypatch):
    """A worker session's recorded turns are readable + searchable.

    Chat a distinctive turn on a worker creature, then read it back
    through the persistence surface: ``GET .../history`` shows the turn,
    and ``.../memory/search`` answers cleanly (the worker's events are
    mirrored to the host — the read endpoints must not 404).
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(
        monkeypatch, script=[ScriptEntry("xenophilia answer", match="xenophilia")]
    )
    cfg_dir = _write_creature_config(tmp_path, "archivist", "You archive.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]

            # Drive a distinctive turn so the worker store records it.
            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "xenophilia please"), timeout=OP_TIMEOUT
                )
            assert "xenophilia answer" in reply

            # --- history shows the turn -------------------------------
            base = f"/api/sessions/{session_id}/creatures/{creature_id}"
            hist = await asyncio.wait_for(
                host.http.get(f"{base}/history"), timeout=OP_TIMEOUT
            )
            assert hist.status_code == 200, f"history read failed: {hist.text}"
            assert "xenophilia" in json.dumps(hist.json()), (
                f"the worker creature's turn is not in its history: " f"{hist.json()}"
            )

            # --- memory search answers cleanly for a worker session ---
            search = await asyncio.wait_for(
                host.http.get(
                    f"/api/sessions/{session_id}/memory/search",
                    params={"q": "xenophilia", "mode": "fts"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert search.status_code == 200, (
                f"memory search on a worker session failed: "
                f"{search.status_code} {search.text}"
            )


# ---------------------------------------------------------------------------
# Working directory get/set on a worker creature — a per-creature
# mutation that must take effect on the worker's real Agent.
# ---------------------------------------------------------------------------


async def test_working_dir_get_set_on_worker(tmp_path, monkeypatch):
    """``GET``/``PUT .../working-dir`` round-trips for a worker creature.

    Reading the working dir must return the worker creature's real
    pwd; setting it must take effect on the worker (a host-side
    phantom would read back the old value).
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_dir = _write_creature_config(tmp_path, "wanderer", "You wander.")
    new_dir = tmp_path / "new-workdir"
    new_dir.mkdir()

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]
            base = f"/api/sessions/{session_id}/creatures/{creature_id}"
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "creature on worker engine",
            )

            # Read — returns the worker creature's real pwd.
            got = await asyncio.wait_for(
                host.http.get(f"{base}/working-dir"), timeout=OP_TIMEOUT
            )
            assert got.status_code == 200, f"working-dir read failed: {got.text}"
            assert got.json().get("pwd"), f"working-dir read empty: {got.json()}"

            # Set — takes effect on the worker creature's real Agent.
            put = await asyncio.wait_for(
                host.http.put(f"{base}/working-dir", json={"path": str(new_dir)}),
                timeout=OP_TIMEOUT,
            )
            assert put.status_code == 200, (
                f"working-dir set failed on worker creature: "
                f"{put.status_code} {put.text}"
            )
            # Read-back reflects the change on the worker.
            again = await asyncio.wait_for(
                host.http.get(f"{base}/working-dir"), timeout=OP_TIMEOUT
            )
            assert Path(again.json()["pwd"]) == new_dir, (
                f"working-dir set did not take effect on the worker "
                f"creature: {again.json()} != {new_dir}"
            )

            # Native-tool-options read surface routes to the worker too.
            nto = await asyncio.wait_for(
                host.http.get(f"{base}/native-tool-options"),
                timeout=OP_TIMEOUT,
            )
            assert nto.status_code == 200, (
                f"native-tool-options read failed on worker creature: "
                f"{nto.status_code} {nto.text}"
            )


# ---------------------------------------------------------------------------
# Sub-agent dispatch ON A WORKER — the VERTICAL composition level must
# work inside a worker-hosted creature exactly as it does standalone. The
# creature config declares a builtin ``explore`` sub-agent; a chat turn
# that dispatches to it must route the sub-agent's reply back into the
# controller's wrap-up turn — all on the worker, invisible to the host.
# ---------------------------------------------------------------------------


async def test_subagent_dispatch_on_worker(tmp_path, monkeypatch):
    """A worker creature dispatches to its sub-agent and routes the result back.

    The creature declares a builtin ``explore`` sub-agent. The scripted
    controller, on a "delegate" turn, emits an ``[/explore]...[explore/]``
    dispatch; the sub-agent (its own LLM, same shared script, content-
    matched) replies; the controller's wrap-up turn consumes that reply.
    The whole VERTICAL hierarchy runs on the worker — the standalone path
    is the behavior reference.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    # Three content-matched entries — the controller and the sub-agent
    # each get a fresh ScriptedLLM over this shared script, so position
    # indexing would collide; ``match`` keys each reply to its trigger.
    install_scripted_llm(
        monkeypatch,
        script=[
            ScriptEntry("[/explore]survey the codebase[explore/]", match="delegate"),
            ScriptEntry("explored: all clear", match="survey the codebase"),
            ScriptEntry("delegation complete: explored", match="explored: all clear"),
        ],
    )
    cfg_dir = _write_creature_config(
        tmp_path, "delegator", "You delegate.", subagents=["explore"]
    )

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "creature on worker engine",
            )

            # The worker creature actually built the sub-agent — the
            # VERTICAL composition exists on the worker, not the host.
            wc_agent = worker.engine.get_creature(creature_id).agent
            sa_manager = getattr(wc_agent, "subagent_manager", None)
            assert sa_manager is not None, (
                "worker creature has no subagent_manager — sub-agent "
                "config was not built on the worker"
            )

            # A dispatch turn over the chat WS: the controller delegates,
            # the sub-agent answers, the controller wraps up. The final
            # streamed reply carries the wrap-up text. Sub-agent build +
            # two controller round-trips take longer than a plain turn,
            # so the drain gets a wider ceiling than OP_TIMEOUT.
            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "delegate the survey"),
                    timeout=50.0,
                )
            assert "delegation complete: explored" in reply, (
                f"sub-agent dispatch did not route back through the worker "
                f"creature's controller — reply was {reply!r}"
            )


# ---------------------------------------------------------------------------
# Identity sync host → worker — "ANY setting stuff can be in host then
# sync to worker". The worker has no identity files of its own; its
# IdentityCache fetches LLM profiles / API keys from the host's
# StudioIdentityAdapter over the real ``studio.identity`` wire.
# ---------------------------------------------------------------------------


async def test_identity_sync_host_to_worker(tmp_path, monkeypatch):
    """A worker's IdentityCache resolves a profile that lives on the host.

    The host is the source of truth for identity. A profile configured
    on the host must be fetchable by a connected worker through the real
    ``studio.identity`` APP namespace — proving the host→worker settings
    sync path is wired, not just stubbed.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)

    # The host's StudioIdentityAdapter reads profiles via
    # ``list_profiles_payload`` — seam it to a known host-side profile
    # so the test doesn't depend on the operator's real identity files.
    synced_profile = {
        "name": "synced-profile",
        "model": "gpt-4-test",
        "provider": "openai",
    }
    monkeypatch.setattr(
        "kohakuterrarium.laboratory.adapters.studio_identity.list_profiles_payload",
        lambda: [synced_profile],
    )

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            assert worker.identity_cache is not None

            # The worker fetches the host-defined profile over the wire.
            got = await asyncio.wait_for(
                worker.identity_cache.get_profile("synced-profile"),
                timeout=OP_TIMEOUT,
            )
            assert got == synced_profile, (
                f"worker IdentityCache did not sync the host profile: "
                f"got {got!r}, expected {synced_profile!r}"
            )

            # A profile the host doesn't have surfaces as a clean miss,
            # not a hang and not a wrong record.
            with pytest.raises(Exception):
                await asyncio.wait_for(
                    worker.identity_cache.get_profile("does-not-exist"),
                    timeout=OP_TIMEOUT,
                )


# ---------------------------------------------------------------------------
# Resume happy-path ON A WORKER — the operator stops a worker creature
# (its turns persist + mirror to the host), then resumes that session
# onto a worker. The host pushes the .kohakutr to the worker and the
# worker's engine adopts it; the resumed creature must come back live.
# ---------------------------------------------------------------------------


async def test_resume_happy_path_on_worker(tmp_path, monkeypatch):
    """A stopped worker session resumes cleanly back onto a worker.

    1. spawn a creature on worker-1 and drive a distinctive turn — the
       turn persists to the worker store and mirrors to the host.
    2. stop the creature (the session file survives on the mirror).
    3. ``POST .../resume`` with ``on_node=worker-1`` — the host reads the
       mirrored ``.kohakutr``, pushes it to the worker, and the worker's
       engine adopts the session locally.
    4. the resumed creature is live ON THE WORKER again — only workers
       run agents, and resume is no exception.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(
        monkeypatch, script=[ScriptEntry("remembered turn", match="remember this")]
    )
    cfg_dir = _write_creature_config(tmp_path, "phoenix", "You rise again.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1"
        ) as worker:
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]

            # --- 1. drive a distinctive turn so the store records it ---
            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "remember this"), timeout=OP_TIMEOUT
                )
            assert "remembered turn" in reply

            # --- 2. stop the creature — the session file persists ------
            stop = await asyncio.wait_for(
                host.http.delete(f"/api/sessions/active/agents/{creature_id}"),
                timeout=OP_TIMEOUT,
            )
            assert stop.status_code in (200, 204), f"stop failed: {stop.text}"
            await RealLabHost._wait_for(
                lambda: not worker.engine.list_creatures(),
                "creature removed from worker after stop",
            )

            # --- 3. resume onto the worker -----------------------------
            resume = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/{session_id}/resume",
                    json={"on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert (
                resume.status_code == 200
            ), f"resume onto worker failed: {resume.status_code} {resume.text}"
            resumed = resume.json()
            assert (
                resumed.get("on_node") == "worker-1"
            ), f"resume response not tagged to the worker: {resumed}"

            # --- 4. the resumed creature is live on the worker ---------
            await RealLabHost._wait_for(
                lambda: bool(worker.engine.list_creatures()),
                "resumed creature back on worker engine",
            )


# ===========================================================================
# REGRESSION REPRODUCTION — reported real-deployment failures (lab-host):
#   * GET /api/attach/policies/<id>  →  404 right after creating a creature
#   * the chat WebSocket accepts then immediately closes ("can't attach")
#   * the frontend banner stuck on "worker-1 is offline"
# Per the project methodology these are reproduced as FAILING e2e tests
# first (the symptom, end to end), narrowed to integration + unit after,
# and only then fixed.
# ===========================================================================


async def test_attach_policies_endpoint_for_worker_creature(tmp_path, monkeypatch):
    """``GET /api/attach/policies/<id>`` must resolve a worker target.

    Reproduces the reported ``GET /api/attach/policies/graph_... 404
    Not Found`` seen right after creating a remote creature. The
    frontend hits ``/api/attach/policies/<id>`` where ``<id>`` is the
    session/graph id (the Inspector Overview's target key), but the
    route only resolves ``<id>`` as a *creature* id — against a
    host engine that, in lab-host mode, has neither the creature nor
    the graph. It must instead resolve worker targets through the
    service so the hint isn't a permanent 404.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_dir = _write_creature_config(tmp_path, "policyholder", "You hold policy.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            session_id = resp.json()["session_id"]
            creature_id = resp.json()["creatures"][0]["creature_id"]

            # The creature-policy hint, keyed by creature_id — resolves.
            cp = await asyncio.wait_for(
                host.http.get(f"/api/attach/policies/{creature_id}"),
                timeout=OP_TIMEOUT,
            )
            assert cp.status_code == 200, (
                f"GET /api/attach/policies/{creature_id} returned "
                f"{cp.status_code} for a live worker creature: {cp.text}"
            )

            # The EXACT failing call from the report: the frontend hits
            # ``/api/attach/policies/<session_id>`` (a graph id), and it
            # 404s for a live worker session.
            sp = await asyncio.wait_for(
                host.http.get(f"/api/attach/policies/{session_id}"),
                timeout=OP_TIMEOUT,
            )
            assert sp.status_code == 200, (
                f"GET /api/attach/policies/{session_id} returned "
                f"{sp.status_code} for a live worker session — this is "
                f"the reported 'attach/policies/graph_... 404': {sp.text}"
            )


async def test_nodes_endpoint_reports_connected_worker_online(tmp_path, monkeypatch):
    """``GET /api/nodes`` must report a connected worker as online.

    Reproduces the frontend's persistent "worker-1 is offline" banner:
    the worker IS connected (the host's membership confirms it), so the
    node listing must not report it offline / unreachable.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.get("/api/nodes"), timeout=OP_TIMEOUT
            )
            assert resp.status_code == 200, f"/api/nodes failed: {resp.text}"
            nodes = {n["node_id"]: n for n in resp.json().get("nodes", [])}
            assert (
                "worker-1" in nodes
            ), f"/api/nodes does not list the connected worker: {resp.json()}"
            assert nodes["worker-1"]["status"] == "online", (
                f"/api/nodes reports a connected worker as "
                f"{nodes['worker-1']['status']!r}, not 'online' — this is "
                f"the frontend's 'worker-1 is offline' banner: {nodes}"
            )


async def test_frontend_create_then_chat_attach_sequence_on_worker(
    tmp_path, monkeypatch
):
    """The exact frontend create→attach sequence must work on a worker.

    Reproduces "I can't even create a creature and attach to it in
    multi node": the chat WebSocket accepts then immediately closes.
    Drives the literal frontend order — nodes → history → active →
    chat WS — and attaches BOTH by the engine ``creature_id`` AND by
    the creature's display ``name`` (the frontend's chat tab keys off
    the friendly name, e.g. ``/creatures/quiet-meadow/chat``).
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["attach reply ok"])
    cfg_dir = _write_creature_config(tmp_path, "attachme", "You attach.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            resp = await asyncio.wait_for(
                host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_dir), "on_node": "worker-1"},
                ),
                timeout=OP_TIMEOUT,
            )
            assert resp.status_code == 200, f"spawn failed: {resp.text}"
            entry = resp.json()["creatures"][0]
            session_id = resp.json()["session_id"]
            creature_id = entry["creature_id"]
            creature_name = entry["name"]

            # --- the frontend's literal pre-attach polling order -------
            for path, what in (
                ("/api/nodes", "nodes"),
                (
                    f"/api/sessions/{session_id}/creatures/{creature_id}/history",
                    "history",
                ),
                ("/api/sessions/active", "active"),
            ):
                r = await asyncio.wait_for(host.http.get(path), timeout=OP_TIMEOUT)
                assert r.status_code == 200, (
                    f"frontend pre-attach {what} ({path}) failed: "
                    f"{r.status_code} {r.text}"
                )

            # --- attach + chat BY ID — must stream, not close empty ----
            by_id_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(by_id_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "ping"), timeout=OP_TIMEOUT
                )
            assert "attach reply ok" in reply, (
                f"chat attach by creature_id streamed {reply!r} — the "
                "WebSocket accepted then closed without a reply"
            )

            # --- attach + chat BY NAME — the frontend's tab key --------
            install_scripted_llm(monkeypatch, script=["attach reply ok"])
            by_name_path = f"/ws/sessions/{session_id}/creatures/{creature_name}/chat"
            async with host.api_ws(by_name_path) as ws:
                reply_by_name = await asyncio.wait_for(
                    _drain_chat_ws(ws, "ping"), timeout=OP_TIMEOUT
                )
            assert "attach reply ok" in reply_by_name, (
                f"chat attach by creature NAME {creature_name!r} streamed "
                f"{reply_by_name!r} — the frontend keys its chat tab off "
                "the display name and the WebSocket closes empty"
            )


# ===========================================================================
# REGRESSION REPRODUCTION — reported multi-node routing failures:
#   * cross-node connect (creature on w1 ↔ creature on w2) → merge 404
#   * two workers each with a creature → ``list_creatures failed on
#     worker-1`` and w1 then reported "offline" in the node list
#   * a worker creature's events failing to reach the host's
#     session-sync mirror right after creation
# Reproduced as failing e2e tests first, then narrowed + fixed.
# ===========================================================================


async def _spawn_on(host, cfg_dir, node):
    """POST a creature spawn on ``node``; return (session_id, creature_id)."""
    r = await asyncio.wait_for(
        host.http.post(
            "/api/sessions/active/creature",
            json={"config_path": str(cfg_dir), "on_node": node},
        ),
        timeout=OP_TIMEOUT,
    )
    assert r.status_code == 200, f"spawn on {node} failed: {r.text}"
    body = r.json()
    return body["session_id"], body["creatures"][0]["creature_id"]


async def test_cross_node_connect_does_not_404(tmp_path, monkeypatch):
    """Connecting a creature on worker-1 to one on worker-2 must not 404.

    Reproduces ``POST /api/sessions/topology/{a}/merge/{b} 404`` — the
    frontend graph editor's cross-node wire. The ``merge_sessions``
    route resolves both ids against the *host* engine, which in
    lab-host mode is the agent-free coordination engine with NO graphs
    — so every cross-node merge / connect 404s even though both
    sessions are live on workers.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_a = _write_creature_config(tmp_path, "wireleft", "You wire left.")
    cfg_b = _write_creature_config(tmp_path, "wireright", "You wire right.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"),
            RealLabWorker("worker-2", host.lab_ws_url, tmp_path / "worker-2"),
        ):
            await RealLabHost._wait_for(
                lambda: {"worker-1", "worker-2"}
                <= set(host.host_engine.alive_clients()),
                "both workers join",
            )
            sid_a, cid_a = await _spawn_on(host, cfg_a, "worker-1")
            sid_b, cid_b = await _spawn_on(host, cfg_b, "worker-2")

            # The graph editor's cross-node wire posts a merge of the
            # two sessions.  It must resolve the worker-hosted graphs,
            # not dead-end on the empty host coordination engine.
            merge = await asyncio.wait_for(
                host.http.post(f"/api/sessions/topology/{sid_a}/merge/{sid_b}"),
                timeout=OP_TIMEOUT,
            )
            assert merge.status_code != 404, (
                f"cross-node merge {sid_a} + {sid_b} returned 404 — the "
                f"route resolves session ids against the agent-free host "
                f"coordination engine: {merge.text}"
            )

            # The direct ``connect`` route must likewise reach the
            # worker-hosted creatures, not 404.
            conn = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/topology/{sid_a}/connect",
                    json={"sender": cid_a, "receiver": cid_b},
                ),
                timeout=OP_TIMEOUT,
            )
            assert conn.status_code != 404, (
                f"cross-node connect of {cid_a} (w1) → {cid_b} (w2) "
                f"returned 404: {conn.text}"
            )


async def test_two_workers_both_stay_online_under_concurrent_list(
    tmp_path, monkeypatch
):
    """Two workers each with a creature must both report online.

    Reproduces ``list_creatures failed on worker-1`` → worker-1 shown
    "offline" in the frontend. After spawning a creature on each
    worker, concurrent ``/api/nodes`` + ``/api/sessions/active`` polls
    (the frontend's steady-state loop) must keep BOTH workers
    ``status == "online"`` and BOTH creatures listed — a fan-out that
    drops a worker on a transient slow read is the "conflict on some
    method" the report describes.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch)
    cfg_a = _write_creature_config(tmp_path, "onlinea", "You are A.")
    cfg_b = _write_creature_config(tmp_path, "onlineb", "You are B.")

    async with RealLabHost(tmp_path) as host:
        async with (
            RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"),
            RealLabWorker("worker-2", host.lab_ws_url, tmp_path / "worker-2"),
        ):
            await RealLabHost._wait_for(
                lambda: {"worker-1", "worker-2"}
                <= set(host.host_engine.alive_clients()),
                "both workers join",
            )
            sid_a, _ = await _spawn_on(host, cfg_a, "worker-1")
            sid_b, _ = await _spawn_on(host, cfg_b, "worker-2")

            # Hammer the frontend's steady-state poll surface
            # concurrently — the fan-out must stay coherent.
            async def _poll_nodes():
                r = await host.http.get("/api/nodes")
                assert r.status_code == 200, f"/api/nodes failed: {r.text}"
                return {n["node_id"]: n for n in r.json().get("nodes", [])}

            async def _poll_active():
                r = await host.http.get("/api/sessions/active")
                assert r.status_code == 200
                return {s["session_id"] for s in r.json()}

            for _ in range(5):
                results = await asyncio.wait_for(
                    asyncio.gather(
                        _poll_nodes(),
                        _poll_active(),
                        _poll_nodes(),
                        _poll_active(),
                    ),
                    timeout=OP_TIMEOUT,
                )
                nodes_a, active_a, nodes_b, active_b = results
                for nodes in (nodes_a, nodes_b):
                    for wid in ("worker-1", "worker-2"):
                        assert wid in nodes, f"{wid} missing from /api/nodes"
                        assert nodes[wid]["status"] == "online", (
                            f"{wid} reported {nodes[wid]['status']!r} under "
                            f"concurrent polling — the report's "
                            f"'list_creatures failed → offline' bug"
                        )
                for active in (active_a, active_b):
                    assert {sid_a, sid_b} <= active, (
                        f"a worker session vanished from /api/sessions/active "
                        f"under concurrent polling: {active}"
                    )


async def test_worker_creature_events_reach_host_mirror(tmp_path, monkeypatch):
    """A worker creature's turn events land in the host's session mirror.

    Reproduces ``session-sync mirror: append failed`` — a worker
    creature streams a turn, its events are tee'd to the host's
    :class:`SessionMirrorWriter`, and the host's mirror ``.kohakutr``
    must actually contain them. An append failure leaves the mirror
    empty even though the chat streamed fine.
    """
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
    install_scripted_llm(monkeypatch, script=["mirrored turn body"])
    cfg_dir = _write_creature_config(tmp_path, "mirrorme", "You mirror.")

    async with RealLabHost(tmp_path) as host:
        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path / "worker-1"):
            await RealLabHost._wait_for(
                lambda: "worker-1" in set(host.host_engine.alive_clients()),
                "worker-1 join",
            )
            session_id, creature_id = await _spawn_on(host, cfg_dir, "worker-1")

            chat_path = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
            async with host.api_ws(chat_path) as ws:
                reply = await asyncio.wait_for(
                    _drain_chat_ws(ws, "mirror this turn"), timeout=OP_TIMEOUT
                )
            assert "mirrored turn body" in reply

            # The host's session mirror must have recorded the turn's
            # events — give the tee a beat to drain to the mirror.
            mirror = host.app.state.session_mirror

            def _mirror_has_events() -> bool:
                store = mirror._stores.get(session_id)
                if store is None:
                    return False
                for agent in store.load_meta().get("agents") or []:
                    if store.get_events(agent):
                        return True
                return False

            await RealLabHost._wait_for(
                _mirror_has_events,
                "worker creature events reach the host session mirror",
            )
