"""Unit tests for the chunked ``terrarium.files`` write_stream.

Covers both halves of the "pack system": the worker-side streaming
ops on :class:`TerrariumFilesAdapter` (``write_begin`` / ``write_chunk``
/ ``write_commit`` / ``write_abort``) and the host-side driver
:func:`kohakuterrarium.laboratory.file_transfer.stream_write_file`.
They are one feature — a chunk written by the driver is only meaningful
against the ops that reassemble it — so they are exercised together.
"""

import base64
import hashlib

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_files import (
    STREAM_CHUNK_BYTES,
    TerrariumFilesAdapter,
)
from kohakuterrarium.laboratory.file_transfer import stream_write_file


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _msg(type_, body):
    return AppMessage(
        namespace=TerrariumFilesAdapter.NAMESPACE,
        type=type_,
        body=body,
        sender_node="ctrl",
        request_id=None,
        in_reply_to=None,
    )


class _FakeNode:
    def __init__(self):
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


class _FakeEngine:
    pass


class _FakeSender:
    """A Lab ``request`` surface that routes straight into one adapter.

    Mirrors how a real ``HostEngine.request`` reaches a worker's
    ``terrarium.files`` handler — only without the wire — so the
    host-side driver and worker-side ops are tested against each
    other exactly as they run in production.
    """

    def __init__(self, adapter: TerrariumFilesAdapter):
        self._adapter = adapter
        self.calls: list[str] = []

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append(type)
        return await self._adapter._dispatch(_msg(type, body))


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    # ``recipe://`` resolves under KT_CONFIG_DIR — isolate it per test.
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "kt-config"))
    return TerrariumFilesAdapter(_FakeEngine(), _FakeNode())


# ── worker-side streaming ops ─────────────────────────────────────


class TestStreamWriteOps:
    async def test_begin_chunk_commit_roundtrip(self, adapter, tmp_path):
        payload = b"chunked-payload-body" * 100
        begin = await adapter._dispatch(
            _msg(
                "write_begin",
                {
                    "scope": "recipe://r1",
                    "path": "sess.kohakutr",
                    "total_size": len(payload),
                    "sha256": _sha(payload),
                },
            )
        )
        tid = begin["transfer_id"]
        assert begin["chunk_size"] == STREAM_CHUNK_BYTES
        # Two chunks, in order.
        half = len(payload) // 2
        r0 = await adapter._dispatch(
            _msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": _b64(payload[:half])},
            )
        )
        assert r0["received"] == half
        await adapter._dispatch(
            _msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 1, "bytes_b64": _b64(payload[half:])},
            )
        )
        commit = await adapter._dispatch(_msg("write_commit", {"transfer_id": tid}))
        assert commit == {"written": len(payload), "sha256": _sha(payload)}
        # The file landed at the resolved scope path with exact bytes.
        target = tmp_path / "kt-config" / "recipes" / "r1" / "sess.kohakutr"
        assert target.read_bytes() == payload

    async def test_chunk_out_of_order_aborts_transfer(self, adapter):
        begin = await adapter._dispatch(
            _msg(
                "write_begin",
                {"scope": "recipe://r2", "path": "f.bin", "total_size": 10},
            )
        )
        tid = begin["transfer_id"]
        # seq 1 before seq 0 — rejected, and the transfer is discarded.
        out = await adapter._dispatch(
            _msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 1, "bytes_b64": _b64(b"xxxxx")},
            )
        )
        assert out["error"]["kind"] == "invalid"
        assert tid not in adapter._transfers
        # A follow-up chunk on the dead transfer is not_found, not a
        # silent corruption.
        out2 = await adapter._dispatch(
            _msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": _b64(b"abc")},
            )
        )
        assert out2["error"]["kind"] == "not_found"

    async def test_commit_incomplete_rejected(self, adapter):
        begin = await adapter._dispatch(
            _msg(
                "write_begin",
                {"scope": "recipe://r3", "path": "f.bin", "total_size": 100},
            )
        )
        tid = begin["transfer_id"]
        await adapter._dispatch(
            _msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": _b64(b"short")},
            )
        )
        out = await adapter._dispatch(_msg("write_commit", {"transfer_id": tid}))
        assert out["error"]["kind"] == "invalid"
        assert "incomplete" in out["error"]["message"]
        assert tid not in adapter._transfers

    async def test_commit_sha_mismatch_rejected(self, adapter):
        data = b"the-real-bytes"
        begin = await adapter._dispatch(
            _msg(
                "write_begin",
                {
                    "scope": "recipe://r4",
                    "path": "f.bin",
                    "total_size": len(data),
                    "sha256": _sha(b"a-different-thing"),
                },
            )
        )
        tid = begin["transfer_id"]
        await adapter._dispatch(
            _msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": _b64(data)},
            )
        )
        out = await adapter._dispatch(_msg("write_commit", {"transfer_id": tid}))
        assert out["error"]["kind"] == "invalid"
        assert "sha256 mismatch" in out["error"]["message"]

    async def test_chunk_overrun_rejected(self, adapter):
        begin = await adapter._dispatch(
            _msg(
                "write_begin",
                {"scope": "recipe://r5", "path": "f.bin", "total_size": 4},
            )
        )
        tid = begin["transfer_id"]
        out = await adapter._dispatch(
            _msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": _b64(b"too-long")},
            )
        )
        assert out["error"]["kind"] == "invalid"
        assert "overran" in out["error"]["message"]

    async def test_abort_removes_staging(self, adapter, tmp_path):
        begin = await adapter._dispatch(
            _msg(
                "write_begin",
                {"scope": "recipe://r6", "path": "f.bin", "total_size": 3},
            )
        )
        tid = begin["transfer_id"]
        staging = adapter._transfers[tid].staging
        assert staging.exists()
        out = await adapter._dispatch(_msg("write_abort", {"transfer_id": tid}))
        assert out == {}
        assert tid not in adapter._transfers
        assert not staging.exists()

    async def test_detach_cleans_orphan_transfers(self, adapter):
        begin = await adapter._dispatch(
            _msg(
                "write_begin",
                {"scope": "recipe://r7", "path": "f.bin", "total_size": 9},
            )
        )
        staging = adapter._transfers[begin["transfer_id"]].staging
        assert staging.exists()
        adapter.detach()
        # A never-committed transfer leaves no staging file behind.
        assert not staging.exists()
        assert adapter._transfers == {}

    async def test_unknown_transfer_id_not_found(self, adapter):
        out = await adapter._dispatch(
            _msg("write_chunk", {"transfer_id": "ghost", "seq": 0, "bytes_b64": ""})
        )
        assert out["error"]["kind"] == "not_found"
        out2 = await adapter._dispatch(_msg("write_commit", {"transfer_id": "ghost"}))
        assert out2["error"]["kind"] == "not_found"


# ── host-side driver ──────────────────────────────────────────────


class TestStreamWriteFile:
    async def test_roundtrip_multi_chunk(self, adapter, tmp_path):
        # A payload several chunks long — proves the driver slices and
        # the ops reassemble byte-for-byte.
        payload = bytes(range(256)) * 4000  # ~1 MiB, > STREAM_CHUNK_BYTES
        assert len(payload) > STREAM_CHUNK_BYTES
        sender = _FakeSender(adapter)
        result = await stream_write_file(
            sender, "worker-1", "recipe://big", "blob.bin", payload
        )
        assert result == {"written": len(payload), "sha256": _sha(payload)}
        # More than one write_chunk RPC was issued — it really chunked.
        assert sender.calls.count("write_chunk") > 1
        assert sender.calls[0] == "write_begin"
        assert sender.calls[-1] == "write_commit"
        target = tmp_path / "kt-config" / "recipes" / "big" / "blob.bin"
        assert target.read_bytes() == payload

    async def test_roundtrip_empty_payload(self, adapter, tmp_path):
        sender = _FakeSender(adapter)
        result = await stream_write_file(
            sender, "worker-1", "recipe://empty", "zero.bin", b""
        )
        assert result == {"written": 0, "sha256": _sha(b"")}
        assert "write_chunk" not in sender.calls  # zero chunks for empty
        target = tmp_path / "kt-config" / "recipes" / "empty" / "zero.bin"
        assert target.read_bytes() == b""

    async def test_failure_aborts_transfer(self, adapter):
        # A scope error on write_begin must propagate, and on a
        # mid-stream failure the driver issues write_abort so the
        # worker keeps no orphan staging file.
        sender = _FakeSender(adapter)
        with pytest.raises(RuntimeError, match="write_begin failed"):
            await stream_write_file(
                sender, "worker-1", "bad-scope-no-slashes", "x", b"data"
            )
        assert adapter._transfers == {}

    async def test_driver_aborts_on_chunk_failure(self, adapter, monkeypatch):
        # Force a mid-stream chunk failure and assert the driver fires
        # write_abort, leaving the worker with no in-flight transfer.
        sender = _FakeSender(adapter)
        real_dispatch = adapter._dispatch
        state = {"fail_next_chunk": False}

        async def flaky(msg):
            if msg.type == "write_chunk" and state["fail_next_chunk"]:
                return {"error": {"kind": "files", "message": "disk full"}}
            if msg.type == "write_begin":
                state["fail_next_chunk"] = True
            return await real_dispatch(msg)

        monkeypatch.setattr(adapter, "_dispatch", flaky)
        with pytest.raises(RuntimeError, match="write_chunk failed"):
            await stream_write_file(
                sender, "worker-1", "recipe://flaky", "f.bin", b"some-bytes"
            )
        assert "write_abort" in sender.calls
        assert adapter._transfers == {}
