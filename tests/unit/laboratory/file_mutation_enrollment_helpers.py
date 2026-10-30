"""Shared helpers for laboratory exact-path mutation occupancy tests."""

import base64
import hashlib
import time
from pathlib import Path

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_files import TerrariumFilesAdapter
from kohakuterrarium.session.occupancy.gate import GATE, REASON_OK, try_fast


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def msg(type_, body=None) -> AppMessage:
    return AppMessage(
        namespace=TerrariumFilesAdapter.NAMESPACE,
        type=type_,
        body=body or {},
        sender_node="ctrl",
        request_id=None,
        in_reply_to=None,
    )


class FakeNode:
    def __init__(self):
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


class FakeEngine:
    def __init__(self):
        self._session_stores = {}


def make_files_adapter(monkeypatch, tmp_path) -> TerrariumFilesAdapter:
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "kt-config"))
    return TerrariumFilesAdapter(FakeEngine(), FakeNode())


def wait_pending(path, count: int = 1, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if GATE.snapshot(path).legacy_pending == count:
            return
        time.sleep(0.01)
    raise AssertionError(f"pending occupancy did not reach {count}")


def wait_active(path, count: int = 1, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if GATE.snapshot(path).legacy_active == count:
            return
        time.sleep(0.01)
    raise AssertionError(f"active occupancy did not reach {count}")


def assert_idle(path) -> None:
    snap = GATE.snapshot(path)
    assert snap.legacy_active == 0
    assert snap.legacy_pending == 0
    assert snap.legacy_uncertain is False
    admitted = try_fast(path, eligible=True)
    assert admitted.reason == REASON_OK
    assert admitted.lease is not None
    admitted.lease.finish(source_released=True)


def plain_file(tmp_path, name: str = "sess.kohakutr", data: bytes = b"session") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
