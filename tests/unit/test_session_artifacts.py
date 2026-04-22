"""Session-local artifact directory (images, attachments, etc.)."""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore


def test_artifacts_dir_lazy_created(tmp_path: Path):
    store_path = tmp_path / "agent_xyz.kohakutr"
    store = SessionStore(store_path)
    # Directory doesn't exist until first access.
    expected = tmp_path / "agent_xyz.artifacts"
    assert not expected.exists()
    resolved = store.artifacts_dir
    assert resolved == expected
    assert expected.is_dir()


def test_write_artifact_basic(tmp_path: Path):
    store = SessionStore(tmp_path / "s.kohakutr")
    p = store.write_artifact("hello.png", b"\x89PNG\r\n\x1a\n")
    assert p.is_file()
    assert p.read_bytes().startswith(b"\x89PNG")
    assert p == store.artifacts_dir / "hello.png"


def test_write_artifact_subdir_allowed(tmp_path: Path):
    store = SessionStore(tmp_path / "s.kohakutr")
    p = store.write_artifact("generated_images/cat.png", b"xxx")
    assert p.is_file()
    assert p.parent == store.artifacts_dir / "generated_images"


def test_write_artifact_rejects_traversal(tmp_path: Path):
    store = SessionStore(tmp_path / "s.kohakutr")
    with pytest.raises(ValueError):
        store.write_artifact("../escape.png", b"x")
    with pytest.raises(ValueError):
        store.write_artifact("a/../../b.png", b"x")


def test_write_artifact_rejects_absolute(tmp_path: Path):
    store = SessionStore(tmp_path / "s.kohakutr")
    other = tmp_path / "outside.png"
    with pytest.raises(ValueError):
        store.write_artifact(str(other), b"x")
