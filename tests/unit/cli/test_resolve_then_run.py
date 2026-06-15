"""Unit tests for ``cli.run.resolve_then_run`` (the cli/tui front-door core).

The picker and the engine are stubbed (monkeypatched at module scope) so
these tests pin the *routing* contract: explicit path runs directly;
no-path runs the picker on a TTY; a cancelled picker is a clean no-op; a
non-TTY refuses instead of hanging.
"""

from kohakuterrarium.cli import run as runmod


class _FakeTTY:
    """Minimal file-like stand-in: controllable isatty, swallowing writes."""

    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty

    def write(self, *_args, **_kwargs) -> int:
        return 0

    def flush(self) -> None:
        pass


def _force_tty(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(runmod.sys, "stdin", _FakeTTY(value))
    monkeypatch.setattr(runmod.sys, "stdout", _FakeTTY(value))


class TestResolveThenRun:
    def test_explicit_path_runs_directly(self, monkeypatch):
        seen: dict = {}

        def fake_run(path, log_level, **kw):
            seen["path"] = path
            seen["log_level"] = log_level
            seen.update(kw)
            return 0

        monkeypatch.setattr(runmod, "run_agent_cli", fake_run)
        rc = runmod.resolve_then_run(
            "foo", io_mode="cli", llm="m", extra_creatures=["a"]
        )
        assert rc == 0
        assert seen["path"] == "foo"
        assert seen["io_mode"] == "cli"
        assert seen["llm"] == "m"
        assert seen["extra_creatures"] == ["a"]
        assert seen["extra_channels"] == []  # None coerced to []

    def test_picker_ref_is_run(self, monkeypatch):
        _force_tty(monkeypatch, True)
        monkeypatch.setattr(runmod, "pick_runnable", lambda io_mode: "@pkg/creatures/x")
        seen: dict = {}
        monkeypatch.setattr(
            runmod,
            "run_agent_cli",
            lambda path, log_level, **kw: seen.update(path=path, io=kw["io_mode"]) or 7,
        )
        rc = runmod.resolve_then_run(None, io_mode="tui")
        assert rc == 7
        assert seen["path"] == "@pkg/creatures/x"
        assert seen["io"] == "tui"

    def test_picker_cancel_is_clean_noop(self, monkeypatch):
        _force_tty(monkeypatch, True)
        monkeypatch.setattr(runmod, "pick_runnable", lambda io_mode: None)
        called: list = []
        monkeypatch.setattr(runmod, "run_agent_cli", lambda *a, **k: called.append(1))
        rc = runmod.resolve_then_run(None, io_mode="cli")
        assert rc == 0
        assert called == []

    def test_non_tty_refuses_without_picker_or_run(self, monkeypatch):
        _force_tty(monkeypatch, False)
        called: list = []
        monkeypatch.setattr(
            runmod, "pick_runnable", lambda io_mode: called.append("picked")
        )
        monkeypatch.setattr(
            runmod, "run_agent_cli", lambda *a, **k: called.append("ran")
        )
        rc = runmod.resolve_then_run(None, io_mode="cli")
        assert rc == 2
        assert called == []
