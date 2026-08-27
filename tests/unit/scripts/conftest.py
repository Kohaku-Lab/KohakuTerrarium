"""Locate the repo checkout for tests that drive ``scripts/*.py``.

The scripts are not part of the installed package, so tests reach them
through pytest's rootdir rather than walking up from ``__file__``.
"""

import pytest


@pytest.fixture
def repo_root(pytestconfig):
    """Path to the checkout under test (pytest's rootdir)."""
    return pytestconfig.rootpath


@pytest.fixture
def scripts_dir(repo_root):
    """Directory holding the CI helper scripts."""
    return repo_root / "scripts"
