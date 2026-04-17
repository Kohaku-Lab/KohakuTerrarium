import types
from pathlib import Path

import pytest


class _DummyVault:
    def __init__(self, *args, **kwargs):
        pass

    def enable_auto_pack(self):
        pass

    def enable_cache(self, *args, **kwargs):
        pass

    def flush_cache(self):
        pass

    def insert(self, *args, **kwargs):
        pass

    def keys(self, *args, **kwargs):
        return []

    def __setitem__(self, key, value):
        pass

    def __getitem__(self, key):
        raise KeyError(key)


import sys

sys.modules.setdefault(
    "html2text",
    types.SimpleNamespace(HTML2Text=object, html2text=lambda text: text),
)
sys.modules.setdefault(
    "kohakuvault",
    types.SimpleNamespace(
        KVault=_DummyVault, TextVault=_DummyVault, VectorKVault=_DummyVault
    ),
)

from kohakuterrarium.cli import run as run_cli
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig


class _DummyAgent:
    def __init__(self, config=None):
        self.config = config or AgentConfig(name="demo")
        self.output_router = types.SimpleNamespace(default_output=None)

    async def start(self):
        return None

    async def stop(self):
        return None

    def run(self):
        return None


def _make_config(*, input_type="cli", output_type="stdout"):
    return AgentConfig(
        name="demo",
        input=InputConfig(
            type=input_type, module="pkg.input", class_name="CustomInput"
        ),
        output=OutputConfig(
            type=output_type,
            module="pkg.output",
            class_name="CustomOutput",
        ),
    )


def test_no_mode_preserves_configured_io(monkeypatch, tmp_path):
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("name: demo\n")

    config = _make_config(input_type="custom", output_type="custom")
    captured = {}

    monkeypatch.setattr(run_cli, "load_agent_config", lambda _path: config)

    def fake_from_path(path, llm_override=None, **kwargs):
        captured["kwargs"] = kwargs
        return _DummyAgent(config=config)

    monkeypatch.setattr(run_cli.Agent, "from_path", fake_from_path)
    monkeypatch.setattr(run_cli.asyncio, "run", lambda coro: None)

    rc = run_cli.run_agent_cli(str(config_dir), log_level="INFO", io_mode=None)
    assert rc == 0
    assert captured["kwargs"] == {}


def test_explicit_mode_warns_and_overrides_custom_io(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("name: demo\n")

    config = _make_config(input_type="custom", output_type="custom")
    captured = {}

    monkeypatch.setattr(run_cli, "load_agent_config", lambda _path: config)

    def fake_from_path(path, llm_override=None, **kwargs):
        captured["kwargs"] = kwargs
        return _DummyAgent(config=config)

    monkeypatch.setattr(run_cli.Agent, "from_path", fake_from_path)
    monkeypatch.setattr(
        run_cli, "_create_io_modules", lambda mode: (f"input:{mode}", f"output:{mode}")
    )
    monkeypatch.setattr(run_cli.asyncio, "run", lambda coro: None)

    rc = run_cli.run_agent_cli(str(config_dir), log_level="INFO", io_mode="plain")
    assert rc == 0
    assert captured["kwargs"] == {
        "input_module": "input:plain",
        "output_module": "output:plain",
    }
    out = capsys.readouterr().out
    assert "Warning: --mode plain overrides configured custom I/O" in out


def test_should_log_to_stderr_auto_off_for_cli_io():
    assert run_cli._should_log_to_stderr("auto", "cli", "stdout") is False
    assert run_cli._should_log_to_stderr("auto", "custom", "tui") is False


def test_should_log_to_stderr_auto_on_for_non_terminal_io():
    assert run_cli._should_log_to_stderr("auto", "custom", "stdout") is True
    assert run_cli._should_log_to_stderr("auto", "package", "plain") is True


def test_should_log_to_stderr_flag_overrides():
    assert run_cli._should_log_to_stderr("on", "cli", "tui") is True
    assert run_cli._should_log_to_stderr("off", "custom", "stdout") is False


def test_resolve_effective_io_respects_explicit_mode():
    config = _make_config(input_type="custom", output_type="custom")
    assert run_cli._resolve_effective_io(config, "plain") == ("plain", "plain")
    assert run_cli._resolve_effective_io(config, None) == ("custom", "custom")


def test_run_agent_cli_enables_stderr_for_custom_io(monkeypatch, tmp_path):
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("name: demo\n")

    config = _make_config(input_type="custom", output_type="stdout")
    called = {}

    monkeypatch.setattr(run_cli, "load_agent_config", lambda _path: config)
    monkeypatch.setattr(
        run_cli.Agent,
        "from_path",
        lambda path, llm_override=None, **kwargs: _DummyAgent(config=config),
    )
    monkeypatch.setattr(run_cli.asyncio, "run", lambda coro: None)
    monkeypatch.setattr(
        run_cli,
        "enable_stderr_logging",
        lambda level: called.setdefault("level", level),
    )

    rc = run_cli.run_agent_cli(
        str(config_dir), log_level="DEBUG", io_mode=None, log_stderr="auto"
    )
    assert rc == 0
    assert called == {"level": "DEBUG"}


def test_run_agent_cli_skips_stderr_when_off(monkeypatch, tmp_path):
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("name: demo\n")

    config = _make_config(input_type="custom", output_type="stdout")
    called = {}

    monkeypatch.setattr(run_cli, "load_agent_config", lambda _path: config)
    monkeypatch.setattr(
        run_cli.Agent,
        "from_path",
        lambda path, llm_override=None, **kwargs: _DummyAgent(config=config),
    )
    monkeypatch.setattr(run_cli.asyncio, "run", lambda coro: None)
    monkeypatch.setattr(
        run_cli,
        "enable_stderr_logging",
        lambda level: called.setdefault("level", level),
    )

    rc = run_cli.run_agent_cli(
        str(config_dir), log_level="DEBUG", io_mode=None, log_stderr="off"
    )
    assert rc == 0
    assert called == {}


def test_explicit_mode_without_custom_io_does_not_warn(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("name: demo\n")

    config = _make_config(input_type="cli", output_type="stdout")

    monkeypatch.setattr(run_cli, "load_agent_config", lambda _path: config)
    monkeypatch.setattr(
        run_cli.Agent,
        "from_path",
        lambda path, llm_override=None, **kwargs: _DummyAgent(config=config),
    )
    monkeypatch.setattr(
        run_cli, "_create_io_modules", lambda mode: (f"input:{mode}", f"output:{mode}")
    )
    monkeypatch.setattr(run_cli.asyncio, "run", lambda coro: None)

    rc = run_cli.run_agent_cli(str(config_dir), log_level="INFO", io_mode="plain")
    assert rc == 0
    assert "Warning:" not in capsys.readouterr().out
