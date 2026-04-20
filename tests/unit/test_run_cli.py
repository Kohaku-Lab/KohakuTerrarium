import types

from prompt_toolkit.history import FileHistory


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


def test_rich_cli_enter_persists_submission_to_history(tmp_path, monkeypatch):
    """Issue #28: submissions must be appended to FileHistory so Up/Down can
    recall them later. The previous `_enter` handler called `buf.reset()`
    without `append_to_history=True`, so nothing was ever persisted."""
    from kohakuterrarium.builtins.cli_rich import composer as composer_mod

    monkeypatch.setattr(composer_mod, "HISTORY_DIR", tmp_path)

    submitted: list[str] = []
    composer = composer_mod.Composer(
        creature_name="test-creature",
        on_submit=submitted.append,
    )

    buf = composer.text_area.buffer
    buf.text = "first command"

    enter_binding = next(
        b for b in composer.key_bindings.bindings if b.handler.__name__ == "_enter"
    )

    class _Event:
        def __init__(self, current_buffer):
            self.current_buffer = current_buffer

    enter_binding.handler(_Event(buf))

    # Submission was forwarded and buffer cleared
    assert submitted == ["first command"]
    assert buf.text == ""

    # And — the critical assertion — it was persisted to history on disk
    history_file = tmp_path / "test-creature.txt"
    assert history_file.exists()
    persisted = FileHistory(str(history_file)).load_history_strings()
    assert list(persisted) == ["first command"]


async def test_rich_cli_enter_does_not_persist_line_continuation(tmp_path, monkeypatch):
    """A trailing backslash extends the input to a new line — that partial
    draft must NOT be appended to history. (Async because prompt_toolkit's
    Buffer.insert_text schedules a completer task on the running loop.)"""
    from kohakuterrarium.builtins.cli_rich import composer as composer_mod

    monkeypatch.setattr(composer_mod, "HISTORY_DIR", tmp_path)

    composer = composer_mod.Composer(creature_name="test-creature")
    buf = composer.text_area.buffer
    buf.text = "line1\\"
    buf.cursor_position = len(buf.text)

    enter_binding = next(
        b for b in composer.key_bindings.bindings if b.handler.__name__ == "_enter"
    )

    class _Event:
        def __init__(self, current_buffer):
            self.current_buffer = current_buffer

    enter_binding.handler(_Event(buf))

    # Backslash dropped, newline inserted — draft lives on, nothing in history
    assert buf.text == "line1\n"
    history_file = tmp_path / "test-creature.txt"
    if history_file.exists():
        assert list(FileHistory(str(history_file)).load_history_strings()) == []
