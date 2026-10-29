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

import ipaddress
import os
import sys
import tempfile
import uuid
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
# The operator's home, captured before any fixture redirects HOME; read-only.
os.environ.setdefault("KT_TEST_REAL_HOME", str(Path.home()))

from kohakuterrarium.llm.api_keys import PROVIDER_KEY_MAP  # noqa: E402
from kohakuterrarium.testing.llm_seam import (  # noqa: E402
    forbid_real_providers,
    install_llm_seam,
)

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", ""}
_NETWORK_GUARD = {"blocked": False}


def _is_loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).strip("[]").split("%", 1)[0].lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback or host in ("0.0.0.0", "::")
    except ValueError:
        return False


def _network_audit(event, args):
    """Refuse non-loopback connects and lookups while a test blocks the network."""
    if not _NETWORK_GUARD["blocked"]:
        return
    if event == "socket.connect":
        address = args[1]
        host = address[0] if isinstance(address, tuple) and address else None
    elif event == "socket.getaddrinfo":
        host = args[0]
    else:
        return
    if not _is_loopback(host):
        raise OSError(
            f"network is blocked in tests (to {host!r}); "
            "mark the test @pytest.mark.allow_network if it must reach out"
        )


sys.addaudithook(_network_audit)

# ``-n auto`` resolves to this many xdist workers unless KT_TEST_WORKERS is set.
LOCAL_TEST_WORKERS = 16
MAX_TEST_WORKERS = 64
_E2E_DIR = _ROOT / "tests" / "e2e"


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config):
    """Worker count for ``-n auto``: ``KT_TEST_WORKERS``, else min(16, cpus), cap 64."""
    raw = os.environ.get("KT_TEST_WORKERS", "").strip()
    count = int(raw) if raw else min(LOCAL_TEST_WORKERS, os.cpu_count() or 1)
    return max(0, min(count, MAX_TEST_WORKERS))


def pytest_collection_modifyitems(config, items):
    """Run the e2e tier as one serial group; its journeys share real clusters."""
    for item in items:
        if Path(str(item.path)).is_relative_to(_E2E_DIR):
            item.add_marker(pytest.mark.xdist_group("e2e"))


@pytest.fixture(autouse=True)
def isolate_global_state():
    """Snapshot every module-global registry used by the framework.

    Tests routinely mutate ``lifecycle._meta``, ``api.deps._service``,
    ``llm.api_keys._resolver``, etc.  Without isolation a failure in
    one test poisons the next; we've already had several "passes
    alone, fails in suite" episodes in the old suite.
    """
    snapshots: dict[str, object] = {}

    # NB: studio session bookkeeping (the old ``lifecycle._meta`` /
    # ``_session_stores`` module globals) is now INSTANCE-scoped on the
    # engine/service (``studio.sessions.registry``) — it dies with the
    # per-test engine and needs no snapshot here.

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
    config.addinivalue_line(
        "markers",
        "real_llm_provider: build real provider classes (against fakes); "
        "lifts the LLM seam and the real-provider block",
    )
    config.addinivalue_line(
        "markers", "allow_network: lift the non-loopback network block"
    )


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
    # Summaries default to the llm source, which would consume ScriptedLLM entries.
    monkeypatch.setenv("KT_SESSION_SUMMARY_SOURCE", "heuristic")
    yield
    # The live-session list keeps one open file per config dir; close it so tmp cleanup can delete it.
    live = sys.modules.get("kohakuterrarium.studio.sessions.live.registry")
    if live is not None:
        live.close_all()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Give every test a private home and temp root under a uuid-named folder.

    HOME / USERPROFILE / XDG dirs and TMP / TEMP / TMPDIR (inherited by child
    processes) plus ``tempfile.tempdir`` point inside it, so concurrent workers
    and concurrent CI jobs never share a path. The folder sits beside, not
    inside, the test's ``tmp_path``.
    """
    root = tmp_path_factory.mktemp(f"kt-{uuid.uuid4().hex[:12]}", numbered=False)
    home = root / "home"
    temp = root / "tmp"
    home.mkdir()
    temp.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    for name, sub in (
        ("XDG_CONFIG_HOME", ".config"),
        ("XDG_DATA_HOME", ".local/share"),
        ("XDG_STATE_HOME", ".local/state"),
        ("XDG_CACHE_HOME", ".cache"),
    ):
        monkeypatch.setenv(name, str(home / sub))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    return root


@pytest.fixture(autouse=True)
def _no_real_provider_login(monkeypatch):
    """Make a test fail loudly instead of opening a real provider login.

    A provider built without credentials falls back to the browser OAuth flow.
    A test that exercises that path replaces ``oauth_login`` itself.
    """

    async def _refuse(*args, **kwargs):
        raise AssertionError("a test tried to start a real provider login")

    monkeypatch.setattr("kohakuterrarium.llm.codex_auth.oauth_login", _refuse)
    monkeypatch.setattr("kohakuterrarium.llm.codex_provider.oauth_login", _refuse)


@pytest.fixture(autouse=True)
def _no_inherited_provider_keys(monkeypatch):
    """Hide the operator's provider API keys (``OPENAI_API_KEY`` ...) from tests.

    ``get_api_key`` falls back to these variables, so an operator shell with a
    real key changes results. A test that needs a key sets it itself.
    """
    for name in PROVIDER_KEY_MAP.values():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _llm_seam(request, monkeypatch):
    """Route every provider factory to ``ScriptedLLM`` and refuse real providers.

    ``@pytest.mark.real_llm_provider`` lifts both for a test that builds
    provider classes against fake transports.
    """
    if request.node.get_closest_marker("real_llm_provider") is None:
        install_llm_seam(monkeypatch)
        forbid_real_providers(monkeypatch.setattr)


@pytest.fixture(autouse=True)
def _block_network(request):
    """Block non-loopback sockets unless the test is marked ``allow_network``."""
    _NETWORK_GUARD["blocked"] = request.node.get_closest_marker("allow_network") is None
    yield
    _NETWORK_GUARD["blocked"] = False
