"""Unit tests for ``llm/artifact_resolve.py`` — local artifact → data URL.

Behavior-first: assert the single-URL resolver passes through non-local
URLs untouched, resolves a real on-disk artifact to a base64 ``data:``
URL, and falls back to the original string on a missing file; and that
the message-walk resolves ``image_url`` parts in place while staying an
identity no-op when nothing needs resolving (the common hot path).
"""

import base64

import pytest

from kohakuterrarium.llm import artifact_resolve
from kohakuterrarium.llm.artifact_resolve import (
    file_reference_path,
    resolve_artifact_url,
    resolve_message_image_urls,
)


def _lay_artifact(tmp_path, monkeypatch, sid="sid123", rel="pic.png", data=b"PNGDATA"):
    """Create ``<session_dir>/<sid>.artifacts/<rel>`` and point the resolver at it."""
    session_dir = tmp_path / "sessions"
    artifacts = session_dir / f"{sid}.artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / rel).write_bytes(data)
    monkeypatch.setattr(artifact_resolve, "_session_dir", lambda: session_dir)
    return session_dir


class TestResolveArtifactUrl:
    def test_non_artifact_urls_passthrough(self):
        assert resolve_artifact_url("https://example.com/x.png") == (
            "https://example.com/x.png"
        )
        assert resolve_artifact_url("data:image/png;base64,QUJD") == (
            "data:image/png;base64,QUJD"
        )

    def test_non_string_passthrough(self):
        assert resolve_artifact_url(None) is None

    def test_malformed_artifact_path_passthrough(self):
        assert resolve_artifact_url("/api/sessions/onlysid") == "/api/sessions/onlysid"

    def test_resolved_to_data_url(self, tmp_path, monkeypatch):
        _lay_artifact(tmp_path, monkeypatch)
        out = resolve_artifact_url("/api/sessions/sid123/artifacts/pic.png")
        assert out == "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()

    def test_missing_file_falls_back_to_original(self, tmp_path, monkeypatch):
        monkeypatch.setattr(artifact_resolve, "_session_dir", lambda: tmp_path)
        url = "/api/sessions/sid/artifacts/nope.png"
        assert resolve_artifact_url(url) == url


class TestFileReferences:
    @pytest.mark.parametrize(
        "name",
        [
            "image%20name.png",
            "image%25name.png",
            "image%2Fname.png",
            "a b.png",
            "图片.png",
        ],
    )
    def test_file_reference_preserves_exact_filename(self, tmp_path, name):
        pic = tmp_path / name
        pic.write_bytes(b"expected image")
        url = pic.resolve().as_uri()

        assert file_reference_path(url) == pic.resolve()
        assert resolve_artifact_url(url) == (
            "data:image/png;base64," + base64.b64encode(b"expected image").decode()
        )

    def test_file_reference_does_not_read_decoded_neighbor(self, tmp_path):
        pic = tmp_path / "image%20name.png"
        pic.write_bytes(b"expected image")
        (tmp_path / "image name.png").write_bytes(b"wrong image")

        assert resolve_artifact_url(pic.resolve().as_uri()) == (
            "data:image/png;base64," + base64.b64encode(b"expected image").decode()
        )

    def test_file_reference_inlined_from_disk(self, tmp_path):
        pic = tmp_path / "a b.png"
        pic.write_bytes(b"PNGDATA")
        out = resolve_artifact_url(pic.resolve().as_uri())
        assert out == "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()

    def test_missing_file_reference_falls_back_to_original(self, tmp_path):
        url = (tmp_path / "gone.png").resolve().as_uri()
        assert resolve_artifact_url(url) == url

    def test_file_reference_path_parsing(self, tmp_path):
        pic = tmp_path / "a b.png"
        assert file_reference_path(pic.resolve().as_uri()) == pic.resolve()
        assert file_reference_path("file://host/share/x.png") is None
        assert file_reference_path("https://example.com/x.png") is None
        assert file_reference_path(None) is None

    @pytest.mark.parametrize("authority", ["host", "127.0.0.1", "user@localhost"])
    def test_remote_authority_is_not_a_local_media_reference(self, authority):
        assert file_reference_path(f"file://{authority}/share/x.png") is None


class TestResolveMessageImageUrls:
    def test_identity_when_no_local_artifacts(self):
        msgs = [
            {"role": "user", "content": "plain text"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAA"},
                    },
                ],
            },
        ]
        # No local artifact url anywhere -> SAME list object back (no-op).
        assert resolve_message_image_urls(msgs) is msgs

    def test_resolves_local_artifact_part(self, tmp_path, monkeypatch):
        _lay_artifact(tmp_path, monkeypatch)
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "/api/sessions/sid123/artifacts/pic.png",
                            "detail": "auto",
                        },
                    },
                ],
            }
        ]
        out = resolve_message_image_urls(msgs)
        assert out is not msgs  # changed -> new list
        part = out[0]["content"][1]
        assert part["image_url"]["url"].startswith("data:image/png;base64,")
        # detail (and other keys) preserved.
        assert part["image_url"]["detail"] == "auto"
        # original untouched (identity-preserving copy).
        assert msgs[0]["content"][1]["image_url"]["url"].startswith("/api/sessions/")

    def test_non_image_parts_untouched(self, tmp_path, monkeypatch):
        _lay_artifact(tmp_path, monkeypatch)
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "/api/sessions/sid123/artifacts/pic.png"},
                ],
            }
        ]
        # A text part that merely *contains* the path is not rewritten.
        assert resolve_message_image_urls(msgs) is msgs
