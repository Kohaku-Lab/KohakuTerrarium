"""Audit findings against ``terrarium/multi_node_service.py``.

Each test method targets ONE specific suspected bug found during a
static read of :class:`MultiNodeTerrariumService`, exercised through
the real lab-host + worker stack (``RealLabHost`` + ``RealLabWorker``)
and the real public HTTP API.  All assertions are behavior asserts —
each observes a concrete side effect (cluster fold result, channel
delivery, name resolver result) rather than a shape check.

These tests are written to FAIL on the current implementation;
each failure is a concrete bug-report receipt.  Fixes belong in a
follow-up — this file pins behavior only.
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


async def _spawn_cross_cluster(host, cfg_a, cfg_b):
    """alpha on w1, bravo on w2, ch1 with alpha→ch1→bravo cross-wired."""
    sa = (
        await host.http.post(
            "/api/sessions/active/creature",
            json={"config_path": str(cfg_a), "on_node": "w1"},
        )
    ).json()
    ga = sa["session_id"]
    a_id = sa["creatures"][0]["creature_id"]
    a_name = sa["creatures"][0]["name"]

    sb = (
        await host.http.post(
            "/api/sessions/active/creature",
            json={"config_path": str(cfg_b), "on_node": "w2"},
        )
    ).json()
    gb = sb["session_id"]
    b_id = sb["creatures"][0]["creature_id"]
    b_name = sb["creatures"][0]["name"]

    await host.http.post(f"/api/sessions/topology/{ga}/channels", json={"name": "ch1"})
    await host.http.post(
        f"/api/sessions/topology/{ga}/creatures/{a_id}/wire",
        json={"channel": "ch1", "direction": "send"},
    )
    await host.http.post(
        f"/api/sessions/topology/{gb}/creatures/{b_id}/wire",
        json={"channel": "ch1", "direction": "listen"},
    )
    return ga, gb, a_id, a_name, b_id, b_name


def _get_service(host):
    """Pull the live :class:`MultiNodeTerrariumService` from API deps.

    Uses ``get_service_legacy`` because the e2e helpers drive the
    cluster from outside an HTTP request context — the HTTP-scoped
    ``get_service`` dep needs a ``Request``/``HTTPConnection`` to
    resolve per-user routing.  The legacy variant returns the
    process-wide singleton, which is what the multi-node service
    is regardless.
    """
    from kohakuterrarium.api import deps as _deps

    return _deps.get_service_legacy()


class TestMultiNodeAuditFindings:
    """One method per suspected bug — each fails on current impl."""

    async def test_cross_node_disconnect_must_clear_cluster_link(
        self, tmp_path, monkeypatch
    ):
        """``_cluster_links`` is added on ``connect`` (line 504) and on
        ``_ensure_channel_replicated`` (line 990) but is NEVER cleaned —
        not on ``disconnect``, not on ``remove_channel``, not on
        ``remove_creature``, not on ``drop_remote``.

        User-facing symptom: after a user wires a cross-node bridge and
        then disconnects it, ``runtime_graph_snapshot`` STILL folds the
        two engine graphs into a single cluster (``is_cluster=True``,
        ``members`` carries both sides) — the UI continues to render
        ONE merged graph forever.  The cluster is also "sticky": after
        disconnect, both graphs are still ad-hoc joined, so even a
        single-node user op against either graph_id may surface
        creatures from the other side.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[ScriptEntry(response="ack"), ScriptEntry(response="ack2")],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as _w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as _w2,
            ):
                await asyncio.sleep(0.3)
                ga, gb, a_id, _a_name, b_id, _b_name = await _spawn_cross_cluster(
                    host, cfg_a, cfg_b
                )

                # Sanity: after cross-wire, snapshot folds into a cluster.
                snap_before = (await host.http.get("/api/runtime/graph")).json()
                fused_before = [
                    g for g in snap_before.get("graphs", []) if g.get("is_cluster")
                ]
                assert fused_before, (
                    "precondition: cross-wire should produce a cluster fold; "
                    f"got graphs={[g.get('graph_id') for g in snap_before.get('graphs', [])]}"
                )

                # Disconnect the cross-node bridge.
                disc = await host.http.post(
                    f"/api/sessions/topology/{ga}/disconnect",
                    json={"sender": a_id, "receiver": b_id, "channel": "ch1"},
                )
                assert (
                    disc.status_code == 200
                ), f"disconnect call must succeed: {disc.status_code} {disc.text}"

                # BEHAVIOR ASSERTION: after disconnect, the two engine
                # graphs MUST surface as separate graphs again — no
                # cluster fold, ``is_cluster`` absent or False on both.
                snap_after = (await host.http.get("/api/runtime/graph")).json()
                still_fused = [
                    g for g in snap_after.get("graphs", []) if g.get("is_cluster")
                ]
                # Also assert the cross-node bookkeeping was cleaned.
                service = _get_service(host)
                cluster_links_after = set(service._cluster_links)

                assert not still_fused and not cluster_links_after, (
                    "BUG: cross-node disconnect leaves _cluster_links and "
                    "the cluster fold intact.  After disconnect, the two "
                    "engine graphs should render as separate graphs.\n"
                    f"still_fused graphs: {[g.get('graph_id') for g in still_fused]}\n"
                    f"cluster_links: {cluster_links_after}\n"
                    f"file:line — multi_node_service.py:518-573 (disconnect) "
                    f"never removes from self._cluster_links."
                )

    async def test_remove_creature_must_purge_name_cache(self, tmp_path, monkeypatch):
        """``remove_creature`` (line 380) clears ``_home`` but NOT
        ``_creature_name_cache``.  The output-wire target resolver
        installed by ``api/app.py`` reads that cache *synchronously*
        (it can't fan-out async) to translate a target name into a
        ``(node_id, creature_id)``.

        User-facing symptom: after a creature is removed, any source
        creature still configured to output-wire to its NAME continues
        to resolve to the dead ``(node_id, creature_id)`` for as long
        as nothing has called ``list_creatures`` (which would re-key
        the cache from the workers' fresh state).  Cross-node emits
        target a dead address; the host then forwards to a worker that
        no longer hosts the creature.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(monkeypatch, script=[ScriptEntry(response="ack")])
        cfg_a = _write_cfg(tmp_path, "alpha")

        async with RealLabHost(tmp_path) as host:
            async with RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as _w1:
                await asyncio.sleep(0.3)
                sa = (
                    await host.http.post(
                        "/api/sessions/active/creature",
                        json={"config_path": str(cfg_a), "on_node": "w1"},
                    )
                ).json()
                _ga = sa["session_id"]
                a_id = sa["creatures"][0]["creature_id"]
                a_name = sa["creatures"][0]["name"]

                # Warm the cache via list_creatures fan-out.
                service = _get_service(host)
                await service.list_creatures()
                assert (
                    a_name in service._creature_name_cache
                ), "precondition: name cache must carry alpha by name"
                assert (
                    a_id in service._creature_name_cache
                ), "precondition: name cache must carry alpha by id"

                # Remove the creature directly through the service —
                # this is the per-creature lifecycle path the API uses.
                await service.remove_creature(a_id)

                # BEHAVIOR ASSERTION: name cache MUST no longer report
                # the dead creature, otherwise the output-wire resolver
                # serves stale routes.
                cache_after = dict(service._creature_name_cache)
                assert a_name not in cache_after and a_id not in cache_after, (
                    "BUG: remove_creature does not purge _creature_name_cache; "
                    f"resolver still sees {a_name!r}/{a_id!r}.\n"
                    f"cache_after={cache_after}\n"
                    f"file:line — multi_node_service.py:380-384 "
                    f"(remove_creature) only pops self._home, not the "
                    f"name cache used by _make_output_wire_target_resolver."
                )

                # And the public resolver entry — built from the same
                # cache — must miss for the dead name.
                from kohakuterrarium.api.app import (
                    _make_output_wire_target_resolver,
                )

                resolve = _make_output_wire_target_resolver(service)
                assert resolve(a_name) is None and resolve(a_id) is None, (
                    "BUG: output-wire resolver returns a hit for a "
                    f"removed creature; resolve({a_name!r})="
                    f"{resolve(a_name)!r} resolve({a_id!r})="
                    f"{resolve(a_id)!r}"
                )

    async def test_drop_remote_must_purge_name_cache_and_cluster_links(
        self, tmp_path, monkeypatch
    ):
        """``drop_remote`` (line 195) clears ``_home`` for the
        departing node's creatures, but leaves ``_creature_name_cache``
        and ``_cluster_links`` alone.

        User-facing symptom: when a worker disconnects (network
        partition / kt lab-client exit), the sync output-wire resolver
        keeps returning ``(dead_node_id, creature_id)`` pairs that
        ``service_for(dead_node_id)`` will then raise ``KeyError`` for —
        every output-wire emit targeted at the dead worker fails
        loudly, instead of cleanly being a "no target" miss.  And
        runtime_graph_snapshot keeps trying to fold around the dead
        member.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(
            monkeypatch,
            script=[ScriptEntry(response="ack"), ScriptEntry(response="ack2")],
        )
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            w1 = RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1")
            w2 = RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2")
            await w1.__aenter__()
            await w2.__aenter__()
            try:
                await asyncio.sleep(0.3)
                ga, gb, a_id, a_name, b_id, b_name = await _spawn_cross_cluster(
                    host, cfg_a, cfg_b
                )

                service = _get_service(host)
                await service.list_creatures()
                assert (
                    b_name in service._creature_name_cache
                ), "precondition: bravo name cached"
                links_before = set(service._cluster_links)
                assert links_before, "precondition: cluster link recorded"

                # w2 disconnects (clean shutdown — host gets LEFT).
                await w2.__aexit__(None, None, None)
                w2 = None
                # Give the host's _watch_membership a moment to call
                # drop_remote on the multi-node service.
                deadline = asyncio.get_event_loop().time() + OP_TIMEOUT
                while asyncio.get_event_loop().time() < deadline:
                    if "w2" not in service.connected_nodes():
                        break
                    await asyncio.sleep(0.05)
                assert "w2" not in service.connected_nodes(), (
                    f"precondition: w2 must be dropped from connected_nodes; "
                    f"got {service.connected_nodes()}"
                )

                # BEHAVIOR ASSERTION: drop_remote should have purged
                # bravo from the name cache and removed the cluster
                # link pointing at w2.
                cache_after = dict(service._creature_name_cache)
                links_after = set(service._cluster_links)
                bravo_in_cache = (
                    b_name in cache_after
                    or b_id in cache_after
                    or any(v[0] == "w2" for v in cache_after.values())
                )
                links_pointing_at_w2 = {
                    link
                    for link in links_after
                    if any(endpoint[0] == "w2" for endpoint in link)
                }
                assert not bravo_in_cache and not links_pointing_at_w2, (
                    "BUG: drop_remote leaves stale name-cache entries "
                    "and cluster_links pointing at the departed worker.\n"
                    f"cache_after={cache_after}\n"
                    f"links pointing at w2 ={links_pointing_at_w2}\n"
                    f"file:line — multi_node_service.py:195-200 "
                    f"(drop_remote) only purges self._home; "
                    f"_creature_name_cache and _cluster_links survive."
                )

                # And the resolver returns a dead address — the most
                # user-visible consequence:
                from kohakuterrarium.api.app import (
                    _make_output_wire_target_resolver,
                )

                resolve = _make_output_wire_target_resolver(service)
                entry = resolve(b_name)
                assert entry is None or entry[0] != "w2", (
                    "BUG: output-wire resolver still routes to the dead "
                    f"node; resolve({b_name!r})={entry!r}"
                )
            finally:
                if w2 is not None:
                    await w2.__aexit__(None, None, None)
                await w1.__aexit__(None, None, None)
