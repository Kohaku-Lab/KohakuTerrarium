"""Tests for GET /api/studio/skills and POST /api/studio/skills/<name>/toggle."""

import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.studio.app import build_studio_router


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(build_studio_router())
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def _write_skill(base: Path, name: str, body: str = "hi") -> Path:
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    md = folder / "SKILL.md"
    md.write_text(
        f"---\nname: {name}\ndescription: {name}-desc\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return md


def test_get_list_empty_when_no_skills(client, tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(
        "kohakuterrarium.studio.editors.skills_state._STATE_FILE",
        state_file,
    )
    # Point cwd at tmp_path with no skills dirs.
    with mock.patch("kohakuterrarium.api.routes.catalog.skills.Path") as patched_path:
        patched_path.cwd.return_value = tmp_path
        patched_path.home.return_value = tmp_path / "home"
        r = client.get("/api/studio/skills")
    assert r.status_code == 200


def test_get_list_returns_discovered_skills(client, tmp_path, monkeypatch):
    _write_skill(tmp_path / ".kt" / "skills", "alpha")
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(
        "kohakuterrarium.studio.editors.skills_state._STATE_FILE",
        state_file,
    )
    monkeypatch.chdir(tmp_path)
    r = client.get("/api/studio/skills")
    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data]
    assert "alpha" in names
    alpha = next(s for s in data if s["name"] == "alpha")
    assert alpha["description"] == "alpha-desc"
    assert alpha["origin"] == "project"
    assert "enabled" in alpha
    assert "disable_model_invocation" in alpha


def test_toggle_flips_state(client, tmp_path, monkeypatch):
    _write_skill(tmp_path / ".kt" / "skills", "beta")
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(
        "kohakuterrarium.studio.editors.skills_state._STATE_FILE",
        state_file,
    )
    monkeypatch.chdir(tmp_path)
    # First toggle: enabled by default (project scope) → toggles to disabled.
    r = client.post("/api/studio/skills/beta/toggle")
    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "beta"
    assert payload["enabled"] is False
    # File persisted.
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["beta"] is False

    # Second toggle flips back.
    r = client.post("/api/studio/skills/beta/toggle")
    assert r.json()["enabled"] is True


def test_toggle_unknown_skill_404(client, tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(
        "kohakuterrarium.studio.editors.skills_state._STATE_FILE",
        state_file,
    )
    monkeypatch.chdir(tmp_path)
    r = client.post("/api/studio/skills/not-real/toggle")
    assert r.status_code == 404


def test_list_serializes_paths_and_allowed_tools(client, tmp_path, monkeypatch):
    folder = tmp_path / ".kt" / "skills" / "rich"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\n"
        "name: rich\n"
        "description: rich one\n"
        "paths: ['*.pdf', 'docs/**']\n"
        "allowed-tools: [read, bash]\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(
        "kohakuterrarium.studio.editors.skills_state._STATE_FILE",
        state_file,
    )
    monkeypatch.chdir(tmp_path)
    r = client.get("/api/studio/skills")
    data = r.json()
    rich = next(s for s in data if s["name"] == "rich")
    assert rich["paths"] == ["*.pdf", "docs/**"]
    assert rich["allowed_tools"] == ["read", "bash"]
