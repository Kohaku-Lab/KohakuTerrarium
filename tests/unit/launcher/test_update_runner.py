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
