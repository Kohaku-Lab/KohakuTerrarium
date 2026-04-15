"""Unit tests for the Phase 1 read-only API endpoints.

These endpoints expose existing agent / session state over HTTP for
the new frontend panels. Each test mounts a minimal FastAPI app with
the real route handler but a mocked ``KohakuManager`` that returns a
fake agent whose surface is exactly what the endpoint needs.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.api.routes import agents as agents_route
from kohakuterrarium.api.routes import files as files_route
from kohakuterrarium.api.routes import sessions as sessions_route
from kohakuterrarium.api.routes import terrariums as terrariums_route
from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.core.trigger_manager import TriggerInfo
from datetime import datetime


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


def _make_client(fake_agent, *, agent_id: str = "test-agent") -> TestClient:
    """Build a FastAPI app wired to our agents router + a fake manager."""
    app = FastAPI()
    app.include_router(agents_route.router, prefix="/api/agents")

    fake_session = SimpleNamespace(agent=fake_agent)
    fake_manager = SimpleNamespace(_agents={agent_id: fake_session})

    def _override_manager():
        return fake_manager

    app.dependency_overrides[get_manager] = _override_manager
    return TestClient(app)


def _make_terrarium_client(
    fake_agent, *, terrarium_id: str = "terrarium_test"
) -> TestClient:
    app = FastAPI()
    app.include_router(terrariums_route.router, prefix="/api/terrariums")

    fake_session = SimpleNamespace(agent=fake_agent)

    class _FakeManager:
        def terrarium_mount(self, tid, target):
            if tid != terrarium_id:
                raise ValueError(f"Terrarium not found: {tid}")
            if target in {"root", "worker"}:
                return fake_session
            raise ValueError(f"Creature not found: {target}")

    app.dependency_overrides[get_manager] = lambda: _FakeManager()
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

    resp = client.get("/api/agents/test-agent/scratchpad")

    assert resp.status_code == 200
    assert resp.json() == {"answer": "42", "language": "python"}


def test_get_scratchpad_404_for_unknown_agent():
    client = _make_client(_make_fake_agent())
    resp = client.get("/api/agents/nope/scratchpad")
    assert resp.status_code == 404


def test_patch_scratchpad_merges_updates_and_deletes_nulls():
    sp = Scratchpad()
    sp.set("keep", "yes")
    sp.set("drop", "gone-after-patch")
    client = _make_client(_make_fake_agent(scratchpad=sp))

    resp = client.patch(
        "/api/agents/test-agent/scratchpad",
        json={"updates": {"new": "hello", "drop": None}},
    )

    assert resp.status_code == 200
    assert resp.json() == {"keep": "yes", "new": "hello"}
    # Ensure the live agent's scratchpad really changed
    assert sp.get("drop") is None
    assert sp.get("new") == "hello"


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

    resp = client.get("/api/agents/test-agent/triggers")

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
    resp = client.get("/api/agents/test-agent/triggers")
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
    resp = client.get("/api/agents/test-agent/env")

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
    resp = client.get("/api/agents/test-agent/system-prompt")
    assert resp.status_code == 200
    assert resp.json() == {"text": "You are the agent. Be helpful."}


# ----------------------------------------------------------------------
# Terrarium inspection endpoints
# ----------------------------------------------------------------------


def test_terrarium_get_scratchpad_returns_target_dict():
    sp = Scratchpad()
    sp.set("answer", "42")
    client = _make_terrarium_client(_make_fake_agent(scratchpad=sp))

    resp = client.get("/api/terrariums/terrarium_test/scratchpad/root")

    assert resp.status_code == 200
    assert resp.json() == {"answer": "42"}


def test_terrarium_patch_scratchpad_merges_updates_and_deletes_nulls():
    sp = Scratchpad()
    sp.set("keep", "yes")
    sp.set("drop", "gone-after-patch")
    client = _make_terrarium_client(_make_fake_agent(scratchpad=sp))

    resp = client.patch(
        "/api/terrariums/terrarium_test/scratchpad/worker",
        json={"updates": {"new": "hello", "drop": None}},
    )

    assert resp.status_code == 200
    assert resp.json() == {"keep": "yes", "new": "hello"}


def test_terrarium_get_env_filters_credentials(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "hidden")
    monkeypatch.setenv("SAFE_VAR", "visible")
    client = _make_terrarium_client(_make_fake_agent(working_dir="/tmp/terrarium-cwd"))

    resp = client.get("/api/terrariums/terrarium_test/env/root")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pwd"] == "/tmp/terrarium-cwd"
    assert "MY_SECRET" not in body["env"]
    assert body["env"].get("SAFE_VAR") == "visible"


def test_terrarium_list_triggers_returns_expected_shape():
    triggers = [
        TriggerInfo(
            trigger_id="trigger_abc123",
            trigger_type="ChannelTrigger",
            running=True,
            created_at=datetime(2026, 4, 10, 12, 34, 56),
        ),
    ]
    client = _make_terrarium_client(_make_fake_agent(triggers=triggers))

    resp = client.get("/api/terrariums/terrarium_test/triggers/root")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["trigger_id"] == "trigger_abc123"


def test_terrarium_get_system_prompt_returns_text():
    client = _make_terrarium_client(
        _make_fake_agent(system_prompt="You are the terrarium root. Be helpful.")
    )
    resp = client.get("/api/terrariums/terrarium_test/system-prompt/root")
    assert resp.status_code == 200
    assert resp.json() == {"text": "You are the terrarium root. Be helpful."}


def test_terrarium_agent_only_endpoint_rejects_channel_target():
    client = _make_terrarium_client(_make_fake_agent())
    resp = client.get("/api/terrariums/terrarium_test/env/ch:tasks")
    assert resp.status_code == 400


# ----------------------------------------------------------------------
# Files browse endpoint
# ----------------------------------------------------------------------


def test_files_browse_lists_roots(tmp_path: Path, monkeypatch):
    root_a = tmp_path / "workspace"
    root_b = tmp_path / "home"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setattr(
        files_route,
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
    monkeypatch.setattr(files_route, "_list_browse_roots", lambda: [root.resolve()])
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
        files_route,
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
    monkeypatch.setattr(sessions_route, "_SESSION_DIR", tmp_path)

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

    monkeypatch.setattr(sessions_route, "SessionStore", _FakeStore)
    app = FastAPI()
    app.include_router(sessions_route.router, prefix="/api/sessions")
    client = TestClient(app)

    resp = client.get("/api/sessions/history-session/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["targets"] == ["root", "worker", "ch:tasks"]


def test_session_history_returns_agent_messages_and_events(tmp_path: Path, monkeypatch):
    fake_session = tmp_path / "history-session.kohakutr"
    fake_session.write_bytes(b"")
    monkeypatch.setattr(sessions_route, "_SESSION_DIR", tmp_path)

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

    monkeypatch.setattr(sessions_route, "SessionStore", _FakeStore)
    app = FastAPI()
    app.include_router(sessions_route.router, prefix="/api/sessions")
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
    monkeypatch.setattr(sessions_route, "_SESSION_DIR", tmp_path)

    class _FakeStore:
        def __init__(self, path):
            pass

        def load_meta(self):
            return {"agents": ["root"], "terrarium_channels": [{"name": "tasks"}]}

        def get_channel_messages(self, channel):
            return [{"sender": "root", "content": "queued", "ts": 1.0}]

        def close(self, update_status=False):
            pass

    monkeypatch.setattr(sessions_route, "SessionStore", _FakeStore)
    app = FastAPI()
    app.include_router(sessions_route.router, prefix="/api/sessions")
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
    monkeypatch.setattr(sessions_route, "_SESSION_DIR", tmp_path)
    app = FastAPI()
    app.include_router(sessions_route.router, prefix="/api/sessions")
    client = TestClient(app)

    resp = client.get("/api/sessions/nope/memory/search", params={"q": "hello"})
    assert resp.status_code == 404


def test_memory_search_response_shape(tmp_path: Path, monkeypatch):
    """When the SessionMemory call succeeds, the response has the expected shape."""
    # Create a fake .kohakutr file so the resolve step succeeds.
    fake_session = tmp_path / "test-session.kohakutr"
    fake_session.write_bytes(b"")
    monkeypatch.setattr(sessions_route, "_SESSION_DIR", tmp_path)

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

        class state:
            @staticmethod
            def get(key):
                raise KeyError(key)

    # Mock manager to return no live agents.
    fake_manager = SimpleNamespace(_agents={})
    monkeypatch.setattr(sessions_route, "get_manager", lambda: fake_manager)
    monkeypatch.setattr(sessions_route, "SessionMemory", _FakeMemory)
    monkeypatch.setattr(sessions_route, "SessionStore", _FakeStore)
    monkeypatch.setattr(sessions_route, "create_embedder", lambda cfg: None)

    app = FastAPI()
    app.include_router(sessions_route.router, prefix="/api/sessions")
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
# Log WS route is registered
# ----------------------------------------------------------------------


def test_log_ws_route_is_registered():
    """The WS /ws/logs route is mounted on the app factory."""
    from kohakuterrarium.api.app import create_app

    app = create_app()
    paths = {r.path for r in app.routes}
    assert "/ws/logs" in paths
