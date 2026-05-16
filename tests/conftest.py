"""Shared fixtures for the entire test suite.

Discipline rules — every test in any tier must follow these:

1. **Behavior asserts, not shape asserts.**  ``assert resp.status_code in
   {200, 400}`` is forbidden — assert the EXACT expected status and a
   specific observable side effect (state change, event fired, return
   value content).
2. **No silent mock-passthroughs.**  If a fake is used, its job is to
   stand in for a deterministic dependency (filesystem, network, LLM
   response), not to replace the unit under test.
3. **No global state leaks.**  Tests that touch a global registry
   (``lifecycle._meta``, ``api.deps._service``, …) MUST snapshot +
   restore that state.  Use the ``isolate_global_state`` fixture.

These rules are how we earn the right to claim "covered."
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure ``src/`` is on the import path even outside the editable
# install — some CI configurations don't run ``pip install -e .`` first.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Module-import-time isolation: some framework modules instantiate a
# logging FileHandler under ``config_dir() / "logs"`` the very first
# time ``get_logger`` is called — which can happen during pytest's
# test-module *collection*, BEFORE any fixture (autouse or otherwise)
# runs.  We pin ``KT_CONFIG_DIR`` to a session-wide tmp dir here so
# even those import-time writes land in tmp instead of the operator's
# real ``~/.kohakuterrarium/``.  The autouse fixture below further
# narrows this to a per-test ``tmp_path`` so tests don't share state.
_SESSION_CONFIG_DIR = Path(tempfile.mkdtemp(prefix="kt-test-session-config-"))
os.environ["KT_CONFIG_DIR"] = str(_SESSION_CONFIG_DIR)


@pytest.fixture(autouse=True)
def isolate_global_state():
    """Snapshot every module-global registry used by the framework.

    Tests routinely mutate ``lifecycle._meta``, ``api.deps._service``,
    ``llm.api_keys._resolver``, etc.  Without isolation a failure in
    one test poisons the next; we've already had several "passes
    alone, fails in suite" episodes in the old suite.
    """
    snapshots: dict[str, object] = {}

    try:
        from kohakuterrarium.studio.sessions import lifecycle

        snapshots["lifecycle._meta"] = dict(lifecycle._meta)
        snapshots["lifecycle._session_stores"] = dict(lifecycle._session_stores)
    except Exception:
        pass

    try:
        from kohakuterrarium.api import deps

        snapshots["deps._service"] = deps._service
        snapshots["deps._engine_legacy"] = deps._engine_legacy
        snapshots["deps._engine_legacy_warned"] = set(deps._engine_legacy_warned)
    except Exception:
        pass

    try:
        from kohakuterrarium.llm import api_keys

        snapshots["api_keys._resolver"] = getattr(api_keys, "_resolver", None)
    except Exception:
        pass

    try:
        from kohakuterrarium.llm import codex_auth

        snapshots["codex_auth._resolver"] = getattr(codex_auth, "_resolver", None)
    except Exception:
        pass

    try:
        from kohakuterrarium.llm import backends, preset_store

        snapshots["preset_store._remote_presets"] = dict(preset_store._remote_presets)
        snapshots["backends._remote_backends"] = dict(backends._remote_backends)
    except Exception:
        pass

    yield snapshots

    try:
        from kohakuterrarium.studio.sessions import lifecycle

        lifecycle._meta.clear()
        lifecycle._meta.update(snapshots.get("lifecycle._meta", {}))
        lifecycle._session_stores.clear()
        lifecycle._session_stores.update(snapshots.get("lifecycle._session_stores", {}))
    except Exception:
        pass

    try:
        from kohakuterrarium.api import deps

        deps._service = snapshots.get("deps._service")
        deps._engine_legacy = snapshots.get("deps._engine_legacy")
        deps._engine_legacy_warned = snapshots.get("deps._engine_legacy_warned", set())
    except Exception:
        pass

    try:
        from kohakuterrarium.llm import api_keys

        api_keys._resolver = snapshots.get("api_keys._resolver")
    except Exception:
        pass

    try:
        from kohakuterrarium.llm import codex_auth

        codex_auth._resolver = snapshots.get("codex_auth._resolver")
    except Exception:
        pass

    try:
        from kohakuterrarium.llm import backends, preset_store

        preset_store._remote_presets.clear()
        preset_store._remote_presets.update(
            snapshots.get("preset_store._remote_presets", {})
        )
        backends._remote_backends.clear()
        backends._remote_backends.update(snapshots.get("backends._remote_backends", {}))
    except Exception:
        pass


@pytest.fixture
def tmp_session_dir(tmp_path, monkeypatch):
    """Override the session-dir resolver to a per-test tmpdir.

    Several modules read ``~/.kohakuterrarium/sessions`` at import or
    runtime — this fixture redirects so tests never touch real user
    state.
    """
    target = tmp_path / "sessions"
    target.mkdir()
    monkeypatch.setenv("KOHAKUTERRARIUM_SESSION_DIR", str(target))
    return target


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Override the config dir likewise."""
    target = tmp_path / "config"
    target.mkdir()
    monkeypatch.setenv("KOHAKUTERRARIUM_CONFIG_DIR", str(target))
    return target


# Make Windows-locale issues go away — every test runs UTF-8.
os.environ.setdefault("PYTHONUTF8", "1")


def pytest_configure(config):
    """Use pytest-timeout's ``signal`` method on POSIX so a single
    hanging test FAILS that test and continues with the next one,
    instead of ``os._exit(1)``-ing the whole pytest process (which
    is what the ``thread`` method does — it can't actually interrupt
    a blocking native call from another thread).

    ``signal`` mode relies on ``SIGALRM`` which is POSIX-only; on
    Windows we keep the project default (``thread``) so the test
    suite still runs.  The pyproject default stays at ``thread`` so
    nothing changes for explicit ``--timeout-method`` overrides.
    """
    if sys.platform != "win32":
        try:
            config.option.timeout_method = "signal"
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _default_isolated_config_dir(tmp_path, monkeypatch):
    """Belt-and-braces: every test gets a tmp ``KT_CONFIG_DIR``.

    Any code path that saves a profile / api key / mcp server / ui pref
    / codex token resolves its path fresh through
    :func:`kohakuterrarium.utils.config_dir.config_dir` — honouring
    ``KT_CONFIG_DIR``.  A test that legitimately needs a specific
    config root sets ``KT_CONFIG_DIR`` itself (and our value here is
    overridden), but a test that *forgets* still writes into tmp
    instead of polluting the operator's real ``~/.kohakuterrarium/``.

    Previously several test files used the deprecated
    ``monkeypatch.setattr(mod, "PROFILES_PATH"/"KEYS_PATH", …)`` seam.
    That seam stopped working after the live read/write path moved to
    ``_profiles_path()`` / ``_keys_path()`` and silently leaked saves
    to the real user config.  This fixture is the last line of
    defence.
    """
    # ``setenv`` (not ``setdefault``) so we deterministically override
    # any inherited value from the operator's shell — the inherited
    # ``KT_CONFIG_DIR`` could itself point at the real config and
    # bypass the autouse intent.
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "kt-config-isolated"))
