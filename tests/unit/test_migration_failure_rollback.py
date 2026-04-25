"""Wave D — migration failure cleans up partial output.

Monkeypatches the v1→v2 migrator to raise partway through creating
the v2 file. The framework must remove the partial ``.v2`` file and
re-raise with the original v1 path in the error message so users
can recover.
"""

import pytest

from kohakuterrarium.session import migrations
from kohakuterrarium.session.migrations import migrate, v1_to_v2
from tests.unit.fixtures.sessions import build_v1_basic_session


@pytest.fixture
def v1_session(tmp_path):
    path = tmp_path / "alice.kohakutr"
    build_v1_basic_session(path, agent="alice")
    return path


def test_partial_v2_is_deleted_on_migrator_failure(v1_session, monkeypatch):
    v2_path = v1_session.with_name("alice.kohakutr.v2")

    def flaky_migrator(src: str, dst: str) -> None:
        # Touch the destination so we can verify cleanup happens.
        from pathlib import Path

        Path(dst).write_bytes(b"partial")
        raise RuntimeError("injected failure mid-migration")

    # Patch the canonical location so the registry sees it.
    monkeypatch.setitem(migrations.MIGRATORS, (1, 2), flaky_migrator)
    # Also patch the direct module reference in case someone imported
    # v1_to_v2.migrate directly (defensive).
    monkeypatch.setattr(v1_to_v2, "migrate", flaky_migrator)

    with pytest.raises(RuntimeError) as exc_info:
        migrate(v1_session, target_version=2)

    message = str(exc_info.value)
    assert str(v1_session) in message
    assert "v1" in message and "v2" in message
    assert not v2_path.exists(), "partial v2 file must be removed"
    assert v1_session.exists(), "v1 file must stay intact"
