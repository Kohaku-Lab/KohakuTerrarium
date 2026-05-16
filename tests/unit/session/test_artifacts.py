"""Unit tests for :mod:`kohakuterrarium.session.artifacts`."""

from pathlib import Path

import pytest

from kohakuterrarium.session.artifacts import (
    artifacts_dir_for,
    resolve_artifact_relpath,
    write_artifact_bytes,
)

# ── resolve_artifact_relpath ────────────────────────────────────


class TestResolveArtifactRelpath:
    def test_valid_relative(self):
        assert resolve_artifact_relpath("a.png") == Path("a.png")
        assert resolve_artifact_relpath("sub/a.png") == Path("sub/a.png")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            resolve_artifact_relpath("")

    def test_absolute_rejected(self, tmp_path):
        # Use a platform-correct absolute path (tmp_path is absolute).
        absolute = str(tmp_path / "abs.png")
        with pytest.raises(ValueError, match="relative"):
            resolve_artifact_relpath(absolute)

    def test_traversal_rejected(self):
        with pytest.raises(ValueError, match="traversal"):
            resolve_artifact_relpath("../escape.png")
        with pytest.raises(ValueError, match="traversal"):
            resolve_artifact_relpath("a/../b.png")


# ── artifacts_dir_for ───────────────────────────────────────────


class TestArtifactsDirFor:
    def test_creates_sibling_dir(self, tmp_path):
        session = tmp_path / "my-session.kohakutr.v2"
        session.write_text("")
        out = artifacts_dir_for(session)
        # Sibling named <stem>.artifacts/.
        assert out.parent == tmp_path
        assert out.name == "my-session.kohakutr.artifacts"
        assert out.is_dir()


# ── write_artifact_bytes ────────────────────────────────────────


class TestWriteArtifactBytes:
    def test_writes_bytes(self, tmp_path):
        d = tmp_path / "art"
        d.mkdir()
        out = write_artifact_bytes(d, "x.png", b"PNG-RAW")
        assert out.read_bytes() == b"PNG-RAW"

    def test_subdir_created(self, tmp_path):
        d = tmp_path / "art"
        d.mkdir()
        out = write_artifact_bytes(d, "sub/a.png", b"data")
        assert out.parent.is_dir()
        assert out.read_bytes() == b"data"

    def test_rejects_traversal(self, tmp_path):
        d = tmp_path / "art"
        d.mkdir()
        with pytest.raises(ValueError):
            write_artifact_bytes(d, "../escape.png", b"x")

    def test_symlink_escape_rejected(self, tmp_path, monkeypatch):
        """A relpath that resolves outside the artifacts dir (via a
        compromised parent directory or pre-existing symlink) is
        rejected after final resolution."""
        d = tmp_path / "art"
        d.mkdir()

        # Force Path.resolve() to return an outside path to simulate
        # a symlink escape.
        original_resolve = Path.resolve

        def fake_resolve(self):
            if str(self).endswith(".png"):
                return Path("/some/other/place.png")
            return original_resolve(self)

        monkeypatch.setattr(Path, "resolve", fake_resolve)
        with pytest.raises(ValueError, match="escapes"):
            write_artifact_bytes(d, "x.png", b"data")
