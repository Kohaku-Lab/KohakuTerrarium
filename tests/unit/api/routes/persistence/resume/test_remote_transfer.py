"""Atomic transfer and controller-mirror boundaries for remote resume."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from aiofiles.base import AiofilesContextManager

from kohakuterrarium.api.routes.persistence.resume import (
    remote_transfer as transfer_mod,
)
from kohakuterrarium.api.routes.persistence.resume import remote as remote_mod
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

    monkeypatch.setattr(transfer_mod, "stream_write_path", write)
    host = Host()

    with pytest.raises(asyncio.CancelledError):
        await transfer_mod.push_and_resume_member(
            host=host,
            request=_request_without_mirror(),
            path=path,
            on_node="w1",
        )

    assert transferred == [(host, "w1", "config://", "resume/saved.kohakutr", path)]
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

    monkeypatch.setattr(transfer_mod, "stream_write_path", write)
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


async def test_missing_source_is_404_without_starting_worker_transfer(tmp_path):
    class Host:
        async def request(self, **kwargs):
            raise AssertionError("missing source must not reach worker")

    with pytest.raises(HTTPException) as error:
        await transfer_mod.push_and_resume_member(
            host=Host(),
            request=_request_without_mirror(),
            path=tmp_path / "missing.kohakutr",
            on_node="w1",
        )
    assert error.value.status_code == 404


async def test_source_close_error_is_404_and_aborts_before_commit(
    tmp_path, monkeypatch
):
    source = tmp_path / "close-error.kohakutr"
    source.write_bytes(b"archive")
    original_exit = AiofilesContextManager.__aexit__

    async def close_error(manager, *args):
        await original_exit(manager, *args)
        raise OSError("source close failed")

    class Host:
        def __init__(self):
            self.calls = []

        async def request(self, *, type, **kwargs):
            self.calls.append(type)
            if type == "write_begin":
                return {"transfer_id": "close-error"}
            if type == "write_commit":
                raise AssertionError("source must close before commit")
            return {}

    monkeypatch.setattr(AiofilesContextManager, "__aexit__", close_error)
    host = Host()
    with pytest.raises(HTTPException) as error:
        await transfer_mod.push_and_resume_member(
            host=host,
            request=_request_without_mirror(),
            path=source,
            on_node="w1",
        )
    assert error.value.status_code == 404
    assert "source close failed" in error.value.detail
    assert host.calls == ["write_begin", "write_chunk", "write_abort"]
    source.unlink()


async def test_transport_os_error_is_502_and_source_handle_is_closed(tmp_path):
    source = tmp_path / "source.kohakutr"
    source.write_bytes(b"archive")

    class Host:
        async def request(self, **kwargs):
            raise OSError("transport disconnected")

    with pytest.raises(HTTPException) as error:
        await transfer_mod.push_and_resume_member(
            host=Host(),
            request=_request_without_mirror(),
            path=source,
            on_node="w1",
        )
    assert error.value.status_code == 502
    assert "transport disconnected" in error.value.detail
    source.unlink()
