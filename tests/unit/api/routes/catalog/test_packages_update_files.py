"""Update + files endpoints on the catalog packages route."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.catalog import packages as packages_mod


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build a FastAPI app pointing PACKAGES_DIR at a tmp scratch dir."""
    monkeypatch.setattr(packages_mod, "PACKAGES_DIR", tmp_path)
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(packages_mod.router, prefix="/api/registry")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _seed_package(root, name="alpha"):
    """Create a minimal package dir with a yaml + a markdown file."""
    pkg = root / name
    (pkg / "creatures" / "alpha").mkdir(parents=True)
    (pkg / "creatures" / "alpha" / "config.yaml").write_text(
        "name: alpha\nmodel: gpt-4\ndescription: test\n", encoding="utf-8"
    )
    (pkg / "README.md").write_text("# alpha\n\nhello\n", encoding="utf-8")
    return pkg


class TestUpdate:
    def test_update_unknown_returns_500(self, client, monkeypatch):
        monkeypatch.setattr(
            packages_mod, "update_package_op", lambda name: (1, f"unknown: {name}")
        )
        resp = client.post("/api/registry/nosuch/update")
        assert resp.status_code == 500
        assert "unknown" in resp.json()["detail"]

    def test_update_ok(self, client, monkeypatch):
        monkeypatch.setattr(
            packages_mod, "update_package_op", lambda name: (0, f"Updated: {name}")
        )
        resp = client.post("/api/registry/alpha/update")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["name"] == "alpha"
        assert "Updated" in body["message"]

    def test_update_all_summary(self, client, monkeypatch):
        monkeypatch.setattr(
            packages_mod,
            "update_all_packages_op",
            lambda: (0, ["Updated: a", "Skipped editable: b"], 1, 1),
        )
        resp = client.post("/api/registry/update-all")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["updated"] == 1
        assert body["skipped"] == 1
        assert "Skipped editable: b" in body["messages"]


class TestFiles:
    def test_list_files(self, client, tmp_path):
        _seed_package(tmp_path)
        resp = client.get("/api/registry/alpha/files")
        assert resp.status_code == 200, resp.text
        paths = {f["path"] for f in resp.json()}
        # rglob includes directories — but we only assert files of
        # interest are present.
        assert "README.md" in paths
        assert "creatures/alpha/config.yaml" in paths

    def test_list_files_unknown_404(self, client):
        resp = client.get("/api/registry/nosuch/files")
        assert resp.status_code == 404

    def test_read_file_returns_content(self, client, tmp_path):
        _seed_package(tmp_path)
        resp = client.get("/api/registry/alpha/files/README.md")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "hello" in body["content"]
        assert body["encoding"] == "utf-8"
        assert len(body["sha256"]) == 64

    def test_read_path_traversal_blocked(self, client, tmp_path):
        _seed_package(tmp_path)
        # ``%2F..%2F..%2Fetc%2Fpasswd`` would resolve outside the pkg root.
        resp = client.get("/api/registry/alpha/files/..%2F..%2Fetc%2Fpasswd")
        # Either 403 (escapes root) or 404 (no such file) — both block reads.
        assert resp.status_code in (403, 404)

    def test_write_file_round_trip(self, client, tmp_path):
        pkg = _seed_package(tmp_path)
        # Read current sha
        read = client.get("/api/registry/alpha/files/README.md").json()
        sha = read["sha256"]
        # Write with correct sha — should succeed.
        resp = client.put(
            "/api/registry/alpha/files/README.md",
            json={"content": "# alpha\n\nupdated\n", "sha256_expected": sha},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ok"
        # Verify on disk.
        assert "updated" in (pkg / "README.md").read_text(encoding="utf-8")

    def test_write_file_concurrent_conflict(self, client, tmp_path):
        _seed_package(tmp_path)
        # Wrong sha → 409
        resp = client.put(
            "/api/registry/alpha/files/README.md",
            json={"content": "x", "sha256_expected": "0" * 64},
        )
        assert resp.status_code == 409
