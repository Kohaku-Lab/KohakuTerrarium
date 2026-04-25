"""Wave D — resume prefers the highest-version file on disk.

When both ``alice.kohakutr`` (v1) and ``alice.kohakutr.v2`` exist,
``ensure_latest_version`` returns the v2 file without triggering a
re-migration.
"""

import pytest

from kohakuterrarium.session.migrations import (
    discover_versions,
    ensure_latest_version,
    migrate,
)
from tests.unit.fixtures.sessions import build_v1_basic_session


@pytest.fixture
def v1_and_v2(tmp_path):
    """Create both a v1 file and its migrated v2 neighbour."""
    v1_path = tmp_path / "alice.kohakutr"
    build_v1_basic_session(v1_path, agent="alice")
    v2_path = migrate(v1_path, target_version=2)
    return v1_path, v2_path


def test_discover_versions_lists_both_descending(v1_and_v2):
    v1_path, v2_path = v1_and_v2
    versions = discover_versions(v1_path)
    # Highest version first.
    assert versions[0][0] == 2
    assert versions[0][1] == v2_path
    assert any(v == 1 and p == v1_path for v, p in versions)


def test_ensure_latest_returns_v2(v1_and_v2):
    v1_path, v2_path = v1_and_v2
    resolved = ensure_latest_version(v1_path)
    assert resolved == v2_path


def test_ensure_latest_does_not_re_migrate(v1_and_v2, monkeypatch):
    v1_path, v2_path = v1_and_v2
    pre_mtime = v2_path.stat().st_mtime

    called = {"count": 0}

    def tripwire(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("migrate() must not run when v2 already exists")

    from kohakuterrarium.session import migrations

    monkeypatch.setattr(migrations, "migrate", tripwire)

    resolved = ensure_latest_version(v1_path)
    assert resolved == v2_path
    assert called["count"] == 0
    assert v2_path.stat().st_mtime == pre_mtime
