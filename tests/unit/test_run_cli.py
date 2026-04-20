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


def _parse_terminal_input(seq: str):
    """Feed ``seq`` through prompt_toolkit's vt100 parser and return the
    list of ``(key_enum_str, payload)`` tuples it produces."""
    from prompt_toolkit.input.vt100_parser import Vt100Parser, _Flush

    presses: list = []
    parser = Vt100Parser(lambda kp: presses.append(kp))
    parser.feed(seq)
    parser._input_parser.send(_Flush())  # drain any pending prefix match
    return [(str(kp.key), kp.data) for kp in presses]


def test_enhanced_keyboard_decodes_ctrl_letter_under_kitty_and_modifyother():
    """Issue #29: once ``\\x1b[>1u`` (Kitty keyboard) or ``\\x1b[>4;2m``
    (xterm modifyOtherKeys=2) is active, terminals emit Ctrl+letter as
    escape sequences prompt_toolkit's default table does NOT decode —
    which makes Ctrl+D (and every other Ctrl+letter binding) appear dead
    and leaks the raw bytes (``[100;5u``) into the TextArea.

    The composer module patches ``ANSI_SEQUENCES`` on import; verify the
    patch is in place and the vt100 parser now routes both encodings
    back to ``Keys.ControlX``.
    """
    from kohakuterrarium.builtins.cli_rich import composer  # noqa: F401

    # Spot-check the keys we actually bind in the composer.
    for letter, expected in [
        ("b", "Keys.ControlB"),
        ("c", "Keys.ControlC"),
        ("d", "Keys.ControlD"),
        ("j", "Keys.ControlJ"),
        ("l", "Keys.ControlL"),
        ("x", "Keys.ControlX"),
    ]:
        codepoint = ord(letter)
        kitty = f"\x1b[{codepoint};5u"
        modifyother = f"\x1b[27;5;{codepoint}~"
        assert _parse_terminal_input(kitty) == [
            (expected, kitty)
        ], f"Kitty CSI u for Ctrl+{letter} did not decode"
        assert _parse_terminal_input(modifyother) == [
            (expected, modifyother)
        ], f"modifyOtherKeys for Ctrl+{letter} did not decode"


def test_enhanced_keyboard_decodes_enter_tab_backspace_csi_u():
    """Kitty flag 1 optionally disambiguates Enter / Tab / Backspace too.
    These must decode to the same ``Keys.Control*`` values that the classic
    single-byte encoding produces, so that ``@kb.add("enter")`` etc.
    keep firing."""
    from kohakuterrarium.builtins.cli_rich import composer  # noqa: F401

    assert _parse_terminal_input("\x1b[13u") == [("Keys.ControlM", "\x1b[13u")]
    assert _parse_terminal_input("\x1b[9u") == [("Keys.ControlI", "\x1b[9u")]
    assert _parse_terminal_input("\x1b[127u") == [("Keys.ControlH", "\x1b[127u")]


def test_enhanced_keyboard_modifier_enter_still_proxied_to_f19_f20_f21():
    """Shift+Enter / Ctrl+Enter / Ctrl+Shift+Enter are proxied through
    F19/F20/F21 so the composer can treat them as "insert newline"
    without fighting prompt_toolkit's built-in ``ControlM`` handling."""
    from kohakuterrarium.builtins.cli_rich import composer  # noqa: F401

    # Kitty CSI u form
    assert _parse_terminal_input("\x1b[13;2u") == [("Keys.F19", "\x1b[13;2u")]
    assert _parse_terminal_input("\x1b[13;5u") == [("Keys.F20", "\x1b[13;5u")]
    assert _parse_terminal_input("\x1b[13;6u") == [("Keys.F21", "\x1b[13;6u")]
    # modifyOtherKeys=2 form
    assert _parse_terminal_input("\x1b[27;2;13~") == [("Keys.F19", "\x1b[27;2;13~")]
    assert _parse_terminal_input("\x1b[27;5;13~") == [("Keys.F20", "\x1b[27;5;13~")]
    assert _parse_terminal_input("\x1b[27;6;13~") == [("Keys.F21", "\x1b[27;6;13~")]


def test_enhanced_keyboard_classic_single_byte_encoding_still_works():
    """Regression guard — classic single-byte Ctrl+letter (``\\x04`` etc.)
    and plain ``\\r`` / ``\\t`` / ``\\x7f`` must continue to decode as
    before. Terminals without enhanced-keyboard support still use these.
    """
    from kohakuterrarium.builtins.cli_rich import composer  # noqa: F401

    assert _parse_terminal_input("\x04") == [("Keys.ControlD", "\x04")]
    assert _parse_terminal_input("\x03") == [("Keys.ControlC", "\x03")]
    assert _parse_terminal_input("\r") == [("Keys.ControlM", "\r")]
    assert _parse_terminal_input("\t") == [("Keys.ControlI", "\t")]
    assert _parse_terminal_input("\x7f") == [("Keys.ControlH", "\x7f")]


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
