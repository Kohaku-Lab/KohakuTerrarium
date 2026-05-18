"""Unit tests for :mod:`kohakuterrarium.bootstrap.io`."""

import textwrap


from kohakuterrarium.bootstrap import io as io_mod
from kohakuterrarium.bootstrap.io import create_input, create_output
from kohakuterrarium.builtins.inputs.cli import CLIInput
from kohakuterrarium.builtins.outputs.stdout import StdoutOutput
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    OutputConfigItem,
)
from kohakuterrarium.core.loader import ModuleLoader

# ── create_input ─────────────────────────────────────────────────


class TestCreateInputOverride:
    def test_explicit_override_returned(self):
        override = CLIInput(prompt="x")
        cfg = AgentConfig(name="a", input=InputConfig(type="cli"))
        out = create_input(cfg, override, loader=None)
        assert out is override


class TestCreateInputBuiltin:
    def test_cli_input_built(self):
        cfg = AgentConfig(name="a", input=InputConfig(type="cli", prompt="$ "))
        out = create_input(cfg, None, loader=None)
        assert isinstance(out, CLIInput)

    def test_none_input(self):
        from kohakuterrarium.builtins.inputs.none import NoneInput

        cfg = AgentConfig(name="a", input=InputConfig(type="none"))
        out = create_input(cfg, None, loader=None)
        assert isinstance(out, NoneInput)

    def test_builtin_failure_falls_back_to_cli(self, monkeypatch):
        # Force the builtin factory to raise.
        from kohakuterrarium.bootstrap import io as bio

        def boom(input_type, options):
            raise RuntimeError("builtin failed")

        monkeypatch.setattr(bio, "create_builtin_input", boom)
        cfg = AgentConfig(name="a", input=InputConfig(type="cli"))
        out = create_input(cfg, None, loader=None)
        assert isinstance(out, CLIInput)


class TestCreateInputCustom:
    def test_missing_module_falls_back(self, tmp_path):
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(
            name="a",
            input=InputConfig(type="custom", module=None, class_name="X"),
        )
        out = create_input(cfg, None, loader)
        assert isinstance(out, CLIInput)

    def test_no_loader_falls_back(self):
        cfg = AgentConfig(
            name="a",
            input=InputConfig(type="custom", module="m.py", class_name="X"),
        )
        out = create_input(cfg, None, loader=None)
        assert isinstance(out, CLIInput)

    def test_load_failure_falls_back(self, tmp_path):
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(
            name="a",
            input=InputConfig(type="custom", module="missing.py", class_name="X"),
        )
        out = create_input(cfg, None, loader)
        assert isinstance(out, CLIInput)


class TestCreateInputPackageBareName:
    def test_unknown_falls_back(self, monkeypatch):
        monkeypatch.setattr(io_mod, "resolve_package_io", lambda name: None)
        cfg = AgentConfig(name="a", input=InputConfig(type="unknown_io_xyz"))
        out = create_input(cfg, None, loader=None)
        assert isinstance(out, CLIInput)

    def test_resolved_but_no_loader(self, monkeypatch):
        monkeypatch.setattr(
            io_mod, "resolve_package_io", lambda name: ("m.module", "X")
        )
        cfg = AgentConfig(name="a", input=InputConfig(type="some_pkg_input"))
        out = create_input(cfg, None, loader=None)
        assert isinstance(out, CLIInput)


# ── create_output ────────────────────────────────────────────────


class TestCreateOutputOverride:
    def test_override_used(self):
        out_override = StdoutOutput()
        cfg = AgentConfig(name="a", output=OutputConfig(type="stdout"))
        default_out, named = create_output(cfg, out_override, loader=None)
        assert default_out is out_override


class TestCreateOutputBuiltin:
    def test_stdout(self):
        cfg = AgentConfig(name="a", output=OutputConfig(type="stdout"))
        default_out, named = create_output(cfg, None, loader=None)
        assert isinstance(default_out, StdoutOutput)
        assert named == {}

    def test_builtin_failure_falls_back(self, monkeypatch):
        from kohakuterrarium.bootstrap import io as bio

        def boom(t, o):
            raise RuntimeError("builtin failed")

        monkeypatch.setattr(bio, "create_builtin_output", boom)
        cfg = AgentConfig(name="a", output=OutputConfig(type="stdout"))
        default_out, _ = create_output(cfg, None, loader=None)
        # Falls back to a fresh StdoutOutput.
        assert isinstance(default_out, StdoutOutput)


class TestCreateOutputCustom:
    def test_missing_module_falls_back(self):
        cfg = AgentConfig(
            name="a",
            output=OutputConfig(type="custom", module=None, class_name="X"),
        )
        default_out, _ = create_output(cfg, None, loader=None)
        assert isinstance(default_out, StdoutOutput)

    def test_load_failure_falls_back(self, tmp_path):
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(
            name="a",
            output=OutputConfig(type="custom", module="missing.py", class_name="X"),
        )
        default_out, _ = create_output(cfg, None, loader)
        assert isinstance(default_out, StdoutOutput)


class TestCreateOutputNamedOutputs:
    def test_named_outputs_resolved(self):
        cfg = AgentConfig(
            name="a",
            output=OutputConfig(
                type="stdout",
                named_outputs={
                    "discord": OutputConfigItem(type="stdout"),
                },
            ),
        )
        default_out, named = create_output(cfg, None, loader=None)
        # The named output is resolved into a real StdoutOutput instance,
        # distinct from the default output.
        assert isinstance(named["discord"], StdoutOutput)
        assert named["discord"] is not default_out


class TestCreateOutputPackageBareName:
    def test_unknown_falls_back(self, monkeypatch):
        monkeypatch.setattr(io_mod, "resolve_package_io", lambda name: None)
        cfg = AgentConfig(name="a", output=OutputConfig(type="ghost_out"))
        default_out, _ = create_output(cfg, None, loader=None)
        assert isinstance(default_out, StdoutOutput)


class TestCreateInputTuiSessionKey:
    def test_tui_input_gets_session_key_from_config(self, monkeypatch):
        # TUI input must receive the agent's session key in its options.
        captured = {}

        def fake_builtin(input_type, options):
            captured.update(options)
            return CLIInput(prompt="x")

        monkeypatch.setattr(io_mod, "create_builtin_input", fake_builtin)
        monkeypatch.setattr(io_mod, "is_builtin_input", lambda t: t == "tui")
        cfg = AgentConfig(
            name="agentX",
            input=InputConfig(type="tui"),
            session_key="sess-9",
        )
        create_input(cfg, None, loader=None)
        assert captured["session_key"] == "sess-9"


class TestCreateInputPackageLoad:
    def test_packaged_input_loaded_via_loader(self, tmp_path, monkeypatch):
        # A package input is referenced by importable module path. Drop a
        # real module on sys.path and resolve to it.
        import sys

        mod_dir = tmp_path / "pkgsrc"
        mod_dir.mkdir()
        (mod_dir / "my_pkg_input.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.input.base import BaseInputModule

                class PkgInput(BaseInputModule):
                    def __init__(self, **kw):
                        super().__init__()

                    async def get_input(self):
                        return None
                """))
        monkeypatch.syspath_prepend(str(mod_dir))
        monkeypatch.setattr(
            io_mod,
            "resolve_package_io",
            lambda name: ("my_pkg_input", "PkgInput"),
        )
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(name="a", input=InputConfig(type="discord_input"))
        out = create_input(cfg, None, loader)
        sys.modules.pop("my_pkg_input", None)
        # The package-resolved class was loaded, not the CLI fallback.
        assert type(out).__name__ == "PkgInput"

    def test_packaged_input_load_failure_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            io_mod, "resolve_package_io", lambda name: ("missing/mod.py", "X")
        )
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(name="a", input=InputConfig(type="discord_input"))
        out = create_input(cfg, None, loader)
        assert isinstance(out, CLIInput)


class TestCreateOutputTuiSessionKey:
    def test_tui_output_gets_session_key(self, monkeypatch):
        captured = {}

        def fake_builtin(output_type, options):
            captured.update(options)
            return StdoutOutput()

        monkeypatch.setattr(io_mod, "create_builtin_output", fake_builtin)
        monkeypatch.setattr(io_mod, "is_builtin_output", lambda t: t == "tui")
        cfg = AgentConfig(
            name="a", output=OutputConfig(type="tui"), session_key="s-out"
        )
        create_output(cfg, None, loader=None)
        assert captured["session_key"] == "s-out"


class TestCreateOutputCustomNoLoader:
    def test_custom_output_no_loader_falls_back(self):
        cfg = AgentConfig(
            name="a",
            output=OutputConfig(type="custom", module="m.py", class_name="X"),
        )
        # module+class present but no loader → stdout fallback.
        default_out, _ = create_output(cfg, None, loader=None)
        assert isinstance(default_out, StdoutOutput)


class TestCreateOutputPackageLoad:
    def test_packaged_output_loaded_via_loader(self, tmp_path, monkeypatch):
        import sys

        mod_dir = tmp_path / "pkgsrc"
        mod_dir.mkdir()
        (mod_dir / "my_pkg_output.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.output.base import BaseOutputModule

                class PkgOut(BaseOutputModule):
                    def __init__(self, **kw):
                        super().__init__()

                    async def write(self, content, **kw):
                        return None
                """))
        monkeypatch.syspath_prepend(str(mod_dir))
        monkeypatch.setattr(
            io_mod,
            "resolve_package_io",
            lambda name: ("my_pkg_output", "PkgOut"),
        )
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(name="a", output=OutputConfig(type="discord_output"))
        default_out, _ = create_output(cfg, None, loader)
        sys.modules.pop("my_pkg_output", None)
        assert type(default_out).__name__ == "PkgOut"

    def test_packaged_output_no_loader_falls_back(self, monkeypatch):
        monkeypatch.setattr(io_mod, "resolve_package_io", lambda name: ("m.mod", "X"))
        cfg = AgentConfig(name="a", output=OutputConfig(type="discord_output"))
        default_out, _ = create_output(cfg, None, loader=None)
        assert isinstance(default_out, StdoutOutput)

    def test_packaged_output_load_failure_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            io_mod, "resolve_package_io", lambda name: ("missing/mod.py", "X")
        )
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(name="a", output=OutputConfig(type="discord_output"))
        default_out, _ = create_output(cfg, None, loader)
        assert isinstance(default_out, StdoutOutput)


class TestCreateInputCustomLoadSuccess:
    def test_loads_custom_input(self, tmp_path):
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "myinput.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.input.base import BaseInputModule

                class MyInput(BaseInputModule):
                    def __init__(self, **kw):
                        super().__init__()

                    async def get_input(self):
                        return None
                """))
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = AgentConfig(
            name="a",
            input=InputConfig(
                type="custom",
                module="custom/myinput.py",
                class_name="MyInput",
            ),
            agent_path=tmp_path,
        )
        out = create_input(cfg, None, loader)
        # The custom class was loaded — not the CLIInput fallback.
        assert type(out).__name__ == "MyInput"
        assert not isinstance(out, CLIInput)
