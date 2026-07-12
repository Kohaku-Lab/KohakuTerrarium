"""Identity config-files — list / read / write whitelisted files."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.identity import config_files as cf_mod


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(cf_mod.router, prefix="/api/settings")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestList:
    def test_list_includes_whitelist(self, client):
        resp = client.get("/api/settings/config-files")
        assert resp.status_code == 200
        names = {f["name"] for f in resp.json()}
        for expected in (
            "api_keys",
            "llm_profiles",
            "mcp_servers",
            "app-settings",
            "ui-prefs",
        ):
            assert expected in names

    def test_list_marks_missing_as_not_exist(self, client):
        body = client.get("/api/settings/config-files").json()
        entry = next(f for f in body if f["name"] == "api_keys")
        assert entry["exists"] is False
        assert entry["size"] == 0


class TestRead:
    def test_read_unknown_404(self, client):
        resp = client.get("/api/settings/config-files/nosuch/content")
        assert resp.status_code == 404

    def test_read_missing_returns_empty(self, client):
        resp = client.get("/api/settings/config-files/api_keys/content")
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == ""
        assert body["kind"] == "yaml"


class TestWrite:
    def test_write_yaml_round_trip(self, client, tmp_path):
        # Write valid YAML.
        resp = client.put(
            "/api/settings/config-files/api_keys/content",
            json={"content": "openai: sk-test\n"},
        )
        assert resp.status_code == 200, resp.text
        # Read back.
        read = client.get("/api/settings/config-files/api_keys/content").json()
        assert "openai: sk-test" in read["content"]
        # On-disk file exists.
        assert (
            (tmp_path / "api_keys.yaml")
            .read_text(encoding="utf-8")
            .startswith("openai:")
        )

    def test_invalid_yaml_rejected(self, client):
        # Tab indentation + colon mismatch — clearly invalid.
        resp = client.put(
            "/api/settings/config-files/api_keys/content",
            json={"content": "openai:\n\t- malformed: [unclosed"},
        )
        assert resp.status_code == 400
        assert "YAML parse" in resp.json()["detail"]

    def test_invalid_json_rejected(self, client):
        resp = client.put(
            "/api/settings/config-files/app-settings/content",
            json={"content": "{ not valid json"},
        )
        assert resp.status_code == 400
        assert "JSON parse" in resp.json()["detail"]

    def test_concurrent_conflict(self, client):
        # Seed.
        client.put(
            "/api/settings/config-files/api_keys/content",
            json={"content": "openai: a\n"},
        )
        # Write with bogus sha.
        resp = client.put(
            "/api/settings/config-files/api_keys/content",
            json={"content": "openai: b\n", "sha256_expected": "0" * 64},
        )
        assert resp.status_code == 409


class TestDriveSettings:
    def test_drive_settings_whitelisted(self, client):
        names = {f["name"] for f in client.get("/api/settings/config-files").json()}
        assert "drive-settings" in names

    def test_write_valid_drive_settings_persists(self, client, tmp_path):
        expected = client.get(
            "/api/settings/config-files/drive-settings/content"
        ).json()["sha256"]
        resp = client.put(
            "/api/settings/config-files/drive-settings/content",
            json={
                "content": (
                    "schema_version: 1\n"
                    "runtime:\n  enabled: true\n"
                    "registrations:\n  generic:\n    enabled: true\n"
                ),
                "sha256_expected": expected,
            },
        )
        assert resp.status_code == 200, resp.text
        assert (tmp_path / "drive-settings.yaml").is_file()

    def test_write_schema_invalid_drive_settings_rejected(self, client):
        # Raw editor save is validated-persist only: a schema violation is a 400,
        # never a silent enable (design §8.4).
        expected = client.get(
            "/api/settings/config-files/drive-settings/content"
        ).json()["sha256"]
        resp = client.put(
            "/api/settings/config-files/drive-settings/content",
            json={
                "content": "runtime:\n  enabled: not-a-bool\n",
                "sha256_expected": expected,
            },
        )
        assert resp.status_code == 400
        assert "drive-settings schema error" in resp.json()["detail"]
