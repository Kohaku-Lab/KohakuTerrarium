"""Wave D — migrating twice is a no-op.

If ``<name>.kohakutr.v2`` already exists, the migration call must
return it unchanged rather than overwrite. Calling ``migrate`` on a
file that's already at ``target_version`` returns its path directly.
"""

import hashlib
import time

import pytest

from kohakuterrarium.session.migrations import migrate
from tests.unit.fixtures.sessions import build_v1_basic_session


def _hash(path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


@pytest.fixture
def v1_session(tmp_path):
    path = tmp_path / "alice.kohakutr"
    build_v1_basic_session(path, agent="alice")
    return path


def test_migrate_twice_does_not_overwrite(v1_session):
    first_path = migrate(v1_session, target_version=2)
    first_hash = _hash(first_path)
    mtime = first_path.stat().st_mtime

    # Small wait so an accidental rewrite would move mtime forward.
    time.sleep(0.05)

    second_path = migrate(v1_session, target_version=2)

    assert second_path == first_path
    assert _hash(first_path) == first_hash
    assert first_path.stat().st_mtime == mtime


def test_migrate_already_at_target_returns_same_path(v1_session):
    v2_path = migrate(v1_session, target_version=2)
    # Calling migrate on the v2 file with target=2 is a no-op.
    again = migrate(v2_path, target_version=2)
    assert again == v2_path
