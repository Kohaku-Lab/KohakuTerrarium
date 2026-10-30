"""Actual ASGI cancellation preserves in-flight candidate resource ownership."""

import asyncio
import threading
from contextlib import closing

import httpx
import pytest

import kohakuterrarium.session.readonly.candidate as candidate
import kohakuterrarium.session.readonly.metadata as readonly_mod
from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.routes.persistence.preflight_metadata import (
    local_metadata_reader,
)
from kohakuterrarium.session.occupancy.gate import GATE
from kohakuterrarium.session.store import SessionStore


@pytest.mark.asyncio
async def test_http_cancel_keeps_source_until_worker_close(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("KT_SESSION_DIR", str(root))
    path = root / "saved.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("saved", "agent", "", str(tmp_path), ["alice"])
    entered, release, closed, done = (threading.Event() for _ in range(4))
    real_dispose = readonly_mod.dispose
    errors = []
    result = []

    def gated_dispose(handle):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test source not released")
        released = real_dispose(handle)
        closed.set()
        return released

    def selected(path):
        try:
            with monkeypatch.context() as scoped:
                scoped.setattr(readonly_mod, "dispose", gated_dispose)
                outcome = candidate.read_metadata_candidate(path, eligible=True)
                result.append(outcome)
                return outcome.meta
        except BaseException as exc:
            errors.append(exc)
            raise
        finally:
            done.set()

    app = create_app()
    app.dependency_overrides[local_metadata_reader] = lambda: selected
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request = asyncio.create_task(
            client.post("/api/sessions/saved/resume/preflight")
        )
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            assert GATE.snapshot(path).new_active == 1
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            ticks = 0
            for _ in range(5):
                await asyncio.sleep(0.005)
                ticks += 1
            assert ticks == 5 and not done.is_set() and not closed.is_set()
            assert GATE.snapshot(path).new_active == 1
            missing = await client.post("/api/sessions/missing/resume/preflight")
            assert missing.status_code == 404
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            assert await asyncio.to_thread(done.wait, 10)
    assert closed.is_set() and not errors
    assert [row.reason for row in result] == ["ok"]
    assert result[0].meta["session_id"] == "saved"
    assert not GATE.snapshot(path).registered
