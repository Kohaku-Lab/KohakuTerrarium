"""Unit tests for :mod:`kohakuterrarium.session.vault.handles`."""

import os
import threading

import numpy as np
import pytest
from kohakuvault import KVault, TextVault, VectorKVault

from kohakuterrarium.session.vault.handles import dispose, is_disposed


def _assert_file_released(path) -> None:
    moved = path.with_name(path.name + ".moved")
    os.replace(path, moved)
    os.replace(moved, path)


class TestDispose:
    def test_kvault_close_releases_file(self, tmp_path):
        path = tmp_path / "kv.db"
        vault = KVault(str(path), table="t")
        vault["k"] = b"v"
        assert not is_disposed(vault)
        assert dispose(vault) is True
        assert is_disposed(vault)
        _assert_file_released(path)
        dispose(vault)

    def test_kvault_with_live_flush_worker_reports_unreleased(self, tmp_path):
        vault = KVault(str(tmp_path / "kv.db"), table="t")
        stop = threading.Event()
        worker = threading.Thread(target=stop.wait, daemon=True)
        worker.start()
        vault._daemon_thread = worker
        try:
            assert dispose(vault) is False
            assert is_disposed(vault)
        finally:
            stop.set()
            worker.join()

    def test_textvault_drops_native_handle(self, tmp_path):
        path = tmp_path / "fts.db"
        fts = TextVault(str(path), table="fts")
        fts.insert("searchable text", {"k": 1})
        assert not is_disposed(fts)
        assert dispose(fts) is True
        assert is_disposed(fts)
        _assert_file_released(path)
        assert dispose(fts) is True

    def test_vector_vault_close_releases_file(self, tmp_path):
        path = tmp_path / "vec.db"
        vectors = VectorKVault(str(path), table="v", dimensions=4)
        vectors.insert(np.ones(4, dtype=np.float32), b"row")
        assert not is_disposed(vectors)
        assert dispose(vectors) is True
        assert is_disposed(vectors)
        _assert_file_released(path)

    def test_other_handles_use_close_and_closed_flag(self):
        class Handle:
            _closed = False

            def close(self):
                self._closed = True

        handle = Handle()
        assert not is_disposed(handle)
        assert dispose(handle) is True
        assert is_disposed(handle)
        assert not is_disposed(object())

    def test_close_failure_propagates(self):
        class Broken:
            def close(self):
                raise OSError("release failed")

        with pytest.raises(OSError, match="release failed"):
            dispose(Broken())
