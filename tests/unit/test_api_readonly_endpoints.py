"""Unit tests for the read-only API endpoints.

These endpoints expose existing creature state over HTTP for the new
frontend panels.  Each test mounts a minimal FastAPI app with the
real route handler but a mocked :class:`Terrarium` engine that
returns a fake creature whose surface is exactly what the endpoint
needs.

Phase 3 of the studio cleanup removed the legacy ``/api/agents/...``
and ``/api/terrariums/.../{target}/...`` routes; this file now
exercises the canonical ``/api/sessions/{sid}/creatures/{cid}/...``
shape through ``api/routes/sessions_v2/creatures_state.py``.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.routes.attach import files as files_route
from kohakuterrarium.api.routes.sessions_v2 import creatures_state
from kohakuterrarium.api.routes.sessions_v2 import memory as sessions_memory_route
from kohakuterrarium.studio.persistence import history as persistence_history
from kohakuterrarium.studio.persistence import store as persistence_store
from kohakuterrarium.studio.sessions import memory_search as sessions_memory_search

from tests.unit._persistence_test_helpers import mount_session_routes
from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.core.trigger_manager import TriggerInfo
from kohakuterrarium.studio.attach import workspace_files


def _make_fake_agent(
    *,
    scratchpad: Scratchpad | None = None,
    triggers: list[TriggerInfo] | None = None,
    system_prompt: str = "you are a helpful creature",
    working_dir: str = "/tmp/fake-cwd",
):
    """Build a minimal fake agent with the attributes the endpoints read."""
    sp = scratchpad or Scratchpad()
    tm = MagicMock()
    tm.list.return_value = triggers or []
    agent = SimpleNamespace(
        scratchpad=sp,
        trigger_manager=tm,
        get_system_prompt=lambda: system_prompt,
        _working_dir=working_dir,
    )
    return agent


def _make_engine_with_creature(fake_agent, creature_id: str = "test-agent"):
    """Build a fake engine whose ``get_creature`` returns the fake agent."""
    fake_creature = SimpleNamespace(agent=fake_agent)

    class _FakeEngine:
        def get_creature(self, cid: str):
            if cid != creature_id:
                raise KeyError(cid)
            return fake_creature

    return _FakeEngine()


def _make_client(fake_agent, *, creature_id: str = "test-agent") -> TestClient:
    """FastAPI app wired to creatures_state router + a fake engine."""
    app = FastAPI()
    app.include_router(creatures_state.router, prefix="/api/sessions")

    fake_engine = _make_engine_with_creature(fake_agent, creature_id=creature_id)
    app.dependency_overrides[get_engine] = lambda: fake_engine
    return TestClient(app)


def _make_files_client() -> TestClient:
    app = FastAPI()
    app.include_router(files_route.router, prefix="/api/files")
    return TestClient(app)


# ----------------------------------------------------------------------
# Scratchpad
# ----------------------------------------------------------------------


def test_get_scratchpad_returns_dict():
    sp = Scratchpad()
    sp.set("answer", "42")
    sp.set("language", "python")
    client = _make_client(_make_fake_agent(scratchpad=sp))

    resp = client.get("/api/sessions/sess1/creatures/test-agent/scratchpad")

    assert resp.status_code == 200
    assert resp.json() == {"answer": "42", "language": "python"}


def test_get_scratchpad_404_for_unknown_creature():
    client = _make_client(_make_fake_agent())
    resp = client.get("/api/sessions/sess1/creatures/nope/scratchpad")
    assert resp.status_code == 404


def test_patch_scratchpad_merges_updates_and_deletes_nulls():
    sp = Scratchpad()
    sp.set("keep", "yes")
    sp.set("drop", "gone-after-patch")
    client = _make_client(_make_fake_agent(scratchpad=sp))

    resp = client.patch(
        "/api/sessions/sess1/creatures/test-agent/scratchpad",
        json={"updates": {"new": "hello", "drop": None}},
    )

    assert resp.status_code == 200
    assert resp.json() == {"keep": "yes", "new": "hello"}
    # Ensure the live agent's scratchpad really changed
    assert sp.get("drop") is None
    assert sp.get("new") == "hello"


def test_patch_scratchpad_rejects_reserved_keys():
    sp = Scratchpad()
    client = _make_client(_make_fake_agent(scratchpad=sp))

    resp = client.patch(
        "/api/sessions/sess1/creatures/test-agent/scratchpad",
        json={"updates": {"__private__": "nope"}},
    )

    assert resp.status_code == 400
    assert sp.get("__private__") is None


# ----------------------------------------------------------------------
# Triggers
# ----------------------------------------------------------------------


def test_list_triggers_returns_expected_shape():
    triggers = [
        TriggerInfo(
            trigger_id="trigger_abc123",
            trigger_type="ChannelTrigger",
            running=True,
            created_at=datetime(2026, 4, 10, 12, 34, 56),
        ),
    ]
    client = _make_client(_make_fake_agent(triggers=triggers))

    resp = client.get("/api/sessions/sess1/creatures/test-agent/triggers")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    entry = body[0]
    assert entry["trigger_id"] == "trigger_abc123"
    assert entry["trigger_type"] == "ChannelTrigger"
    assert entry["running"] is True
    assert entry["created_at"].startswith("2026-04-10")


def test_list_triggers_empty_when_none():
    client = _make_client(_make_fake_agent())
    resp = client.get("/api/sessions/sess1/creatures/test-agent/triggers")
    assert resp.status_code == 200
    assert resp.json() == []


# ----------------------------------------------------------------------
# Env — critical filtering test
# ----------------------------------------------------------------------


def test_get_env_filters_credentials(monkeypatch):
    # Inject some hostile env vars.
    monkeypatch.setenv("MY_SECRET", "you-should-not-see-this")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-filtered")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_filtered")
    monkeypatch.setenv("SAFE_VAR", "visible")
    monkeypatch.setenv("MY_PASSWORD", "hunter2")
    monkeypatch.setenv("AUTH_HEADER", "Bearer filtered")

    client = _make_client(_make_fake_agent(working_dir="/tmp/fake-cwd"))
    resp = client.get("/api/sessions/sess1/creatures/test-agent/env")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pwd"] == "/tmp/fake-cwd"
    env = body["env"]
    # Forbidden keys must be filtered
    assert "MY_SECRET" not in env
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "MY_PASSWORD" not in env
    assert "AUTH_HEADER" not in env
    # Benign keys must remain
    assert env.get("SAFE_VAR") == "visible"


# ----------------------------------------------------------------------
# System prompt
# ----------------------------------------------------------------------


def test_get_system_prompt_returns_text():
    client = _make_client(
        _make_fake_agent(system_prompt="You are the agent. Be helpful.")
    )
    resp = client.get("/api/sessions/sess1/creatures/test-agent/system-prompt")
    assert resp.status_code == 200
    assert resp.json() == {"text": "You are the agent. Be helpful."}


# ----------------------------------------------------------------------
# Files browse endpoint
# ----------------------------------------------------------------------


def test_files_browse_lists_roots(tmp_path: Path, monkeypatch):
    root_a = tmp_path / "workspace"
    root_b = tmp_path / "home"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setattr(
        workspace_files,
        "_list_browse_roots",
        lambda: [root_a.resolve(), root_b.resolve()],
    )
    client = _make_files_client()

    resp = client.get("/api/files/browse")

    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] is None
    assert body["parent"] is None
    assert [entry["path"] for entry in body["roots"]] == [
        str(root_a.resolve()),
        str(root_b.resolve()),
    ]


def test_files_browse_lists_child_directories_only(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / "beta").mkdir()
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    (root / ".git").mkdir()
    monkeypatch.setattr(workspace_files, "_list_browse_roots", lambda: [root.resolve()])
    client = _make_files_client()

    resp = client.get("/api/files/browse", params={"path": str(root)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["current"]["path"] == str(root.resolve())
    assert body["parent"] == str(root.resolve().parent)
    assert [entry["name"] for entry in body["directories"]] == ["alpha", "beta"]


def test_files_browse_returns_parent_directory(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    nested = root / "alpha" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.setattr(
        workspace_files,
        "_list_browse_roots",
        lambda: [root.anchor and Path(root.anchor) or root.resolve()],
    )
    client = _make_files_client()

    resp = client.get("/api/files/browse", params={"path": str(nested)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["parent"] == str((root / "alpha").resolve())


# ----------------------------------------------------------------------
# Saved session history endpoints
# ----------------------------------------------------------------------


def test_session_history_index_lists_targets(tmp_path: Path, monkeypatch):
    fake_session = tmp_path / "history-session.kohakutr"
    fake_session.write_bytes(b"")
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)

    class _FakeStore:
        def __init__(self, path):
            pass

        def load_meta(self):
            return {
                "agents": ["root", "worker"],
                "terrarium_channels": [{"name": "tasks", "type": "queue"}],
            }

        def close(self, update_status=False):
            pass

    monkeypatch.setattr(persistence_history, "SessionStore", _FakeStore)
    app = FastAPI()
    mount_session_routes(app)
    client = TestClient(app)

    resp = client.get("/api/sessions/history-session/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["targets"] == ["root", "worker", "ch:tasks"]


def test_session_history_returns_agent_messages_and_events(tmp_path: Path, monkeypatch):
    fake_session = tmp_path / "history-session.kohakutr"
    fake_session.write_bytes(b"")
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)

    class _FakeStore:
        def __init__(self, path):
            pass

        def load_meta(self):
            return {"agents": ["root"]}

        def load_conversation(self, agent):
            return [{"role": "user", "content": "hello"}]

        def get_events(self, agent):
            return [{"type": "user_input", "content": "hello", "ts": 1.0}]

        def close(self, update_status=False):
            pass

    monkeypatch.setattr(persistence_history, "SessionStore", _FakeStore)
    app = FastAPI()
    mount_session_routes(app)
    client = TestClient(app)

    resp = client.get("/api/sessions/history-session/history/root")

    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["events"][0]["type"] == "user_input"


def test_session_history_returns_channel_messages_as_events(
    tmp_path: Path, monkeypatch
):
    fake_session = tmp_path / "history-session.kohakutr"
    fake_session.write_bytes(b"")
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)

    class _FakeStore:
        def __init__(self, path):
            pass

        def load_meta(self):
            return {"agents": ["root"], "terrarium_channels": [{"name": "tasks"}]}

        def get_channel_messages(self, channel):
            return [{"sender": "root", "content": "queued", "ts": 1.0}]

        def close(self, update_status=False):
            pass

    monkeypatch.setattr(persistence_history, "SessionStore", _FakeStore)
    app = FastAPI()
    mount_session_routes(app)
    client = TestClient(app)

    resp = client.get("/api/sessions/history-session/history/ch%3Atasks")

    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == []
    assert body["events"] == [
        {
            "type": "channel_message",
            "channel": "tasks",
            "sender": "root",
            "content": "queued",
            "ts": 1.0,
        }
    ]


# ----------------------------------------------------------------------
# Memory search endpoint
# ----------------------------------------------------------------------


def test_memory_search_404_on_unknown_session(tmp_path: Path, monkeypatch):
    # Point the session lookup at a real (empty) temp dir so the test
    # doesn't accidentally hit the user's actual session store.
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
    app = FastAPI()
    mount_session_routes(app)
    client = TestClient(app)

    resp = client.get("/api/sessions/nope/memory/search", params={"q": "hello"})
    assert resp.status_code == 404


def test_memory_search_response_shape(tmp_path: Path, monkeypatch):
    """When the SessionMemory call succeeds, the response has the expected shape."""
    # Create a fake .kohakutr file so the resolve step succeeds.
    fake_session = tmp_path / "test-session.kohakutr"
    fake_session.write_bytes(b"")
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)

    class _FakeResult:
        def __init__(self):
            self.content = "hello from memory"
            self.round_num = 1
            self.block_num = 2
            self.agent = "creature-a"
            self.block_type = "assistant"
            self.score = 0.9
            self.ts = 1_700_000_000
            self.tool_name = ""
            self.channel = ""

    class _FakeMemory:
        def __init__(self, path, embedder=None, store=None):
            pass

        def search(self, query, mode, k, agent):
            return [_FakeResult()]

        def index_events(self, agent, events):
            pass

    class _FakeStore:
        def __init__(self, path):
            pass

        def load_meta(self):
            return {"agents": ["creature-a"]}

        def get_events(self, agent):
            return []

        def close(self, update_status=False):
            pass

        def flush(self):
            pass

        class state:
            @staticmethod
            def get(key):
                raise KeyError(key)

    # Mock engine to return no live creatures.
    fake_engine = SimpleNamespace(list_creatures=lambda: [])
    monkeypatch.setattr(sessions_memory_route, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(sessions_memory_search, "SessionMemory", _FakeMemory)
    monkeypatch.setattr(sessions_memory_search, "SessionStore", _FakeStore)
    monkeypatch.setattr(sessions_memory_search, "create_embedder", lambda cfg: None)

    app = FastAPI()
    mount_session_routes(app)
    client = TestClient(app)

    resp = client.get(
        "/api/sessions/test-session/memory/search",
        params={"q": "hello", "mode": "fts", "k": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hello"
    assert body["mode"] == "fts"
    assert body["count"] == 1
    assert body["results"][0]["content"] == "hello from memory"
    assert body["results"][0]["agent"] == "creature-a"


# ----------------------------------------------------------------------
# Settings / Log WS sanity
# ----------------------------------------------------------------------


def test_settings_profiles_round_trip_includes_variation_groups(
    tmp_path: Path, monkeypatch
):
    from fastapi import FastAPI

    from kohakuterrarium.api.routes.identity import llm as settings_route
    from kohakuterrarium.llm.profile_types import LLMBackend

    profiles_path = tmp_path / "llm_profiles.yaml"
    monkeypatch.setattr(
        "kohakuterrarium.studio.identity.llm_backends.load_backends",
        lambda: {
            "openai": LLMBackend(
                name="openai",
                backend_type="openai",
                base_url="https://api.openai.com/v1",
                api_key_env="OPENAI_API_KEY",
            )
        },
    )
    monkeypatch.setattr("kohakuterrarium.llm.profiles.PROFILES_PATH", profiles_path)

    app = FastAPI()
    app.include_router(settings_route.router, prefix="/api/settings")
    client = TestClient(app)

    payload = {
        "name": "custom-variant",
        "model": "gpt-test",
        "provider": "openai",
        "max_context": 123000,
        "max_output": 4567,
        "temperature": 0.2,
        "reasoning_effort": "high",
        "service_tier": "priority",
        "extra_body": {"foo": "bar"},
        "variation_groups": {
            "reasoning": {
                "low": {"extra_body.reasoning.effort": "low"},
                "high": {"extra_body.reasoning.effort": "high"},
            }
        },
    }

    resp = client.post("/api/settings/profiles", json=payload)
    assert resp.status_code == 200

    resp = client.get("/api/settings/profiles")
    assert resp.status_code == 200
    body = resp.json()
    profile = next(
        item for item in body["profiles"] if item["name"] == "custom-variant"
    )
    assert profile["variation_groups"] == payload["variation_groups"]
    assert profile["extra_body"] == {"foo": "bar"}


def test_log_ws_route_is_registered():
    """The WS /ws/logs route is mounted on the app factory."""
    from kohakuterrarium.api.app import create_app

    app = create_app()
    paths = {r.path for r in app.routes}
    assert "/ws/logs" in paths
