"""Adapter-edge coverage for Laboratory exact-path file mutations.

Drives :class:`TerrariumFilesAdapter` through production dispatch with a real
open :class:`SessionStore` borrowed onto the engine, plus existing helpers.
Lines that remain unreachable without a concurrent hash-race or a production
bug (staged-hash re-verify after write) are named in the closest test
docstring rather than mocked into existence.
"""

from pathlib import Path

import pytest

from kohakuterrarium.laboratory.adapters.terrarium import file_mutations as mut
from kohakuterrarium.laboratory.adapters.terrarium import files as files_mod
from kohakuterrarium.laboratory.adapters.file_scopes import resolve_in_scope
from kohakuterrarium.laboratory.adapters.terrarium.files import MAX_ONESHOT_BYTES
from kohakuterrarium.session.occupancy import family
from kohakuterrarium.session.store import SessionStore
from tests.unit.laboratory.file_mutation_enrollment_helpers import (
    assert_idle,
    b64,
    make_files_adapter,
    msg,
    sha,
)


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    return make_files_adapter(monkeypatch, tmp_path)


def _open_store(path: Path) -> SessionStore:
    store = SessionStore(str(path), writer_lock=True)
    store.init_meta("sid", "agent", "/p", "/w", ["alice"])
    store.flush()
    return store


async def _begin_chunk(adapter, *, scope: str, path: str, payload: bytes, **extra):
    begin = await adapter._dispatch(
        msg(
            "write_begin",
            {
                "scope": scope,
                "path": path,
                "total_size": len(payload),
                "sha256": sha(payload),
                **extra,
            },
        )
    )
    assert "transfer_id" in begin, begin
    tid = begin["transfer_id"]
    chunk = await adapter._dispatch(
        msg(
            "write_chunk",
            {"transfer_id": tid, "seq": 0, "bytes_b64": b64(payload)},
        )
    )
    assert chunk == {"received": len(payload)}
    return tid


class TestActiveStoreGuard:
    async def test_open_store_blocks_write_and_leaves_bytes(self, adapter, tmp_path):
        store_path = tmp_path / "kt-config" / "resume" / "live.kohakutr"
        store_path.parent.mkdir(parents=True)
        store = _open_store(store_path)
        try:
            adapter._engine._session_stores["sid"] = store
            original = store_path.read_bytes()
            denied = await adapter._dispatch(
                msg(
                    "write",
                    {
                        "scope": "config://",
                        "path": "resume/live.kohakutr",
                        "bytes_b64": b64(b"mutated"),
                    },
                )
            )
            assert denied["error"]["kind"] == "invalid"
            assert "active session store" in denied["error"]["message"]
            assert store_path.read_bytes() == original
            assert store.is_ready
        finally:
            store.close()
        assert_idle(store_path)

    async def test_open_store_blocks_delete_of_parent_directory(
        self, adapter, tmp_path
    ):
        store_path = tmp_path / "kt-config" / "resume" / "nested" / "live.kohakutr"
        store_path.parent.mkdir(parents=True)
        store = _open_store(store_path)
        try:
            adapter._engine._session_stores["sid"] = store
            denied = await adapter._dispatch(
                msg("delete", {"scope": "config://", "path": "resume/nested"})
            )
            assert denied["error"]["kind"] == "invalid"
            assert "active session store" in denied["error"]["message"]
            assert store_path.exists()
        finally:
            store.close()
        assert_idle(store_path)

    async def test_closed_store_still_guarded_while_borrowed(self, adapter, tmp_path):
        """Guard keys on the borrowed engine mapping, not the open handle.

        Closing the store releases native occupancy; the adapter still refuses
        mutation while the engine continues to list the path as an active store.
        """
        store_path = tmp_path / "kt-config" / "resume" / "closed.kohakutr"
        store_path.parent.mkdir(parents=True)
        store = _open_store(store_path)
        adapter._engine._session_stores["sid"] = store
        store.close()
        denied = await adapter._dispatch(
            msg(
                "write",
                {
                    "scope": "config://",
                    "path": "resume/closed.kohakutr",
                    "bytes_b64": b64(b"after-close"),
                },
            )
        )
        assert denied["error"]["kind"] == "invalid"
        assert "active session store" in denied["error"]["message"]
        assert store_path.exists()
        assert_idle(store_path)

    async def test_unrelated_file_is_writable_beside_open_store(
        self, adapter, tmp_path
    ):
        live = tmp_path / "kt-config" / "resume" / "live.kohakutr"
        live.parent.mkdir(parents=True)
        store = _open_store(live)
        try:
            adapter._engine._session_stores["sid"] = store
            sibling = tmp_path / "kt-config" / "resume" / "notes.txt"
            sibling.write_bytes(b"old")
            out = await adapter._dispatch(
                msg(
                    "write",
                    {
                        "scope": "config://",
                        "path": "resume/notes.txt",
                        "bytes_b64": b64(b"next"),
                    },
                )
            )
            assert out == {"written": 4, "sha256": sha(b"next")}
            assert sibling.read_bytes() == b"next"
            assert live.exists()
            assert_idle(sibling)
        finally:
            store.close()
        assert_idle(live)

    async def test_commit_discards_when_store_is_attached_after_begin(
        self, adapter, tmp_path
    ):
        payload = b"staged-while-idle"
        target = tmp_path / "kt-config" / "resume" / "later.kohakutr"
        target.parent.mkdir(parents=True)
        tid = await _begin_chunk(
            adapter, scope="config://", path="resume/later.kohakutr", payload=payload
        )
        staging = adapter._transfers[tid].staging
        store = _open_store(target)
        try:
            adapter._engine._session_stores["sid"] = store
            commit = await adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
            assert commit["error"]["kind"] == "invalid"
            assert "active session store" in commit["error"]["message"]
            assert tid not in adapter._transfers
            assert not staging.exists()
            assert store.meta["session_id"] == "sid"
            assert target.read_bytes() != payload
        finally:
            store.close()
        assert_idle(target)


class TestCommitHashAndIdempotence:
    async def test_expect_hash_conflict_discards_staging(self, adapter, tmp_path):
        payload = b"replacement"
        target_dir = tmp_path / "kt-config" / "recipes" / "conflict"
        target_dir.mkdir(parents=True)
        target = target_dir / "sess.kohakutr"
        target.write_bytes(b"on-disk")
        tid = await _begin_chunk(
            adapter,
            scope="recipe://conflict",
            path="sess.kohakutr",
            payload=payload,
            expect_hash="not-the-on-disk-digest",
        )
        staging = adapter._transfers[tid].staging
        commit = await adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
        assert commit["error"]["kind"] == "invalid"
        assert "expect_hash mismatch" in commit["error"]["message"]
        assert tid not in adapter._transfers
        assert not staging.exists()
        assert target.read_bytes() == b"on-disk"
        assert_idle(target)

    async def test_idempotent_commit_skips_replace(
        self, adapter, monkeypatch, tmp_path
    ):
        payload = b"already-there"
        target_dir = tmp_path / "kt-config" / "recipes" / "same"
        target_dir.mkdir(parents=True)
        target = target_dir / "sess.kohakutr"
        target.write_bytes(payload)
        tid = await _begin_chunk(
            adapter, scope="recipe://same", path="sess.kohakutr", payload=payload
        )
        staging = adapter._transfers[tid].staging
        enrolled: list[str] = []
        real_acquire = family.acquire_legacy

        def spy_acquire(path, operation):
            enrolled.append(operation)
            return real_acquire(path, operation)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)
        commit = await adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
        assert commit == {"written": len(payload), "sha256": sha(payload)}
        assert enrolled == []
        assert tid not in adapter._transfers
        assert not staging.exists()
        assert target.read_bytes() == payload
        assert_idle(target)

    async def test_permissionerror_on_missing_target_is_denied(
        self, adapter, monkeypatch
    ):
        payload = b"fresh"
        tid = await _begin_chunk(
            adapter, scope="recipe://missing", path="sess.kohakutr", payload=payload
        )
        transfer = adapter._transfers[tid]
        staging = transfer.staging
        assert not transfer.target.exists()

        def boom_replace(*args, **kwargs):
            raise PermissionError(13, "cannot create target")

        monkeypatch.setattr(mut.os, "replace", boom_replace)
        commit = await adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
        assert commit["error"]["kind"] == "denied"
        assert "cannot create target" in commit["error"]["message"]
        assert tid in adapter._transfers
        assert staging.exists()
        assert not transfer.target.exists()
        await adapter._dispatch(msg("write_abort", {"transfer_id": tid}))
        assert tid not in adapter._transfers
        assert not staging.exists()
        assert_idle(transfer.target)

    async def test_commit_hash_oserror_falls_through_to_replace(
        self, adapter, monkeypatch, tmp_path
    ):
        payload = b"retry-hash"
        target_dir = tmp_path / "kt-config" / "recipes" / "hasherr"
        target_dir.mkdir(parents=True)
        target = target_dir / "sess.kohakutr"
        target.write_bytes(payload)
        tid = await _begin_chunk(
            adapter, scope="recipe://hasherr", path="sess.kohakutr", payload=payload
        )
        real_hash = files_mod._hash_file_async

        async def fail_once(path):
            if path == target:
                raise OSError("temporarily unreadable")
            return await real_hash(path)

        monkeypatch.setattr(files_mod, "_hash_file_async", fail_once)
        commit = await adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
        assert commit == {"written": len(payload), "sha256": sha(payload)}
        assert target.read_bytes() == payload
        assert tid not in adapter._transfers
        assert_idle(target)


class TestWriteBeginAndAbort:
    async def test_write_begin_rejects_non_int_total_size(self, adapter):
        out = await adapter._dispatch(
            msg(
                "write_begin",
                {
                    "scope": "recipe://begin",
                    "path": "f.bin",
                    "total_size": "8",
                },
            )
        )
        assert out["error"]["kind"] == "invalid"
        assert "non-negative int total_size" in out["error"]["message"]
        assert adapter._transfers == {}

    async def test_write_abort_unknown_id_is_noop(self, adapter):
        out = await adapter._dispatch(msg("write_abort", {"transfer_id": "ghost"}))
        assert out == {}
        out2 = await adapter._dispatch(msg("write_abort", {"transfer_id": 12}))
        assert out2 == {}


class TestMalformedBase64AndOneShot:
    async def test_malformed_base64_write_rejected(self, adapter, tmp_path):
        out = await adapter._dispatch(
            msg(
                "write",
                {
                    "scope": "recipe://b64",
                    "path": "f.txt",
                    "bytes_b64": "@@@",
                },
            )
        )
        assert out["error"]["kind"] == "invalid"
        assert "not valid base64" in out["error"]["message"]
        target = tmp_path / "kt-config" / "recipes" / "b64" / "f.txt"
        assert not target.exists()

    async def test_malformed_base64_chunk_leaves_transfer(self, adapter):
        begin = await adapter._dispatch(
            msg(
                "write_begin",
                {
                    "scope": "recipe://chunk-b64",
                    "path": "f.bin",
                    "total_size": 4,
                },
            )
        )
        tid = begin["transfer_id"]
        staging = adapter._transfers[tid].staging
        out = await adapter._dispatch(
            msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": "not-base64!!"},
            )
        )
        assert out["error"]["kind"] == "invalid"
        assert "not valid base64" in out["error"]["message"]
        assert tid in adapter._transfers
        assert staging.exists()
        await adapter._dispatch(msg("write_abort", {"transfer_id": tid}))
        assert not staging.exists()

    async def test_oneshot_read_limit_uses_real_oversize_file(
        self, adapter, tmp_path, monkeypatch
    ):
        """Reject a real oversized file before opening its contents."""
        target = tmp_path / "kt-config" / "recipes" / "oneshot" / "f.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            stream.write(b"small")
            stream.truncate(MAX_ONESHOT_BYTES + 1)
        assert target.is_file()

        def forbidden_open(*args, **kwargs):
            pytest.fail("oversized content must not be opened")

        monkeypatch.setattr(files_mod.aiofiles, "open", forbidden_open)
        out = await adapter._dispatch(
            msg("read", {"scope": "recipe://oneshot", "path": "f.bin"})
        )
        assert out["error"]["kind"] == "invalid"
        assert "one-shot limit" in out["error"]["message"]
        assert target.stat().st_size == MAX_ONESHOT_BYTES + 1
        with target.open("rb") as stream:
            assert stream.read(5) == b"small"


class TestListAndBundleEdges:
    async def test_list_skips_entry_whose_stat_raises(
        self, adapter, monkeypatch, tmp_path
    ):
        await adapter._dispatch(
            msg(
                "write",
                {
                    "scope": "recipe://list-stat",
                    "path": "good.txt",
                    "bytes_b64": b64(b"ok"),
                },
            )
        )
        root = resolve_in_scope("recipe://list-stat", "", adapter._engine)
        bad = root / "ghost.txt"
        bad.write_bytes(b"gone")
        real_stat = Path.stat

        def flaky_stat(self, *args, **kwargs):
            if self == bad:
                raise OSError("vanished between iterdir and stat")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)
        out = await adapter._dispatch(msg("list", {"scope": "recipe://list-stat"}))
        names = [entry["name"] for entry in out["entries"]]
        assert "good.txt" in names
        assert "ghost.txt" not in names

    async def test_bundle_malformed_base64_rejected(self, adapter):
        out = await adapter._dispatch(
            msg(
                "push_bundle",
                {
                    "scope": "recipe://bundle-b64",
                    "files": {"a.txt": [sha(b"hi"), "@@@"]},
                },
            )
        )
        assert out["error"]["kind"] == "invalid"
        assert "not valid base64" in out["error"]["message"]
