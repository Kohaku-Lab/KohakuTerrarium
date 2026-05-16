"""Unit tests for :mod:`kohakuterrarium.session.migrations`."""

from pathlib import Path

import pytest

from kohakuterrarium.session import migrations as mig_mod
from kohakuterrarium.session.migrations import (
    MAX_SUPPORTED_VERSION,
    MIGRATORS,
    _chain,
    _strip_version_suffix,
    _version_from_suffix,
    discover_versions,
    ensure_latest_version,
    migrate,
    migration_marker,
    path_for_version,
)

# ── path helpers ──────────────────────────────────────────────────


class TestStripVersionSuffix:
    def test_with_suffix(self):
        assert _strip_version_suffix(Path("a.kohakutr.v2")) == Path("a.kohakutr")

    def test_no_suffix(self):
        assert _strip_version_suffix(Path("a.kohakutr")) == Path("a.kohakutr")


class TestVersionFromSuffix:
    def test_parses(self):
        assert _version_from_suffix(Path("a.kohakutr.v3")) == 3

    def test_no_suffix(self):
        assert _version_from_suffix(Path("a.kohakutr")) is None

    def test_non_numeric_suffix_returns_none(self):
        # The regex matches ``.v<digits>`` only — a non-matching suffix
        # like ``.vX`` simply yields no match → None.
        assert _version_from_suffix(Path("a.kohakutr.vX")) is None


class TestPathForVersion:
    def test_v1_is_bare(self):
        assert path_for_version("a.kohakutr", 1) == Path("a.kohakutr")

    def test_v2_uses_suffix(self):
        assert path_for_version("a.kohakutr", 2) == Path("a.kohakutr.v2")

    def test_v3_uses_suffix(self):
        assert path_for_version("a.kohakutr", 3) == Path("a.kohakutr.v3")

    def test_strips_existing_suffix_first(self):
        # ``a.kohakutr.v2`` → strip → ``a.kohakutr`` → re-suffix with target
        assert path_for_version("a.kohakutr.v2", 3) == Path("a.kohakutr.v3")

    def test_with_pathlib_input(self):
        assert path_for_version(Path("a.kohakutr"), 2) == Path("a.kohakutr.v2")


# ── _chain ────────────────────────────────────────────────────────


class TestChain:
    def test_simple_one_step(self):
        # Registered (1, 2).
        chain = _chain(1, 2)
        assert chain == [(1, 2)]

    def test_same_version_no_chain(self):
        assert _chain(2, 2) == []

    def test_unreachable_raises(self):
        with pytest.raises(ValueError, match="No migrator"):
            _chain(99, 100)


# ── discover_versions ─────────────────────────────────────────────


class TestDiscoverVersions:
    def test_no_files(self, tmp_path):
        assert discover_versions(tmp_path / "no-such") == []

    def test_returns_only_existing(self, tmp_path):
        bare = tmp_path / "alice.kohakutr"
        bare.write_bytes(b"")
        v2 = tmp_path / "alice.kohakutr.v2"
        v2.write_bytes(b"")
        # The bare file isn't a real KVault — detect_format_version
        # returns 1 on failure. The v2 file has its version in the
        # suffix.
        versions = discover_versions(bare)
        assert {v for v, _ in versions} == {1, 2}

    def test_descending_order(self, tmp_path):
        bare = tmp_path / "alice.kohakutr"
        bare.write_bytes(b"")
        v2 = tmp_path / "alice.kohakutr.v2"
        v2.write_bytes(b"")
        v3 = tmp_path / "alice.kohakutr.v3"
        v3.write_bytes(b"")
        versions = [v for v, _ in discover_versions(bare)]
        assert versions == sorted(versions, reverse=True)

    def test_bare_probe_failure_defaults_to_v1(self, tmp_path, monkeypatch):
        # If detect_format_version raises while probing the bare file,
        # discover_versions defensively records it as v1.
        bare = tmp_path / "alice.kohakutr"
        bare.write_bytes(b"")

        def _boom(p):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(mig_mod, "detect_format_version", _boom)
        versions = dict(discover_versions(bare))
        assert versions.get(1) == bare


# ── migrate ───────────────────────────────────────────────────────


class TestMigrateBasic:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            migrate(tmp_path / "no.kohakutr", 2)

    def test_already_at_target(self, tmp_path, monkeypatch):
        path = tmp_path / "a.kohakutr.v2"
        path.write_bytes(b"")
        monkeypatch.setattr(mig_mod, "detect_format_version", lambda p: 2)
        out = migrate(path, 2)
        assert out == path

    def test_above_target_also_returns_src(self, tmp_path, monkeypatch):
        path = tmp_path / "a.kohakutr.v5"
        path.write_bytes(b"")
        monkeypatch.setattr(mig_mod, "detect_format_version", lambda p: 5)
        out = migrate(path, 2)
        assert out == path

    def test_destination_exists_is_reused(self, tmp_path, monkeypatch):
        src = tmp_path / "a.kohakutr"
        src.write_bytes(b"")
        existing = tmp_path / "a.kohakutr.v2"
        existing.write_bytes(b"existing")
        monkeypatch.setattr(mig_mod, "detect_format_version", lambda p: 1)
        called = []

        def fake_migrator(s, d):
            called.append((s, d))

        monkeypatch.setitem(MIGRATORS, (1, 2), fake_migrator)
        out = migrate(src, 2)
        assert out == existing
        # Migrator was NOT called because target existed.
        assert called == []

    def test_runs_migrator_when_target_missing(self, tmp_path, monkeypatch):
        src = tmp_path / "a.kohakutr"
        src.write_bytes(b"")
        monkeypatch.setattr(mig_mod, "detect_format_version", lambda p: 1)
        called = []

        def fake_migrator(s, d):
            called.append((s, d))
            Path(d).write_bytes(b"migrated")

        monkeypatch.setitem(MIGRATORS, (1, 2), fake_migrator)
        out = migrate(src, 2)
        assert out == tmp_path / "a.kohakutr.v2"
        assert len(called) == 1

    def test_migrator_failure_cleans_partial(self, tmp_path, monkeypatch):
        src = tmp_path / "a.kohakutr"
        src.write_bytes(b"")
        monkeypatch.setattr(mig_mod, "detect_format_version", lambda p: 1)

        def bad_migrator(s, d):
            Path(d).write_bytes(b"partial")
            raise RuntimeError("migrator boom")

        monkeypatch.setitem(MIGRATORS, (1, 2), bad_migrator)
        with pytest.raises(RuntimeError, match="migration v1.*failed"):
            migrate(src, 2)
        # Partial output cleaned up.
        assert not (tmp_path / "a.kohakutr.v2").exists()


# ── ensure_latest_version ─────────────────────────────────────────


class TestEnsureLatestVersion:
    def test_missing_returns_input(self, tmp_path):
        target = tmp_path / "nope.kohakutr"
        out = ensure_latest_version(target)
        # No on-disk file → returns input as-is.
        assert out == Path(target)

    def test_already_latest(self, tmp_path, monkeypatch):
        v = tmp_path / "a.kohakutr.v2"
        v.write_bytes(b"")
        out = ensure_latest_version(v)
        # Best on-disk version (2) ≥ MAX_SUPPORTED_VERSION (also 2).
        assert out == v

    def test_all_files_too_new(self, tmp_path, monkeypatch):
        future = tmp_path / "a.kohakutr.v999"
        future.write_bytes(b"")
        out = ensure_latest_version(future)
        # Falls back to the newest candidate even though we can't read it.
        assert out == future

    def test_runs_migration_and_returns_new_path(self, tmp_path, monkeypatch):
        # A v1 file below MAX_SUPPORTED_VERSION → ensure_latest_version
        # runs the registered migrator and returns the upgraded path.
        src = tmp_path / "a.kohakutr"
        src.write_bytes(b"")
        monkeypatch.setattr(mig_mod, "detect_format_version", lambda p: 1)
        migrated = []

        def _migrator(s, d):
            Path(d).write_bytes(b"migrated")
            migrated.append((Path(s), Path(d)))

        monkeypatch.setitem(MIGRATORS, (1, 2), _migrator)
        out = ensure_latest_version(src)
        # The migrator ran and the upgraded ``.v2`` path is returned.
        assert out == tmp_path / "a.kohakutr.v2"
        assert len(migrated) == 1
        assert out.exists()


# ── migrate: partial-cleanup OSError ──────────────────────────────


class TestMigratePartialCleanupFailure:
    def test_cleanup_oserror_is_logged_not_raised(self, tmp_path, monkeypatch):
        # When the migrator fails AND removing the partial output also
        # fails, migrate still raises the *migration* error (not the
        # cleanup OSError) — the cleanup failure is logged + swallowed.
        src = tmp_path / "a.kohakutr"
        src.write_bytes(b"")
        monkeypatch.setattr(mig_mod, "detect_format_version", lambda p: 1)

        def bad_migrator(s, d):
            Path(d).write_bytes(b"partial")
            raise RuntimeError("migrator boom")

        monkeypatch.setitem(MIGRATORS, (1, 2), bad_migrator)

        real_unlink = Path.unlink

        def _flaky_unlink(self, *a, **kw):
            if self.name.endswith(".v2"):
                raise OSError("unlink denied")
            return real_unlink(self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", _flaky_unlink)
        # The migration error surfaces; the cleanup OSError does not.
        with pytest.raises(RuntimeError, match="migration v1.*failed"):
            migrate(src, 2)


# ── migration_marker ──────────────────────────────────────────────


class TestMigrationMarker:
    def test_iso_format(self):
        from datetime import datetime, timezone

        m = migration_marker()
        # A round-trippable ISO-8601 UTC timestamp.
        parsed = datetime.fromisoformat(m)
        assert parsed.tzinfo == timezone.utc
        assert m.endswith("+00:00")


# ── module-level invariants ──────────────────────────────────────


class TestModuleInvariants:
    def test_max_matches_format_version(self):
        from kohakuterrarium.session.version import FORMAT_VERSION

        assert MAX_SUPPORTED_VERSION == FORMAT_VERSION

    def test_registered_migrator_present(self):
        assert (1, 2) in MIGRATORS
