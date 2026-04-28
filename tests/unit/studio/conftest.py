"""Shared fixtures for studio backend tests.

Every test here mounts its own FastAPI app with just the studio
router (not the full core app) — we assert studio routes behave
correctly in isolation. An ephemeral ``LocalWorkspace`` is wired
via ``deps.set_workspace`` per test via the ``client`` fixture.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.studio import build_studio_router
from kohakuterrarium.api.studio.deps import set_workspace


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Return a fresh workspace root with the canonical subdirs.

    Tests that need specific contents should write them inside
    this tree directly — fixtures don't presume a schema.
    """
    (tmp_path / "creatures").mkdir()
    (tmp_path / "modules" / "tools").mkdir(parents=True)
    (tmp_path / "modules" / "subagents").mkdir(parents=True)
    (tmp_path / "modules" / "plugins").mkdir(parents=True)
    (tmp_path / "modules" / "triggers").mkdir(parents=True)
    (tmp_path / "modules" / "inputs").mkdir(parents=True)
    (tmp_path / "modules" / "outputs").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def studio_app() -> FastAPI:
    """Minimal FastAPI app with just the studio router mounted."""
    app = FastAPI()
    app.include_router(build_studio_router())
    return app


@pytest.fixture
def no_workspace_client(studio_app: FastAPI) -> TestClient:
    """Client with no workspace open — for 409 tests."""
    set_workspace(None)
    with TestClient(studio_app) as c:
        yield c
    set_workspace(None)


@pytest.fixture
def client(studio_app: FastAPI, tmp_workspace: Path) -> TestClient:
    """Client with ``tmp_workspace`` wired as the active workspace.

    Phase 1 task 1.1 adds ``LocalWorkspace`` — until then, this
    fixture is expected to be overridden by tests that need it.
    """
    # Late import to avoid circularity with future workspace module.
    from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace  # noqa: E402

    set_workspace(LocalWorkspace.open(tmp_workspace))
    with TestClient(studio_app) as c:
        yield c
    set_workspace(None)


@pytest.fixture
def fixture_root() -> Path:
    """Root for checked-in fixture files (goldens, sample sources)."""
    return Path(__file__).parent / "fixtures"
