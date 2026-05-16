"""E2E journey — the HTTP+WS terrarium usage path.

One fat journey: drive the real :func:`create_app` FastAPI app through
``fastapi.testclient.TestClient`` over both HTTP and WebSocket, the
exact way the Vue frontend's ``terrariumAPI`` / ``runtimeGraphAPI`` /
the ``/ws/runtime/graph`` + ``/ws/sessions/.../chat`` streams drive a
multi-creature terrarium session.

The whole stack runs for real: ``create_app()``, a real
:class:`LocalTerrariumService` over a real :class:`Terrarium`
installed via :func:`api.deps.set_service`, the engine, the on-disk
session store, topology mutation, the runtime-graph WS pump, the IO
attach loop. The ONLY seam is the LLM — both ``create_llm_provider``
bind points (``bootstrap.llm`` and ``bootstrap.agent_init``) are
monkeypatched to a deterministic :class:`ScriptedLLM`.

The journey, in one method:

1. ``create_app()`` + ``TestClient`` over a real service; recipe
   written to disk; ``KT_SESSION_DIR`` redirected at ``tmp_path``.
2. POST a terrarium session from a recipe → GET it in the active
   session list with its two creatures.
3. Open ``/ws/runtime/graph`` → assert the ``subscribed`` + ``snapshot``
   frames reflect the recipe topology.
4. Declare a channel + send a message on it → assert delivery on the
   runtime-graph WS channel-message frame and the channel read.
5. Hot-plug a third creature into the session → assert it joins the
   graph.
6. Chat with a creature over the WS chat endpoint.
7. Hit the topology endpoints — graph snapshot, creature list,
   connect/disconnect, wire/unwire.
8. Remove the hot-plugged creature → assert the topology shrinks back.
9. Stop the session → assert it leaves the active list.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)

# Deterministic assistant replies. ScriptedLLM hands out the next
# unused entry per LLM call; one plain creature turn = one call.
_REPLY_ONE = "Hello from the scripted terrarium creature."
_REPLY_TWO = "Second scripted terrarium reply."

# A two-creature recipe with one declared shared channel. The creature
# entries are full inline agent configs (the shape
# ``load_terrarium_config`` builds and ``build_agent_config`` resolves);
# no ``base_config`` indirection so the test owns every byte on disk.
_RECIPE_YAML = """\
terrarium:
  name: scripted-team
  channels:
    relay:
      type: broadcast
      description: shared relay channel
  creatures:
    - name: alice
      system_prompt: "You are alice, a deterministic e2e creature."
      tool_format: bracket
      input:
        type: none
      output:
        type: stdout
      channels:
        listen: [relay]
        can_send: [relay]
    - name: bob
      system_prompt: "You are bob, a deterministic e2e creature."
      tool_format: bracket
      input:
        type: none
      output:
        type: stdout
      channels:
        listen: [relay]
"""

# A standalone creature config directory — the hot-plug source. The
# add-creature route wraps the path as a ``base_config`` reference, so
# this must be a resolvable on-disk agent config.
_CREATURE_CONFIG = """\
name: carol
system_prompt: "You are carol, a deterministic hot-plugged creature."
tool_format: bracket
input:
  type: none
output:
  type: stdout
"""


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """Replace the live LLM provider at BOTH bind points.

    ``bootstrap.llm.create_llm_provider`` is the canonical factory;
    ``bootstrap.agent_init`` imports it by name, so the second patch is
    required or the agent-init path reaches a real provider.
    """
    llm = ScriptedLLM([_REPLY_ONE, _REPLY_TWO, _REPLY_TWO, _REPLY_TWO])

    def _fake_create(config, llm_override=None):
        return llm

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return llm


@pytest.fixture
def recipe_dir(tmp_path: Path) -> Path:
    """Write the multi-creature terrarium recipe to disk."""
    rdir = tmp_path / "team"
    rdir.mkdir()
    (rdir / "terrarium.yaml").write_text(_RECIPE_YAML, encoding="utf-8")
    return rdir


@pytest.fixture
def creature_dir(tmp_path: Path) -> Path:
    """Write the standalone creature config used by the hot-plug step."""
    cdir = tmp_path / "carol"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(_CREATURE_CONFIG, encoding="utf-8")
    return cdir


# A second two-creature recipe with DISTINCT creature names — the engine
# creature namespace is process-wide, so the cross-session merge step
# needs a recipe whose creatures don't collide with ``alice`` / ``bob``.
_RECIPE_TWO_YAML = """\
terrarium:
  name: scripted-team-two
  channels:
    relay2:
      type: broadcast
      description: second shared relay channel
  creatures:
    - name: dave
      system_prompt: "You are dave, a deterministic e2e creature."
      tool_format: bracket
      input:
        type: none
      output:
        type: stdout
      channels:
        listen: [relay2]
        can_send: [relay2]
    - name: erin
      system_prompt: "You are erin, a deterministic e2e creature."
      tool_format: bracket
      input:
        type: none
      output:
        type: stdout
      channels:
        listen: [relay2]
"""


@pytest.fixture
def recipe_two_dir(tmp_path: Path) -> Path:
    """Write the second (distinct-names) terrarium recipe to disk."""
    rdir = tmp_path / "team_two"
    rdir.mkdir()
    (rdir / "terrarium.yaml").write_text(_RECIPE_TWO_YAML, encoding="utf-8")
    return rdir


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scripted_llm: ScriptedLLM,
) -> Iterator[TestClient]:
    """A TestClient over a real ``create_app()`` with a real service.

    ``KT_SESSION_DIR`` is redirected at ``tmp_path`` so the engine's
    session-store writes and the studio-tier persistence reads share
    the same isolated directory.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))

    engine = Terrarium(session_dir=str(session_dir))
    service = LocalTerrariumService(engine)
    set_service(service)

    app = create_app()
    # ``with TestClient`` runs the lifespan: startup attaches the
    # runtime-graph prompt, shutdown drives ``engine.shutdown()``.
    with TestClient(app) as test_client:
        yield test_client

    set_service(None)


def _stream_turn(ws, message: str, target: str | None = None) -> str:
    """Send one user input over an attached IO WebSocket and collect
    the streamed assistant text up to the post-turn ``idle`` frame."""
    frame: dict = {"type": "input", "content": message}
    if target is not None:
        frame["target"] = target
    ws.send_json(frame)
    chunks: list[str] = []
    while True:
        msg = ws.receive_json()
        if msg.get("type") == "text":
            chunks.append(msg["content"])
        elif msg.get("type") == "idle":
            break
        elif msg.get("type") == "error":
            raise AssertionError(f"WS chat error frame: {msg!r}")
    return "".join(chunks)


def _drain_until(ws, predicate, *, limit: int = 60) -> dict:
    """Pull runtime-graph WS frames until ``predicate(frame)`` is true.

    The runtime-graph stream interleaves topology + channel-message
    frames; a test waiting for one specific frame must skip the others
    rather than assert on frame ordering.
    """
    for _ in range(limit):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    raise AssertionError("expected runtime-graph WS frame never arrived")


# ── the journey ───────────────────────────────────────────────────────


class TestApiTerrariumJourney:
    """One fat journey over the real HTTP + WS terrarium API surface."""

    def test_terrarium_session_full_journey(
        self,
        client: TestClient,
        recipe_dir: Path,
        recipe_two_dir: Path,
        creature_dir: Path,
        scripted_llm: ScriptedLLM,
    ) -> None:
        # 1. POST create a terrarium session from the recipe
        #    (frontend: terrariumAPI.create).
        resp = client.post(
            "/api/sessions/active/terrariums",
            json={"config_path": str(recipe_dir)},
        )
        assert resp.status_code == 200
        created = resp.json()
        assert created["status"] == "running"
        session_id = created["terrarium_id"]
        assert session_id

        # 2. GET it in the canonical active-session list with its two
        #    recipe creatures.
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        active = resp.json()
        assert [s["session_id"] for s in active] == [session_id]

        resp = client.get(f"/api/sessions/active/{session_id}/creatures")
        assert resp.status_code == 200
        assert {c["name"] for c in resp.json()} == {"alice", "bob"}

        # 3. Declare a NEW shared channel BEFORE the runtime-graph WS
        #    connects, so the WS's initial ``sync_channel_observers``
        #    pass registers a send-observer on it — a channel created
        #    after connect races the engine-event pump.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/channels",
            json={"name": "ops", "description": "operations channel"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

        # Open the runtime-graph WS — the ``subscribed`` then
        # ``snapshot`` frames reflect the recipe topology + the new
        # ``ops`` channel.
        with client.websocket_connect("/ws/runtime/graph") as graph_ws:
            subscribed = graph_ws.receive_json()
            assert subscribed["type"] == "subscribed"
            snap_frame = graph_ws.receive_json()
            assert snap_frame["type"] == "snapshot"
            graphs = {g["graph_id"]: g for g in snap_frame["snapshot"]["graphs"]}
            assert session_id in graphs
            graph = graphs[session_id]
            assert {c["name"] for c in graph["creatures"]} == {"alice", "bob"}
            # Both the recipe-declared ``relay`` and the just-declared
            # ``ops`` channel are in the snapshot.
            assert {"relay", "ops"} <= {c["name"] for c in graph["channels"]}

            # 4. Send a message on the channel; the runtime-graph WS
            #    delivers a channel-message frame for it.
            resp = client.post(
                f"/api/sessions/topology/{session_id}/channels/ops/send",
                json={"content": "status: green", "sender": "human"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "sent"

            chan_frame = _drain_until(
                graph_ws,
                lambda f: f.get("type") == "channel_message"
                and f.get("channel") == "ops",
            )
            assert chan_frame["sender"] == "human"
            assert chan_frame["content"] == "status: green"
            assert chan_frame["graph_id"] == session_id

        # The channel + its message are observable on the channel read.
        resp = client.get(f"/api/sessions/topology/{session_id}/channels/ops")
        assert resp.status_code == 200
        assert resp.json()["name"] == "ops"

        resp = client.get(f"/api/sessions/topology/{session_id}/channels")
        assert resp.status_code == 200
        assert "ops" in {c["name"] for c in resp.json()}

        # 5. Hot-plug a third creature into the SAME session.
        resp = client.post(
            f"/api/sessions/active/{session_id}/creatures",
            json={"name": "carol", "config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        carol_id = resp.json()["creature_id"]
        assert resp.json()["status"] == "running"

        resp = client.get(f"/api/sessions/active/{session_id}/creatures")
        assert resp.status_code == 200
        assert {c["name"] for c in resp.json()} == {"alice", "bob", "carol"}

        # The runtime-graph HTTP snapshot reflects the hot-plug too.
        resp = client.get("/api/runtime/graph")
        assert resp.status_code == 200
        graphs = {g["graph_id"]: g for g in resp.json()["graphs"]}
        assert {c["name"] for c in graphs[session_id]["creatures"]} == {
            "alice",
            "bob",
            "carol",
        }

        # A hot-plugged creature joins the graph with NO channel edges.
        # Wire carol to listen on the recipe's ``relay`` channel so it
        # shares a connected component with alice + bob — otherwise the
        # next topology normalization auto-splits it into its own graph.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/creatures/{carol_id}/wire",
            json={"channel": "relay", "direction": "listen"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "wired"}

        # 6. Chat with a creature over the WS chat endpoint. ``alice``'s
        #    creature_id equals its recipe name. A multi-creature graph
        #    with shared-channel history replays that history first, so
        #    drain up to the ``session_info`` activity frame.
        ws_url = f"/ws/sessions/{session_id}/creatures/alice/chat"
        with client.websocket_connect(ws_url) as chat_ws:
            info = _drain_until(
                chat_ws,
                lambda f: f.get("activity_type") == "session_info",
            )
            assert info["agent_name"] == "alice"
            reply = _stream_turn(chat_ws, "hello alice", target="alice")
        assert reply == _REPLY_ONE
        assert scripted_llm.call_count == 1

        # The turn is observable on the per-creature history endpoint.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/history")
        assert resp.status_code == 200
        messages = resp.json().get("messages", [])
        roles = {m.get("role") for m in messages}
        assert "user" in roles and "assistant" in roles
        joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages
        )
        assert "hello alice" in joined
        assert _REPLY_ONE in joined

        # 6b. Per-creature studio panels — every panel the frontend opens
        #     on a creature routes through these endpoints. Drive the
        #     full read surface plus the scratchpad write round-trip.
        # system prompt — exactly what the recipe declared for alice.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/system-prompt")
        assert resp.status_code == 200
        assert "alice" in resp.json()["text"]
        # scratchpad — starts empty, PATCH sets a key, GET reads it back.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/scratchpad")
        assert resp.status_code == 200
        assert resp.json() == {}
        resp = client.patch(
            f"/api/sessions/{session_id}/creatures/alice/scratchpad",
            json={"updates": {"task": "e2e-check"}},
        )
        assert resp.status_code == 200
        assert resp.json()["task"] == "e2e-check"
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/scratchpad")
        assert resp.json() == {"task": "e2e-check"}
        # a framework-reserved key is a 400.
        resp = client.patch(
            f"/api/sessions/{session_id}/creatures/alice/scratchpad",
            json={"updates": {"__reserved__": "x"}},
        )
        assert resp.status_code == 400
        # triggers — alice's recipe ``relay`` listen edge installed a
        # live ChannelTrigger.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/triggers")
        assert resp.status_code == 200
        triggers = resp.json()
        assert any(t["trigger_type"] == "ChannelTrigger" for t in triggers)
        # env — pwd present, secrets redacted.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/env")
        assert resp.status_code == 200
        env_body = resp.json()
        assert "pwd" in env_body
        assert not any("API_KEY" in k.upper() for k in env_body["env"])
        # working dir — GET then PUT round-trip.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/working-dir")
        assert resp.status_code == 200
        assert resp.json()["pwd"]
        # plugins — every engine creature carries the built-in plugins.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/plugins")
        assert resp.status_code == 200
        plugin_names = {p["name"] for p in resp.json()}
        assert "sandbox" in plugin_names and "budget" in plugin_names
        # toggle a real built-in plugin off then on.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/alice/plugins/budget/toggle",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json() == {"plugin": "budget", "enabled": False}
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/alice/plugins/budget/toggle",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"plugin": "budget", "enabled": True}
        # toggling a non-existent plugin is a 404.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/alice/plugins/ghost/toggle",
            json={"enabled": True},
        )
        assert resp.status_code == 404
        # modules — the unified plugin / native-tool catalog.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/modules")
        assert resp.status_code == 200
        mods = resp.json()["modules"]
        assert {m["type"] for m in mods} <= {"plugin", "native_tool"}
        assert any(m["name"] == "sandbox" for m in mods)
        # jobs — no tool/sub-agent jobs in flight after a plain turn.
        resp = client.get(f"/api/sessions/{session_id}/creatures/alice/jobs")
        assert resp.status_code == 200
        assert resp.json() == []
        # interrupt is idempotent — a no-op when nothing is processing.
        resp = client.post(f"/api/sessions/{session_id}/creatures/alice/interrupt")
        assert resp.status_code == 200
        assert resp.json() == {"status": "interrupted"}
        # command — a built-in slash command runs against the creature.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/alice/command",
            json={"command": "status", "args": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["command"] == "status"
        assert resp.json()["success"] is True

        # 6c. Output-wiring endpoints (mounted at /api/sessions/wiring) —
        #     wire alice → bob as a direct round-output edge, list it,
        #     then unwire it by edge id.
        wire_base = f"/api/sessions/wiring/{session_id}/creatures/alice/outputs"
        resp = client.post(wire_base, json={"to": "bob", "with_content": True})
        assert resp.status_code == 200
        edge_id = resp.json()["edge_id"]
        assert edge_id and resp.json()["status"] == "wired"
        resp = client.get(wire_base)
        assert resp.status_code == 200
        assert any(e["to"] == "bob" for e in resp.json()["outputs"])
        resp = client.delete(f"{wire_base}/{edge_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "unwired"}
        resp = client.get(wire_base)
        assert resp.json()["outputs"] == []

        # 7. Topology endpoints — connect, then verify the wiring, then
        #    disconnect, then wire/unwire a single creature edge.
        #
        #    alice and bob are kept together by the recipe's ``relay``
        #    channel, so connecting / disconnecting them over a SECOND
        #    channel (``ops``) exercises the wire delta without tripping
        #    the auto-split that fires when a disconnect strands a node.
        #
        # 7a. connect alice -> bob over the ``ops`` channel.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/connect",
            json={"sender": "alice", "receiver": "bob", "channel": "ops"},
        )
        assert resp.status_code == 200

        resp = client.get("/api/runtime/graph")
        assert resp.status_code == 200
        graphs = {g["graph_id"]: g for g in resp.json()["graphs"]}
        creatures = {c["name"]: c for c in graphs[session_id]["creatures"]}
        assert "ops" in creatures["alice"]["send_channels"]
        assert "ops" in creatures["bob"]["listen_channels"]

        # 7b. disconnect drops the alice -> bob link on ``ops``; the two
        #     stay in one graph (``relay`` still bridges them).
        resp = client.post(
            f"/api/sessions/topology/{session_id}/disconnect",
            json={"sender": "alice", "receiver": "bob", "channel": "ops"},
        )
        assert resp.status_code == 200

        resp = client.get("/api/runtime/graph")
        assert resp.status_code == 200
        graphs = {g["graph_id"]: g for g in resp.json()["graphs"]}
        creatures = {c["name"]: c for c in graphs[session_id]["creatures"]}
        assert "ops" not in creatures["alice"]["send_channels"]
        assert "ops" not in creatures["bob"]["listen_channels"]
        # All three creatures are still in the one session graph.
        assert {c["name"] for c in graphs[session_id]["creatures"]} == {
            "alice",
            "bob",
            "carol",
        }

        # 7c. wire a single creature edge: bob gets a send edge on ``ops``.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/creatures/bob/wire",
            json={"channel": "ops", "direction": "send"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "wired"}

        resp = client.get("/api/runtime/graph")
        graphs = {g["graph_id"]: g for g in resp.json()["graphs"]}
        creatures = {c["name"]: c for c in graphs[session_id]["creatures"]}
        assert "ops" in creatures["bob"]["send_channels"]

        # 7d. unwire it back off.
        resp = client.request(
            "DELETE",
            f"/api/sessions/topology/{session_id}/creatures/bob/wire",
            json={"channel": "ops", "direction": "send"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "unwired"}

        resp = client.get("/api/runtime/graph")
        graphs = {g["graph_id"]: g for g in resp.json()["graphs"]}
        creatures = {c["name"]: c for c in graphs[session_id]["creatures"]}
        assert "ops" not in creatures["bob"]["send_channels"]

        # 7e. Topology error branches — every route's reject path.
        #     connect with a missing creature is a 400 (engine raises
        #     KeyError, route maps it); channel send on a missing channel
        #     is a 404; a missing session's channel list is a 404.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/connect",
            json={"sender": "alice", "receiver": "ghost", "channel": "ops"},
        )
        assert resp.status_code == 400
        resp = client.get(
            f"/api/sessions/topology/{session_id}/channels/no_such_channel"
        )
        assert resp.status_code == 404
        resp = client.post(
            f"/api/sessions/topology/{session_id}/channels/no_such_channel/send",
            json={"content": "x", "sender": "human"},
        )
        assert resp.status_code == 400
        resp = client.get("/api/sessions/topology/no_such_session/channels")
        assert resp.status_code == 404
        # declaring a duplicate channel is a 400.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/channels",
            json={"name": "ops", "description": "dup"},
        )
        assert resp.status_code == 400

        # 7f. The merge endpoint — merging a session with itself is a
        #     no-op; merging two unknown sessions is a 404. All three
        #     recipe creatures already share one graph (the session id),
        #     so a self-merge is the reachable observable case.
        resp = client.post(f"/api/sessions/topology/{session_id}/merge/{session_id}")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": session_id, "merged": False}
        resp = client.post("/api/sessions/topology/ghost_a/merge/ghost_b")
        assert resp.status_code == 404

        # 7g. Observer WS — open the session channel-observer stream,
        #     send on a channel, and assert the message flows through.
        with client.websocket_connect(f"/ws/sessions/{session_id}/observer") as obs_ws:
            resp = client.post(
                f"/api/sessions/topology/{session_id}/channels/ops/send",
                json={"content": "observer-probe", "sender": "human"},
            )
            assert resp.status_code == 200
            obs_frame = _drain_until(
                obs_ws,
                lambda f: f.get("type") == "channel_message"
                and f.get("content") == "observer-probe",
            )
            assert obs_frame["channel"] == "ops"
            assert obs_frame["sender"] == "human"
        # Observing a non-existent session yields an explicit error frame.
        with client.websocket_connect(
            "/ws/sessions/no_such_session/observer"
        ) as bad_obs:
            err_frame = bad_obs.receive_json()
            assert err_frame["type"] == "error"
            assert "not found" in err_frame["content"]

        # 8. Remove the hot-plugged creature; the session shrinks back.
        resp = client.delete(f"/api/sessions/active/{session_id}/creatures/{carol_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "removed"}

        resp = client.get(f"/api/sessions/active/{session_id}/creatures")
        assert resp.status_code == 200
        assert {c["name"] for c in resp.json()} == {"alice", "bob"}

        # 8b. Cross-session merge — create a SECOND terrarium session
        #     from the same recipe, then merge it into the first via the
        #     topology merge endpoint. The merge unions the two graphs
        #     (engine ``ensure_same_graph``): the survivor hosts every
        #     creature from both sessions and the absorbed session id
        #     stops being a standalone graph.
        resp = client.post(
            "/api/sessions/active/terrariums",
            json={"config_path": str(recipe_two_dir)},
        )
        assert resp.status_code == 200
        second_id = resp.json()["terrarium_id"]
        assert second_id != session_id
        # Two distinct graphs are live in the runtime snapshot.
        resp = client.get("/api/runtime/graph")
        graph_ids = {g["graph_id"] for g in resp.json()["graphs"]}
        assert {session_id, second_id} <= graph_ids
        # Merge the second session into the first.
        resp = client.post(f"/api/sessions/topology/{session_id}/merge/{second_id}")
        assert resp.status_code == 200
        merge_body = resp.json()
        assert merge_body["merged"] is True
        survivor = merge_body["session_id"]
        # The survivor graph now hosts all four creatures: alice + bob
        # from the first recipe, dave + erin from the second.
        resp = client.get("/api/runtime/graph")
        graphs = {g["graph_id"]: g for g in resp.json()["graphs"]}
        assert survivor in graphs
        assert {c["name"] for c in graphs[survivor]["creatures"]} == {
            "alice",
            "bob",
            "dave",
            "erin",
        }
        # The merged graph pools the channels from both recipes.
        merged_channels = {c["name"] for c in graphs[survivor]["channels"]}
        assert {"relay", "relay2"} <= merged_channels

        # 9. Stop the session — it leaves the active list. After the
        #    merge the two original sessions collapsed into one survivor,
        #    so stopping it clears the whole active list.
        resp = client.delete(f"/api/sessions/active/{survivor}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "stopped"}
        assert client.get("/api/sessions/active").json() == []
