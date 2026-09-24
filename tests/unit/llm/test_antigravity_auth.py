import asyncio
import base64
import json
import time

import pytest

from kohakuterrarium.llm import antigravity_auth as auth
from kohakuterrarium.llm.antigravity_auth import AntigravityError, BorrowedCredential


def payload(access="access-example", expiry="2099-01-01T00:00:00Z"):
    return json.dumps(
        {
            "auth_method": "consumer",
            "token": {
                "access_token": access,
                "refresh_token": "never-retain-this",
                "token_type": "Bearer",
                "expiry": expiry,
            },
        }
    )


def test_consumer_parser_discards_refresh_and_redacts_repr():
    raw = payload()
    for value in (raw, "go-keyring-base64:" + base64.b64encode(raw.encode()).decode()):
        token = auth.parse_credential(value, "test")
        assert token.access_token == "access-example"
        assert "access-example" not in repr(token)
        assert "never-retain-this" not in repr(token.__dict__)
        assert token.fresh()


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "{}",
        "not json",
        payload("injected\nheader"),
        payload(expiry="no-date"),
        payload(expiry="2026-01-01"),
    ],
)
def test_invalid_credentials_have_redacted_errors(raw):
    with pytest.raises(AntigravityError, match="malformed_credential") as caught:
        auth.parse_credential(raw, "test")
    assert raw not in str(caught.value)


def test_missing_conflicting_sources_and_offline_status(monkeypatch):
    a = auth.parse_credential(payload(), "keyring")
    b = auth.parse_credential(payload("different"), "file")
    monkeypatch.setattr(auth, "read_sources", lambda: [])
    assert auth.AgyCredentials.status()["state"] == "login_required"
    monkeypatch.setattr(auth, "read_sources", lambda: [a, b])
    assert auth.AgyCredentials.status()["state"] == "ambiguous_sources"
    monkeypatch.setattr(auth, "read_sources", lambda: [a])
    assert auth.AgyCredentials.available()
    assert "access-example" not in json.dumps(auth.AgyCredentials.status())


@pytest.mark.asyncio
async def test_refresh_is_shared_and_survives_caller_cancellation(
    monkeypatch, tmp_path
):
    old = BorrowedCredential("old", time.time() - 1, "test")
    new = BorrowedCredential("new", time.time() + 3600, "test")
    current = [old]
    started, release = asyncio.Event(), asyncio.Event()
    runs = []

    async def run():
        runs.append(1)
        started.set()
        await release.wait()
        current[0] = new

    monkeypatch.setattr(auth, "read_sources", lambda: current)
    monkeypatch.setattr(auth, "refresh_lock_path", lambda: tmp_path / "refresh.lock")
    monkeypatch.setattr(auth, "run_agy_models", run)
    first = asyncio.create_task(auth.AgyCredentials.ensure_fresh())
    await started.wait()
    second = asyncio.create_task(auth.AgyCredentials.ensure_fresh())
    await asyncio.sleep(0.02)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == new
    assert runs == [1]


@pytest.mark.asyncio
async def test_force_refresh_requires_rotation(monkeypatch, tmp_path):
    current = BorrowedCredential("rejected", time.time() + 3600, "test")
    monkeypatch.setattr(auth, "read_sources", lambda: [current])
    monkeypatch.setattr(auth, "refresh_lock_path", lambda: tmp_path / "refresh.lock")

    async def no_change():
        return None

    monkeypatch.setattr(auth, "run_agy_models", no_change)
    with pytest.raises(AntigravityError, match="refresh_required"):
        await auth.AgyCredentials.ensure_fresh(rejected=current.fingerprint)
