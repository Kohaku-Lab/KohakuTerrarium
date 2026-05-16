"""Unit tests for :mod:`kohakuterrarium.api.routes.persistence.artifacts`."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.persistence import artifacts as art_mod


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(art_mod.router, prefix="/sessions")
    return app


class TestArtifactRoute:
    def test_serves_file(self, monkeypatch, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG-RAW")
        monkeypatch.setattr(art_mod, "_resolve_artifact", lambda session, decoded: f)
        client = TestClient(_app())
        resp = client.get("/sessions/sess/artifacts/img.png")
        assert resp.status_code == 200
        assert resp.content == b"PNG-RAW"

    def test_404_on_resolution_failure(self, monkeypatch):
        def boom(session, decoded):
            raise HTTPException(404, "artifact not found")

        monkeypatch.setattr(art_mod, "_resolve_artifact", boom)
        client = TestClient(_app())
        resp = client.get("/sessions/sess/artifacts/missing.png")
        assert resp.status_code == 404

    def test_400_on_traversal(self, monkeypatch):
        def boom(session, decoded):
            raise HTTPException(400, "path escapes")

        monkeypatch.setattr(art_mod, "_resolve_artifact", boom)
        client = TestClient(_app())
        # URL-encoded ../escape.txt
        resp = client.get("/sessions/sess/artifacts/escape.txt")
        assert resp.status_code == 400

    def test_resolves_decoded_path(self, monkeypatch, tmp_path):
        captured = []

        def fake(session, decoded):
            captured.append(decoded)
            f = tmp_path / "a.bin"
            f.write_bytes(b"x")
            return f

        monkeypatch.setattr(art_mod, "_resolve_artifact", fake)
        client = TestClient(_app())
        # URL-encoded "a b.png"
        resp = client.get("/sessions/sess/artifacts/a%20b.png")
        assert resp.status_code == 200
        assert captured == ["a b.png"]


class TestResolveArtifactHelper:
    def test_uses_session_dir(self, monkeypatch, tmp_path):
        # Stub the underlying resolvers to capture how _resolve_artifact
        # composes them.
        captured = []

        def fake_dir(name, sess_dir):
            captured.append(("dir", name, sess_dir))
            return tmp_path / "artifacts"

        def fake_file(artifacts, filepath):
            captured.append(("file", artifacts, filepath))
            return tmp_path / "result.bin"

        monkeypatch.setattr(art_mod, "resolve_artifacts_dir", fake_dir)
        monkeypatch.setattr(art_mod, "resolve_artifact_file", fake_file)
        out = art_mod._resolve_artifact("sess", "rel.png")
        assert out == tmp_path / "result.bin"
        # Both helpers got called in order.
        assert [c[0] for c in captured] == ["dir", "file"]
