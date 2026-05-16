"""Unit tests for :mod:`kohakuterrarium.session.version`."""

import pytest

from kohakuterrarium.session.version import FORMAT_VERSION, detect_format_version


class TestFormatVersionConstant:
    def test_current_version_is_two(self):
        # The framework's current on-disk format is v2 (Wave D migration
        # framework). detect_format_version of an unmarked store returns
        # 1, but freshly-created stores stamp this constant.
        assert FORMAT_VERSION == 2


class TestDetectFormatVersion:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            detect_format_version(tmp_path / "nope.kohakutr")

    def test_v1_default_when_no_meta_key(self, tmp_path):
        """A fresh store with no ``format_version`` key in meta returns 1."""
        from kohakuvault import KVault

        path = tmp_path / "s.kohakutr"
        # Create an empty meta vault.
        meta = KVault(str(path), table="meta")
        try:
            meta.enable_auto_pack()
        finally:
            meta.close()
        assert detect_format_version(path) == 1

    def test_v2_detected(self, tmp_path):
        from kohakuvault import KVault

        path = tmp_path / "s.kohakutr.v2"
        meta = KVault(str(path), table="meta")
        try:
            meta.enable_auto_pack()
            meta["format_version"] = 2
        finally:
            meta.close()
        assert detect_format_version(path) == 2

    def test_string_format_version_coerced(self, tmp_path):
        from kohakuvault import KVault

        path = tmp_path / "s.kohakutr.v2"
        meta = KVault(str(path), table="meta")
        try:
            meta.enable_auto_pack()
            meta["format_version"] = "3"
        finally:
            meta.close()
        assert detect_format_version(path) == 3

    def test_garbage_format_version_falls_back_to_1(self, tmp_path):
        from kohakuvault import KVault

        path = tmp_path / "s.kohakutr.v2"
        meta = KVault(str(path), table="meta")
        try:
            meta.enable_auto_pack()
            meta["format_version"] = "not-a-num"
        finally:
            meta.close()
        assert detect_format_version(path) == 1

    def test_corrupt_file_falls_back_to_1(self, tmp_path):
        # The path exists but isn't a valid KVault DB — opening it
        # raises, and the helper defensively reports v1 rather than
        # propagating the error.
        path = tmp_path / "garbage.kohakutr"
        path.write_bytes(b"this is not a sqlite database at all")
        assert detect_format_version(path) == 1

    def test_close_failure_is_swallowed(self, tmp_path, monkeypatch):
        # If closing the probed meta vault raises, the helper logs and
        # swallows it — the detected version still comes back correctly.
        from kohakuvault import KVault

        path = tmp_path / "s.kohakutr.v2"
        meta = KVault(str(path), table="meta")
        try:
            meta.enable_auto_pack()
            meta["format_version"] = 2
        finally:
            meta.close()

        import kohakuterrarium.session.version as version_mod

        real_kvault = version_mod.KVault

        class _CloseRaisesKVault:
            def __init__(self, *a, **kw):
                self._inner = real_kvault(*a, **kw)

            def enable_auto_pack(self):
                return self._inner.enable_auto_pack()

            def __getitem__(self, key):
                return self._inner[key]

            def close(self):
                # Close the real handle, then raise to exercise the
                # defensive ``except`` around ``meta.close()``.
                self._inner.close()
                raise RuntimeError("close exploded")

        monkeypatch.setattr(version_mod, "KVault", _CloseRaisesKVault)
        # The version is still detected; the close failure is swallowed.
        assert detect_format_version(path) == 2
