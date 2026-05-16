"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters.terrarium_files`."""

import asyncio
import base64
import hashlib

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters import terrarium_files as mod
from kohakuterrarium.laboratory.adapters.terrarium_files import (
    MAX_ONESHOT_BYTES,
    TerrariumFilesAdapter,
    _b64encode,
    _decode_wire_bytes,
    _hash_bytes,
    _hash_file,
    _list_sync,
    _unpack_bundle_entry,
)
from kohakuterrarium.laboratory.adapters.file_scopes import ScopeError


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _msg(type_, body=None) -> AppMessage:
    return AppMessage(
        namespace=TerrariumFilesAdapter.NAMESPACE,
        type=type_,
        body=body or {},
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


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    node = _FakeNode()
    return TerrariumFilesAdapter(_FakeEngine(), node)


# ── Helpers ──────────────────────────────────────────────────────


class TestHelpers:
    def test_hash_bytes(self):
        assert _hash_bytes(b"hello") == _sha(b"hello")

    def test_hash_bytes_bytearray(self):
        assert _hash_bytes(bytearray(b"hello")) == _sha(b"hello")

    def test_hash_file(self, tmp_path):
        f = tmp_path / "x.bin"
        f.write_bytes(b"hello")
        assert _hash_file(f) == _sha(b"hello")

    def test_b64encode(self):
        assert _b64encode(b"hi") == "aGk="

    def test_decode_wire_bytes_b64(self):
        assert _decode_wire_bytes({"x": "aGk="}, "x") == b"hi"

    def test_decode_wire_bytes_raw(self):
        assert _decode_wire_bytes({"x": b"hi"}, "x") == b"hi"

    def test_decode_wire_bytes_missing(self):
        with pytest.raises(ScopeError, match="missing required field"):
            _decode_wire_bytes({}, "x")

    def test_decode_wire_bytes_invalid_b64(self):
        with pytest.raises(ScopeError, match="not valid base64"):
            _decode_wire_bytes({"x": "@@@"}, "x")

    def test_decode_wire_bytes_wrong_type(self):
        with pytest.raises(ScopeError, match="must be"):
            _decode_wire_bytes({"x": 42}, "x")


class TestUnpackBundleEntry:
    def test_basic(self):
        blob = b"hello"
        h = _sha(blob)
        out_hash, out_blob = _unpack_bundle_entry([h, _b64encode(blob)])
        assert out_hash == h
        assert out_blob == blob

    def test_wrong_shape(self):
        with pytest.raises(ScopeError, match="must be"):
            _unpack_bundle_entry([1, 2, 3])

    def test_non_str_hash(self):
        with pytest.raises(ScopeError, match="sha256 must be"):
            _unpack_bundle_entry([1, "aGk="])

    def test_non_str_blob(self):
        with pytest.raises(ScopeError, match="payload must be"):
            _unpack_bundle_entry(["h", 1])

    def test_invalid_b64(self):
        with pytest.raises(ScopeError, match="not valid base64"):
            _unpack_bundle_entry(["h", "@@@"])


# ── _list_sync ───────────────────────────────────────────────────


class TestListSync:
    def test_basic(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("y")
        out = _list_sync(tmp_path, "scope://x", "", recursive=False)
        names = sorted(e["name"] for e in out["entries"])
        assert "a.txt" in names
        assert "sub" in names

    def test_recursive(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("y")
        out = _list_sync(tmp_path, "scope://x", "", recursive=True)
        names = sorted(e["name"] for e in out["entries"])
        assert "sub/b.txt" in names

    def test_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _list_sync(tmp_path / "nope", "s://x", "missing", recursive=False)

    def test_not_a_dir(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        with pytest.raises(ScopeError, match="not a directory"):
            _list_sync(f, "s://x", "x.txt", recursive=False)


# ── adapter init / detach ───────────────────────────────────────


class TestConstruction:
    def test_init_and_detach(self, adapter):
        assert TerrariumFilesAdapter.NAMESPACE in adapter._node.registered
        adapter.detach()
        assert TerrariumFilesAdapter.NAMESPACE in adapter._node.unregistered


# ── error mapping ────────────────────────────────────────────────


class TestErrorMapping:
    async def test_unknown_type(self, adapter):
        out = await adapter._dispatch(_msg("mystery"))
        assert out["error"]["kind"] == "unknown_type"

    async def test_scope_error_to_invalid(self, adapter):
        out = await adapter._dispatch(_msg("read", {"scope": "noslash"}))
        assert out["error"]["kind"] == "invalid"

    async def test_file_not_found(self, adapter):
        out = await adapter._dispatch(
            _msg("read", {"scope": "config://", "path": "nope.txt"})
        )
        assert out["error"]["kind"] == "not_found"


# ── stat / read / write / delete round-trip ────────────────────


class TestStatReadWriteDelete:
    async def test_write_then_read(self, adapter):
        body = {
            "scope": "recipe://test1",
            "path": "f.txt",
            "bytes_b64": _b64encode(b"hello"),
        }
        write_out = await adapter._dispatch(_msg("write", body))
        assert write_out["written"] == 5
        assert write_out["sha256"] == _sha(b"hello")

        read_out = await adapter._dispatch(
            _msg("read", {"scope": "recipe://test1", "path": "f.txt"})
        )
        assert read_out["sha256"] == _sha(b"hello")
        decoded = base64.b64decode(read_out["bytes_b64"])
        assert decoded == b"hello"

    async def test_stat_existing_file(self, adapter):
        await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://stat-test",
                    "path": "x.txt",
                    "bytes_b64": _b64encode(b"hi"),
                },
            )
        )
        out = await adapter._dispatch(
            _msg("stat", {"scope": "recipe://stat-test", "path": "x.txt"})
        )
        assert out["stat"]["size"] == 2
        assert out["stat"]["is_dir"] is False
        assert "sha256" in out["stat"]

    async def test_stat_missing(self, adapter):
        out = await adapter._dispatch(
            _msg("stat", {"scope": "recipe://stat-missing", "path": "x.txt"})
        )
        assert out["error"]["kind"] == "not_found"

    async def test_write_oversize_rejected(self, adapter):
        big = b"x" * (MAX_ONESHOT_BYTES + 1)
        out = await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://oversize",
                    "path": "f.bin",
                    "bytes_b64": _b64encode(big),
                },
            )
        )
        assert out["error"]["kind"] == "invalid"

    async def test_write_expect_hash_mismatch(self, adapter):
        # First write.
        await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://eh",
                    "path": "f.txt",
                    "bytes_b64": _b64encode(b"old"),
                },
            )
        )
        # Second write with wrong expect_hash.
        out = await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://eh",
                    "path": "f.txt",
                    "bytes_b64": _b64encode(b"new"),
                    "expect_hash": "bogus",
                },
            )
        )
        assert out["error"]["kind"] == "invalid"

    async def test_read_on_directory_fails(self, adapter):
        await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://rd",
                    "path": "sub/f.txt",
                    "bytes_b64": _b64encode(b"x"),
                },
            )
        )
        # Read the directory itself.
        out = await adapter._dispatch(
            _msg("read", {"scope": "recipe://rd", "path": "sub"})
        )
        assert out["error"]["kind"] == "invalid"

    async def test_delete_file(self, adapter):
        await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://del",
                    "path": "f.txt",
                    "bytes_b64": _b64encode(b"x"),
                },
            )
        )
        out = await adapter._dispatch(
            _msg("delete", {"scope": "recipe://del", "path": "f.txt"})
        )
        assert out == {}

    async def test_delete_root_refused(self, adapter):
        out = await adapter._dispatch(_msg("delete", {"scope": "recipe://anywhere"}))
        assert out["error"]["kind"] == "invalid"

    async def test_delete_missing(self, adapter):
        out = await adapter._dispatch(
            _msg("delete", {"scope": "recipe://nox", "path": "missing.txt"})
        )
        assert out["error"]["kind"] == "not_found"

    async def test_delete_directory(self, adapter):
        # Create a subdir with a file.
        await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://dir-del",
                    "path": "sub/f.txt",
                    "bytes_b64": _b64encode(b"x"),
                },
            )
        )
        out = await adapter._dispatch(
            _msg("delete", {"scope": "recipe://dir-del", "path": "sub"})
        )
        assert out == {}


# ── list ─────────────────────────────────────────────────────────


class TestListOp:
    async def test_basic(self, adapter):
        await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://list-test",
                    "path": "f.txt",
                    "bytes_b64": _b64encode(b"x"),
                },
            )
        )
        out = await adapter._dispatch(_msg("list", {"scope": "recipe://list-test"}))
        names = [e["name"] for e in out["entries"]]
        assert "f.txt" in names


# ── push_bundle ──────────────────────────────────────────────────


class TestPushBundle:
    async def test_basic_deploy(self, adapter):
        files = {
            "a.txt": [_sha(b"hi"), _b64encode(b"hi")],
            "sub/b.txt": [_sha(b"there"), _b64encode(b"there")],
        }
        out = await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://pb", "files": files})
        )
        deployed = sorted(out["deployed"])
        assert deployed == ["a.txt", "sub/b.txt"]
        assert out["conflicts"] == []

    async def test_idempotent_skip(self, adapter):
        files = {"a.txt": [_sha(b"hi"), _b64encode(b"hi")]}
        await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://idem", "files": files})
        )
        # Second push with same content: idempotent skip.
        out = await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://idem", "files": files})
        )
        assert out["deployed"] == []
        assert out["conflicts"] == []

    async def test_conflict_detected(self, adapter):
        files1 = {"a.txt": [_sha(b"old"), _b64encode(b"old")]}
        await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://cnf", "files": files1})
        )
        # Push different content under the same name.
        files2 = {"a.txt": [_sha(b"new"), _b64encode(b"new")]}
        out = await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://cnf", "files": files2})
        )
        assert out["deployed"] == []
        assert out["conflicts"] == ["a.txt"]

    async def test_hash_mismatch_rejected(self, adapter):
        # sha256 of "real" doesn't match "fake".
        files = {"a.txt": [_sha(b"different"), _b64encode(b"real")]}
        out = await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://bad", "files": files})
        )
        assert out["error"]["kind"] == "invalid"

    async def test_non_dict_files(self, adapter):
        out = await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://nd", "files": []})
        )
        assert out["error"]["kind"] == "invalid"


# ── error-mapping branches ───────────────────────────────────────


class TestDispatchErrorTranslation:
    async def test_missing_scope_key_is_not_found(self, adapter):
        # ``_handle`` reads ``body["scope"]`` directly — a missing key
        # is a KeyError, which the dispatcher maps to ``not_found``.
        out = await adapter._dispatch(_msg("read", {"path": "x"}))
        assert out["error"]["kind"] == "not_found"

    async def test_value_error_maps_to_invalid(self, adapter, monkeypatch):
        # A ValueError raised inside an op is translated to ``invalid``.
        async def _boom(body):
            raise ValueError("malformed request")

        monkeypatch.setattr(adapter, "_op_stat", _boom)
        out = await adapter._dispatch(_msg("stat", {"scope": "config://", "path": "x"}))
        assert out["error"]["kind"] == "invalid"
        assert "malformed request" in out["error"]["message"]

    async def test_permission_error_maps_to_denied(self, adapter, monkeypatch):
        # A PermissionError surfaces as the ``denied`` error kind so the
        # controller can distinguish it from a plain failure.
        async def _boom(body):
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(adapter, "_op_write", _boom)
        out = await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://perm",
                    "path": "f.txt",
                    "bytes_b64": _b64encode(b"x"),
                },
            )
        )
        assert out["error"]["kind"] == "denied"
        assert "read-only filesystem" in out["error"]["message"]


# ── read one-shot size limit ─────────────────────────────────────


class TestReadSizeLimit:
    async def test_read_oversize_file_rejected(self, adapter, monkeypatch):
        # A file larger than the one-shot limit is refused with a
        # ScopeError → ``invalid`` (chunked reads aren't implemented).
        await adapter._dispatch(
            _msg(
                "write",
                {
                    "scope": "recipe://big-read",
                    "path": "f.bin",
                    "bytes_b64": _b64encode(b"small"),
                },
            )
        )
        # Pretend the on-disk file is huge by faking its stat size.
        real_to_thread = asyncio.to_thread

        async def _fake_to_thread(fn, *a, **k):
            result = await real_to_thread(fn, *a, **k)
            if hasattr(result, "st_size"):

                class _St:
                    st_size = MAX_ONESHOT_BYTES + 1
                    st_mtime = 0.0

                return _St()
            return result

        monkeypatch.setattr(mod.asyncio, "to_thread", _fake_to_thread)
        out = await adapter._dispatch(
            _msg("read", {"scope": "recipe://big-read", "path": "f.bin"})
        )
        assert out["error"]["kind"] == "invalid"
        assert "one-shot limit" in out["error"]["message"]


# ── push_bundle partial-commit failure ───────────────────────────


class TestPushBundlePartialFailure:
    async def test_os_replace_failure_reports_partial(self, adapter, monkeypatch):
        # If ``os.replace`` fails partway through the commit loop, the
        # op must report a structured partial result — deployed-so-far,
        # the remaining files, and the commit error — rather than
        # claiming a clean deploy.
        files = {
            "a.txt": [_sha(b"aaa"), _b64encode(b"aaa")],
            "b.txt": [_sha(b"bbb"), _b64encode(b"bbb")],
        }
        import os as _os

        real_replace = _os.replace
        calls = {"n": 0}

        def _flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return real_replace(src, dst)

        monkeypatch.setattr(_os, "replace", _flaky_replace)
        out = await adapter._dispatch(
            _msg("push_bundle", {"scope": "recipe://partial", "files": files})
        )
        # One file committed, one left, partial flag + error set.
        assert out["partial"] is True
        assert len(out["deployed"]) == 1
        assert len(out["remaining"]) == 1
        assert "disk full" in out["error"]
        assert out["conflicts"] == []
