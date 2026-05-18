"""Orchestration tests with fake venv_ops primitives — no real pip."""

from pathlib import Path

import pytest

from kohakuterrarium.launcher import settings as _s
from kohakuterrarium.launcher import update_runner as _r
from kohakuterrarium.launcher import venv_ops as _v


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    # Pre-seed defaults so each test starts from a clean settings file.
    _s.reset()
    return tmp_path


@pytest.fixture
def stub_venv_ops(monkeypatch):
    """Replace each primitive with a deterministic recorder."""
    calls = {"create": [], "install": [], "smoke": [], "swap": [], "marker": []}
    # ``_build_and_smoke`` uses these via module-level imports inside
    # update_runner — patch THERE so our stubs are seen.
    monkeypatch.setattr(
        _r,
        "create_venv",
        lambda p: (
            calls["create"].append(p),
            Path(p).mkdir(parents=True, exist_ok=True),
        )[0],
    )
    monkeypatch.setattr(
        _r, "install_into", lambda p, args: calls["install"].append((p, args))
    )
    monkeypatch.setattr(
        _r, "smoke_test", lambda p: (calls["smoke"].append(p), "1.5.0")[1]
    )
    monkeypatch.setattr(
        _r,
        "atomic_swap",
        lambda c, n, b: (
            calls["swap"].append((c, n, b)),
            c.mkdir(parents=True, exist_ok=True) if not c.exists() else None,
        )[0],
    )
    monkeypatch.setattr(_r, "write_wrapper_marker", lambda p: calls["marker"].append(p))
    return calls


class TestFirstInstall:
    def test_happy_path(self, _isolate_config, stub_venv_ops):
        result = _r.first_install()
        assert result.ok
        assert result.version == "1.5.0"
        assert not result.restart_required
        # Settings persisted with the version + check timestamp.
        cfg = _s.load()
        assert cfg.runtime.last_installed_version == "1.5.0"
        assert cfg.runtime.last_check_at is not None
        # install_source reflects which source was actually used —
        # in this test the bundled-wheels dir isn't present so we
        # fall through to PyPI per the default source kind.
        assert cfg.runtime.install_source == "pypi"
        # primitives invoked in order.
        assert len(stub_venv_ops["create"]) == 1
        assert len(stub_venv_ops["install"]) == 1
        assert len(stub_venv_ops["smoke"]) == 1
        assert len(stub_venv_ops["marker"]) == 1

    def test_smoke_failure_cleans_up(self, _isolate_config, monkeypatch):
        monkeypatch.setattr(
            _r, "create_venv", lambda p: Path(p).mkdir(parents=True, exist_ok=True)
        )
        monkeypatch.setattr(_r, "install_into", lambda p, args: None)
        monkeypatch.setattr(
            _r, "smoke_test", lambda p: (_ for _ in ()).throw(_v.VenvOpError("nope"))
        )
        monkeypatch.setattr(_r, "write_wrapper_marker", lambda p: None)
        result = _r.first_install()
        assert not result.ok
        assert "nope" in (result.error or "")


class TestFirstInstallBundledFirst:
    """Topic 06 / sub-plan 01 — bundled-first first-install behaviour.

    The runner prefers bundled wheels when (a) ``bundled_wheels_dir``
    returns a populated directory AND (b) the user hasn't explicitly
    chosen a non-default source.  Updates still respect ``source.kind``.
    """

    def _populate_bundle(self, tmp_path: Path) -> Path:
        bundle = tmp_path / "wheels-bundle"
        bundle.mkdir()
        (bundle / "kohakuterrarium-1.5.0-py3-none-any.whl").write_bytes(b"")
        return bundle

    def test_uses_bundled_when_default_source_and_bundle_present(
        self, _isolate_config, monkeypatch, stub_venv_ops
    ):
        bundle = self._populate_bundle(_isolate_config)
        monkeypatch.setattr(_r, "bundled_wheels_dir", lambda: bundle)
        result = _r.first_install()
        assert result.ok
        cfg = _s.load()
        assert cfg.runtime.install_source == "bundled"
        # Pip args carried the bundled --find-links plus --no-index.
        pip_args = stub_venv_ops["install"][0][1]
        assert "--no-index" in pip_args
        assert "--find-links" in pip_args
        assert str(bundle) in pip_args

    def test_ignores_bundled_when_user_picked_non_default_source(
        self, _isolate_config, monkeypatch, stub_venv_ops
    ):
        bundle = self._populate_bundle(_isolate_config)
        monkeypatch.setattr(_r, "bundled_wheels_dir", lambda: bundle)
        # User explicitly picked git — bundled-first MUST stand down.
        cfg = _s.load()
        cfg.source.kind = "git"
        cfg.source.spec = "https://example.com/repo.git@main"
        _s.save(cfg)
        result = _r.first_install()
        assert result.ok
        cfg = _s.load()
        assert cfg.runtime.install_source == "git"
        pip_args = stub_venv_ops["install"][0][1]
        # Not the bundled path.
        assert "--no-index" not in pip_args

    def test_ignores_bundled_when_pypi_pin_set(
        self, _isolate_config, monkeypatch, stub_venv_ops
    ):
        bundle = self._populate_bundle(_isolate_config)
        monkeypatch.setattr(_r, "bundled_wheels_dir", lambda: bundle)
        # A PyPI pin counts as "user overrode the default" — bundled
        # version may not match the pin.
        cfg = _s.load()
        cfg.source.spec = "==1.4.9"
        _s.save(cfg)
        result = _r.first_install()
        assert result.ok
        cfg = _s.load()
        assert cfg.runtime.install_source == "pypi"

    def test_falls_through_to_pypi_when_no_bundle_present(
        self, _isolate_config, monkeypatch, stub_venv_ops
    ):
        monkeypatch.setattr(_r, "bundled_wheels_dir", lambda: None)
        result = _r.first_install()
        assert result.ok
        cfg = _s.load()
        assert cfg.runtime.install_source == "pypi"
        # No --no-index in the install args this time.
        pip_args = stub_venv_ops["install"][0][1]
        assert "--no-index" not in pip_args

    def test_bundled_failure_falls_through_to_pypi(self, _isolate_config, monkeypatch):
        bundle = self._populate_bundle(_isolate_config)
        monkeypatch.setattr(_r, "bundled_wheels_dir", lambda: bundle)
        install_calls: list[tuple] = []

        def fake_install(p, args):
            install_calls.append((p, args))
            # First call (bundled) blows up; second (PyPI) succeeds.
            if "--no-index" in args:
                raise _v.VenvOpError("bundled wheel corrupt")

        monkeypatch.setattr(
            _r, "create_venv", lambda p: Path(p).mkdir(parents=True, exist_ok=True)
        )
        monkeypatch.setattr(_r, "install_into", fake_install)
        monkeypatch.setattr(_r, "smoke_test", lambda p: "1.5.0")
        monkeypatch.setattr(_r, "write_wrapper_marker", lambda p: None)
        result = _r.first_install()
        assert result.ok, result.error
        cfg = _s.load()
        # Recovery path ran — install_source reflects what actually worked.
        assert cfg.runtime.install_source == "pypi"
        # Two install attempts: bundled then PyPI.
        assert len(install_calls) == 2
        assert "--no-index" in install_calls[0][1]
        assert "--no-index" not in install_calls[1][1]

    def test_bundled_failure_with_custom_source_does_not_fallthrough(
        self, _isolate_config, monkeypatch
    ):
        # User explicitly picked git — even though bundled is present
        # the runner never tries it, AND if git fails the runner does
        # NOT silently fall through to PyPI (would mask the user's
        # configured intent).
        bundle = self._populate_bundle(_isolate_config)
        monkeypatch.setattr(_r, "bundled_wheels_dir", lambda: bundle)
        cfg = _s.load()
        cfg.source.kind = "git"
        cfg.source.spec = "https://example.com/repo.git@main"
        _s.save(cfg)

        install_calls: list[tuple] = []

        def fake_install(p, args):
            install_calls.append((p, args))
            raise _v.VenvOpError("git install failed")

        monkeypatch.setattr(
            _r, "create_venv", lambda p: Path(p).mkdir(parents=True, exist_ok=True)
        )
        monkeypatch.setattr(_r, "install_into", fake_install)
        monkeypatch.setattr(_r, "smoke_test", lambda p: "1.5.0")
        monkeypatch.setattr(_r, "write_wrapper_marker", lambda p: None)
        result = _r.first_install()
        assert not result.ok
        # Only one attempt — the git install — no fallthrough.
        assert len(install_calls) == 1
        assert "git+" in install_calls[0][1][1]


class TestInstallSourcePersistenceOnUpdate:
    def test_run_update_records_install_source(self, _isolate_config, stub_venv_ops):
        _r.run_update()
        cfg = _s.load()
        assert cfg.runtime.install_source == "pypi"

    def test_reset_to_bundled_records_install_source(
        self, _isolate_config, monkeypatch, stub_venv_ops
    ):
        bundle = _isolate_config / "wheels-bundle"
        bundle.mkdir()
        (bundle / "kohakuterrarium-1.5.0-py3-none-any.whl").write_bytes(b"")
        # ``reset_to_bundled`` resolves bundled via ``resolve_pip_args``
        # which calls ``bundled_wheels_dir`` from sources.py — patch
        # THERE so the resolver finds the test bundle.
        monkeypatch.setattr(
            "kohakuterrarium.launcher.sources.bundled_wheels_dir",
            lambda: bundle,
        )
        result = _r.reset_to_bundled()
        assert result.ok, result.error
        cfg = _s.load()
        assert cfg.runtime.install_source == "bundled"


class TestRunUpdate:
    def test_happy_path_requires_restart(self, _isolate_config, stub_venv_ops):
        result = _r.run_update()
        assert result.ok
        assert result.restart_required is True
        assert len(stub_venv_ops["swap"]) == 1

    def test_smoke_failure_skips_swap(self, _isolate_config, monkeypatch):
        monkeypatch.setattr(
            _r, "create_venv", lambda p: Path(p).mkdir(parents=True, exist_ok=True)
        )
        monkeypatch.setattr(_r, "install_into", lambda p, args: None)
        monkeypatch.setattr(
            _r, "smoke_test", lambda p: (_ for _ in ()).throw(_v.VenvOpError("smoke!"))
        )
        swap_calls = []
        monkeypatch.setattr(
            _r, "atomic_swap", lambda c, n, b: swap_calls.append((c, n, b))
        )
        monkeypatch.setattr(_r, "write_wrapper_marker", lambda p: None)
        result = _r.run_update()
        assert not result.ok
        assert swap_calls == []  # swap NEVER fires when smoke fails


class TestMaybeUpdate:
    def test_manual_mode_is_noop(self, _isolate_config):
        cfg = _s.load()
        cfg.update.mode = "manual"
        _s.save(cfg)
        result = _r.maybe_update()
        assert result.ok
        assert result.skipped_reason == "manual"

    def test_notify_mode_is_noop(self, _isolate_config):
        # default is notify-on-launch
        result = _r.maybe_update()
        assert result.ok
        assert result.skipped_reason == "notify-only"

    def test_auto_mode_with_local_source_skips(self, _isolate_config):
        cfg = _s.load()
        cfg.update.mode = "auto-on-launch"
        cfg.source.kind = "local"
        cfg.source.spec = "/tmp/checkout"
        _s.save(cfg)
        result = _r.maybe_update()
        assert result.ok
        assert "auto-update disabled" in (result.skipped_reason or "")

    def test_auto_mode_with_pypi_runs_update(self, _isolate_config, stub_venv_ops):
        cfg = _s.load()
        cfg.update.mode = "auto-on-launch"
        _s.save(cfg)
        result = _r.maybe_update()
        assert result.ok
        # Used run_update -> primitives fired.
        assert len(stub_venv_ops["create"]) == 1
