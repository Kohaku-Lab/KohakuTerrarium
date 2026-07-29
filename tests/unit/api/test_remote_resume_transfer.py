"""Atomic transfer and controller-mirror boundaries for remote resume."""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.api.routes.persistence import (
    remote_resume_transfer as transfer_mod,
)
from kohakuterrarium.api.routes.persistence import resume_remote as remote_mod
from kohakuterrarium.session.store import SessionStore


def _request_without_mirror():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_mirror=None))
    )


def test_index_failure_rolls_back_store_and_index(monkeypatch, tmp_path):
    path = tmp_path / "saved.kohakutr"
    store = SessionStore(path)
    store.init_meta("saved", "agent", "/cfg", "/old", ["alice"])
    store.meta["conversation_open"] = False
    store.meta["status"] = "stopped"
    store.meta["last_active"] = "before"
    store.close(update_status=False)

    index = remote_mod.get_session_index_default(tmp_path)
    original_index_row = index.get(path.name)
    original_meta = remote_mod.read_session_meta(path)
    monkeypatch.setattr(remote_mod, "push_index_update", lambda *_args: None)

    with pytest.raises(RuntimeError, match="session index update failed"):
        remote_mod.persist_remote_workspace_meta(
            path,
            {
                "pwd": "/new",
                "conversation_open": True,
                "status": "running",
                "last_active": "after",
            },
            "w1",
        )

    restored_meta = remote_mod.read_session_meta(path)
    for key in remote_mod._MIRROR_KEYS:
        assert restored_meta.get(key) == original_meta.get(key)
    assert index.get(path.name) == original_index_row


@pytest.mark.asyncio
async def test_cancel_after_transfer_deletes_staged_store(monkeypatch, tmp_path):
    path = tmp_path / "saved.kohakutr"
    path.write_bytes(b"saved")
    transferred = []

    async def write(*args):
        transferred.append(args)

    class Host:
        def __init__(self):
            self.calls = []

        async def request(self, *, to_node, namespace, type, body, timeout):
            self.calls.append((namespace, type, body))
            if namespace == "terrarium.files" and type == "stat":
                raise asyncio.CancelledError
            return {}

    monkeypatch.setattr(transfer_mod, "stream_write_file", write)
    host = Host()

    with pytest.raises(asyncio.CancelledError):
        await transfer_mod.push_and_resume_member(
            host=host,
            request=_request_without_mirror(),
            path=path,
            on_node="w1",
        )

    assert transferred
    assert host.calls == [
        (
            "terrarium.files",
            "stat",
            {"scope": "config://", "path": "resume/saved.kohakutr"},
        ),
        (
            "terrarium.files",
            "delete",
            {"scope": "config://", "path": "resume/saved.kohakutr"},
        ),
    ]


@pytest.mark.asyncio
async def test_cancel_after_resume_rolls_back_adopted_graph(monkeypatch, tmp_path):
    path = tmp_path / "saved.kohakutr"
    path.write_bytes(b"saved")

    async def write(*_args):
        return None

    class CancelAfterSessionId(dict):
        def get(self, key, default=None):
            if key == "session_id":
                return "new-sid"
            if key == "meta":
                raise asyncio.CancelledError
            return super().get(key, default)

    class Host:
        def __init__(self):
            self.calls = []

        async def request(self, *, to_node, namespace, type, body, timeout):
            self.calls.append((namespace, type, body))
            if namespace == "terrarium.files" and type == "stat":
                return {"stat": {"path": "C:/worker-config/resume/saved.kohakutr"}}
            if namespace == "terrarium.session" and type == "resume":
                return CancelAfterSessionId()
            if namespace == "terrarium.session" and type == "rollback_resume":
                return {"ok": True}
            return {}

    monkeypatch.setattr(transfer_mod, "stream_write_file", write)
    host = Host()

    with pytest.raises(asyncio.CancelledError):
        await transfer_mod.push_and_resume_member(
            host=host,
            request=_request_without_mirror(),
            path=path,
            on_node="w1",
        )

    assert (
        "terrarium.session",
        "rollback_resume",
        {"graph_id": "new-sid"},
    ) in host.calls
