import json
import time

import httpx
import pytest

from kohakuterrarium.llm import antigravity_auth as auth
from kohakuterrarium.llm.antigravity_client import discover_models
from kohakuterrarium.llm.antigravity_auth import AntigravityError


@pytest.mark.asyncio
async def test_discovery_uses_empty_body_and_one_401_refresh(monkeypatch, tmp_path):
    tokens = [auth.BorrowedCredential("old", time.time() + 3600, "test")]
    requests = []
    runs = []
    monkeypatch.setattr(auth, "read_sources", lambda: tokens)
    monkeypatch.setattr(auth, "refresh_lock_path", lambda: tmp_path / "lock")

    async def refresh():
        runs.append(1)
        tokens[0] = auth.BorrowedCredential("new", time.time() + 3600, "test")

    monkeypatch.setattr(auth, "run_agy_models", refresh)

    def respond(request):
        requests.append(request)
        assert json.loads(request.content) == {}
        assert request.url.path.endswith("fetchAvailableModels")
        if len(requests) == 1:
            return httpx.Response(401, text="old secret")
        assert request.headers["authorization"] == "Bearer new"
        return httpx.Response(
            200, json={"models": {"gemini-3-flash": {"displayName": "Flash"}}}
        )

    assert await discover_models(transport=httpx.MockTransport(respond)) == [
        {"id": "gemini-3-flash", "name": "Flash"}
    ]
    assert len(requests) == 2
    assert runs == [1]


@pytest.mark.asyncio
async def test_discovery_refuses_bad_json_and_never_prints_body(monkeypatch):
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: [auth.BorrowedCredential("secret", time.time() + 3600, "test")],
    )
    with pytest.raises(AntigravityError, match="malformed_response") as caught:
        await discover_models(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, text="secret")
            )
        )
    assert "secret" not in str(caught.value)
