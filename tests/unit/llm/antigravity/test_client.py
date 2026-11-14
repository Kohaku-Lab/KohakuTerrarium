import json
import time

import httpx
import pytest

from kohakuterrarium.llm.antigravity import auth
from kohakuterrarium.llm.antigravity.client import discover_models, user_agent
from kohakuterrarium.llm.antigravity.auth import AntigravityError


@pytest.mark.parametrize(
    "system,machine,expected",
    [
        ("linux", "x86_64", "os_type=linux; arch=amd64"),
        ("linux", "aarch64", "os_type=linux; arch=arm64"),
        ("win32", "AMD64", "os_type=windows; arch=amd64"),
        ("win32", "ARM64", "os_type=windows; arch=arm64"),
    ],
)
def test_agy_157_header_profile(system, machine, expected):
    assert user_agent(system, machine) == (
        f"antigravity/cli/1.5.7 (aidev_client; {expected}; "
        "cl=1019769835; auth_method=consumer)"
    )


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
        assert request.headers["user-agent"].startswith("antigravity/cli/1.5.7 (")
        assert "auth_method=consumer" in request.headers["user-agent"]
        assert "client-metadata" not in request.headers
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
