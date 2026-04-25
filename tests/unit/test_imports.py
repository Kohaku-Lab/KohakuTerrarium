"""Guard against circular imports and verify all modules import cleanly."""

import importlib
import pkgutil

import pytest

import kohakuterrarium


def _iter_all_modules():
    """Yield all module names under kohakuterrarium."""
    prefix = kohakuterrarium.__name__ + "."
    for info in pkgutil.walk_packages(
        kohakuterrarium.__path__, prefix=prefix, onerror=lambda name: None
    ):
        yield info.name


@pytest.mark.parametrize("module_name", list(_iter_all_modules()))
def test_import_module(module_name):
    """Every module should import without error (catches circular imports)."""
    # Skip modules that require optional deps at import time
    skip_prefixes = ("kohakuterrarium.builtins.outputs.tts",)
    if any(module_name.startswith(p) for p in skip_prefixes):
        pytest.skip("optional dependency")
    importlib.import_module(module_name)
