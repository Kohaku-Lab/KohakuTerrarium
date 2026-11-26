"""Local paths in these tests are POSIX strings on every host OS."""

import pytest

from kohakuterrarium.studio.persistence.projects import paths as paths_mod


@pytest.fixture(autouse=True)
def posix_local_paths(monkeypatch):
    monkeypatch.setattr(paths_mod, "LOCAL_WINDOWS", False)
