"""Catalog extensions route — aggregated package-manifest view."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.catalog import extensions as ext_mod


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(
        ext_mod,
        "list_packages",
        lambda: [
            {
                "name": "alpha",
                "version": "1.0",
                "editable": False,
                "plugins": [{"name": "sandbox", "description": "cap gating"}],
                "tools": ["my_tool"],
                "triggers": [],
                "io": [],
                "llm_presets": [{"name": "claude-opus"}],
                "skills": [],
                "commands": [],
                "user_commands": [],
                "prompts": [],
            },
            {
                "name": "beta",
                "version": "0.3",
                "editable": True,
                "plugins": [],
                "tools": [{"name": "grep_tool", "module": "beta.grep"}],
                "triggers": ["nightly"],
                "io": [],
                "llm_presets": [],
                "skills": ["plan"],
                "commands": [],
                "user_commands": [],
                "prompts": [],
            },
        ],
    )
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(ext_mod.router, prefix="/api/registry/extensions")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestList:
    def test_list_flattens_manifests(self, client):
        resp = client.get("/api/registry/extensions")
        assert resp.status_code == 200, resp.text
        entries = resp.json()
        names = [(e["kind"], e["name"]) for e in entries]
        assert ("plugin", "sandbox") in names
        assert ("tool", "my_tool") in names
        assert ("tool", "grep_tool") in names
        assert ("trigger", "nightly") in names
        assert ("llm-preset", "claude-opus") in names
        assert ("skill", "plan") in names
        # Editable flag propagates from package.
        beta_tool = next(e for e in entries if e["name"] == "grep_tool")
        assert beta_tool["editable"] is True
        assert beta_tool["module"] == "beta.grep"
        # Stable order: kind → package → name.
        assert entries == sorted(
            entries, key=lambda e: (e["kind"], e["package"], e["name"])
        )

    def test_get_one(self, client):
        resp = client.get("/api/registry/extensions/plugin/sandbox")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "sandbox"
        assert body["package"] == "alpha"
        assert body["description"] == "cap gating"

    def test_get_missing_404(self, client):
        resp = client.get("/api/registry/extensions/plugin/nosuch")
        assert resp.status_code == 404
