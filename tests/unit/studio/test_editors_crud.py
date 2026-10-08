"""Unit tests for studio.editors.{creatures_crud, modules_crud}."""

import pytest
import yaml

from kohakuterrarium.studio.editors import codegen_plugin
from kohakuterrarium.studio.editors import (
    creatures_crud as cc_mod,
    modules_crud as mc_mod,
)
from kohakuterrarium.studio.editors.starters import UnknownStarterError
from kohakuterrarium.studio.editors.yaml_creature import load_creature_file

# ── creatures_crud ──────────────────────────────────────────


class TestScaffoldCreature:
    def test_basic(self, tmp_path):
        out = cc_mod.scaffold_creature(tmp_path, "alice", base=None)
        assert out.is_dir()
        assert (out / "config.yaml").exists()
        assert (out / "prompts" / "system.md").exists()

    def test_existing_raises(self, tmp_path):
        (tmp_path / "dup").mkdir()
        with pytest.raises(FileExistsError):
            cc_mod.scaffold_creature(tmp_path, "dup", None)

    def test_a_starter_seeds_config_prompt_and_model(self, tmp_path):
        out = cc_mod.scaffold_creature(
            tmp_path,
            "dev",
            None,
            starter="coder",
            description="Writes code",
            purpose="Maintains the docs site.",
            model="claude-opus",
        )
        config = load_creature_file(out / "config.yaml")
        assert [t["name"] for t in config["tools"]] == [
            "read",
            "write",
            "edit",
            "glob",
            "grep",
            "bash",
        ]
        assert config["subagents"] == [{"name": "explore"}]
        assert config["controller"] == {
            "reasoning_effort": "high",
            "llm": "claude-opus",
        }
        assert config["description"] == "Writes code"
        prompt = (out / "prompts" / "system.md").read_text(encoding="utf-8")
        assert prompt.startswith("# dev\n") and "Maintains the docs site." in prompt

    def test_extending_inherits_instead_of_applying_the_default_starter(self, tmp_path):
        files = cc_mod.render_creature(
            "kid", "@kt-biome/creatures/general", purpose="Terse."
        )
        config = yaml.safe_load(files["config.yaml"])
        assert config["base_config"] == "@kt-biome/creatures/general"
        assert config["tools"] == [] and "llm" not in config["controller"]
        assert files["prompts/system.md"] == "# kid\n\nTerse.\n"

    def test_an_unknown_starter_writes_nothing(self, tmp_path):
        with pytest.raises(UnknownStarterError):
            cc_mod.scaffold_creature(tmp_path, "x", None, starter="nope")
        assert not (tmp_path / "x").exists()


class TestForkCreature:
    def _source(self, tmp_path):
        base = tmp_path / "pkg" / "creatures" / "general"
        base.mkdir(parents=True)
        (base / "config.yaml").write_text("name: general\n", encoding="utf-8")
        src = tmp_path / "pkg" / "creatures" / "swe"
        (src / "prompts").mkdir(parents=True)
        (src / "config.yaml").write_text(
            "# the swe creature\nname: swe\nbase_config: ../general\n", encoding="utf-8"
        )
        (src / "prompts" / "system.md").write_text("You code.", encoding="utf-8")
        (src / "__pycache__").mkdir()
        (src / "__pycache__" / "x.pyc").write_bytes(b"\0")
        return src, base

    def test_copies_renames_and_makes_a_relative_base_absolute(self, tmp_path):
        src, base = self._source(tmp_path)
        out = cc_mod.fork_creature(tmp_path / "ws" / "creatures", "mine", src)
        config = load_creature_file(out / "config.yaml")
        assert config["name"] == "mine"
        assert config["base_config"] == str(base.resolve())
        assert (out / "config.yaml").read_text(encoding="utf-8").startswith("# the swe")
        assert (out / "prompts" / "system.md").read_text(
            encoding="utf-8"
        ) == "You code."
        assert not (out / "__pycache__").exists()
        assert load_creature_file(src / "config.yaml")["name"] == "swe"

    def test_refuses_an_existing_name_a_missing_source_and_a_non_creature(
        self, tmp_path
    ):
        src, _ = self._source(tmp_path)
        creatures = tmp_path / "ws" / "creatures"
        (creatures / "taken").mkdir(parents=True)
        with pytest.raises(FileExistsError):
            cc_mod.fork_creature(creatures, "taken", src)
        with pytest.raises(FileNotFoundError):
            cc_mod.fork_creature(creatures, "a", tmp_path / "nowhere")
        (tmp_path / "empty").mkdir()
        with pytest.raises(FileNotFoundError, match="no config.yaml"):
            cc_mod.fork_creature(creatures, "b", tmp_path / "empty")
        assert not (creatures / "b").exists()


class TestSaveCreature:
    def test_writes_config_and_prompts(self, tmp_path):
        creature_dir = cc_mod.save_creature(
            tmp_path,
            "alice",
            {
                "config": {"name": "alice", "model": "m"},
                "prompts": {"system.md": "hello"},
            },
        )
        assert (creature_dir / "config.yaml").exists()
        assert (creature_dir / "system.md").read_text(encoding="utf-8") == "hello"

    def test_no_prompts(self, tmp_path):
        cc_mod.save_creature(tmp_path, "alice", {"config": {"name": "alice"}})
        assert (tmp_path / "alice" / "config.yaml").exists()

    def test_nested_prompts(self, tmp_path):
        creature_dir = cc_mod.save_creature(
            tmp_path,
            "alice",
            {"prompts": {"sub/nested.md": "x"}},
        )
        assert (creature_dir / "sub" / "nested.md").read_text() == "x"


class TestDeleteCreature:
    def test_unknown_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            cc_mod.delete_creature(tmp_path, "ghost")

    def test_removes_dir(self, tmp_path):
        (tmp_path / "alice").mkdir()
        (tmp_path / "alice" / "config.yaml").write_text("name: alice")
        cc_mod.delete_creature(tmp_path, "alice")
        assert not (tmp_path / "alice").exists()


class TestWritePrompt:
    def test_writes_file(self, tmp_path):
        cc_mod.write_prompt(tmp_path, "alice", "system.md", "content")
        assert (tmp_path / "alice" / "system.md").read_text() == "content"

    def test_creates_parents(self, tmp_path):
        cc_mod.write_prompt(tmp_path, "alice", "sub/nested.md", "x")
        assert (tmp_path / "alice" / "sub" / "nested.md").read_text() == "x"


# ── modules_crud ────────────────────────────────────────────


class TestScaffoldModule:
    def test_creates_file(self, tmp_path):
        path = mc_mod.scaffold_module(tmp_path, "tools", "my_tool", template=None)
        assert path == tmp_path / "my_tool.py"
        # Scaffolded file is a valid tool module parseable by codegen.
        from kohakuterrarium.studio.editors import codegen_tool

        back = codegen_tool.parse_back(path.read_text(encoding="utf-8"))
        assert back["mode"] == "simple"
        assert back["form"]["tool_name"] == "my_tool"

    def test_existing_raises(self, tmp_path):
        kd = tmp_path
        (kd / "dup.py").write_text("x")
        with pytest.raises(FileExistsError):
            mc_mod.scaffold_module(kd, "tools", "dup", None)

    def test_the_starter_decides_the_source_and_its_sidecars(self, tmp_path):
        path = mc_mod.scaffold_module(tmp_path, "plugins", "guard", "tool_guard")
        source = path.read_text(encoding="utf-8")
        assert "raise PluginBlockError" in source and "priority = 10" in source
        schema = (tmp_path / "guard.schema.json").read_text(encoding="utf-8")
        assert '"blocked_tools"' in schema

    def test_an_unknown_starter_writes_nothing(self, tmp_path):
        with pytest.raises(UnknownStarterError):
            mc_mod.scaffold_module(tmp_path / "tools", "tools", "t", "nope")
        assert not (tmp_path / "tools").exists()


class TestSaveModule:
    def test_raw_mode(self, tmp_path):
        out_path = mc_mod.save_module(
            "tools",
            "x",
            {"mode": "raw", "raw_source": "x = 1\n"},
            existing_path=None,
            fallback_path=tmp_path / "x.py",
        )
        assert out_path.read_text() == "x = 1\n"

    def test_raw_empty_raises(self, tmp_path):
        with pytest.raises(ValueError):
            mc_mod.save_module(
                "tools",
                "x",
                {"mode": "raw", "raw_source": ""},
                existing_path=None,
                fallback_path=tmp_path / "x.py",
            )

    def test_unknown_mode(self, tmp_path):
        with pytest.raises(ValueError):
            mc_mod.save_module(
                "tools",
                "x",
                {"mode": "garbage"},
                existing_path=None,
                fallback_path=tmp_path / "x.py",
            )

    def test_simple_new_file(self, tmp_path):
        out = mc_mod.save_module(
            "tools",
            "newt",
            {
                "mode": "simple",
                "form": {"tool_name": "newt", "description": "a new tool"},
                "execute_body": "return ToolResult(output='x')",
            },
            existing_path=None,
            fallback_path=tmp_path / "newt.py",
        )
        # The written file round-trips through the tool codegen parser.
        from kohakuterrarium.studio.editors import codegen_tool

        back = codegen_tool.parse_back(out.read_text())
        assert back["mode"] == "simple"
        assert back["form"]["tool_name"] == "newt"
        assert back["form"]["description"] == "a new tool"
        assert "return ToolResult(output='x')" in back["execute_body"]

    def test_simple_save_of_a_plugin_keeps_the_imports_its_hooks_use(self, tmp_path):
        path = mc_mod.scaffold_module(tmp_path, "plugins", "guard", "tool_guard")
        form = codegen_plugin.parse_back(path.read_text(encoding="utf-8"))["form"]
        mc_mod.save_module(
            "plugins",
            "guard",
            {"mode": "simple", "form": {**form, "priority": 5}},
            existing_path=path,
            fallback_path=path,
        )
        source = path.read_text(encoding="utf-8")
        assert "priority = 5" in source
        assert (
            "from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError"
            in source
        )
        assert source.count("import BasePlugin") == 1
        compile(source, "guard.py", "exec")

    def test_simple_new_output_is_an_output_module(self, tmp_path):
        out = mc_mod.save_module(
            "outputs",
            "log",
            {"mode": "simple", "form": {"description": "d"}, "execute_body": "pass"},
            existing_path=None,
            fallback_path=tmp_path / "log.py",
        )
        source = out.read_text(encoding="utf-8")
        assert "class LogOutput(BaseOutputModule)" in source
        assert "async def write(self, content: str)" in source
        assert "BaseInputModule" not in source

    def test_simple_existing_file(self, tmp_path):
        existing = tmp_path / "x.py"
        existing.write_text(
            "from kohakuterrarium.modules.tool.base import BaseTool\n"
            "class XTool(BaseTool):\n"
            "    @property\n"
            "    def tool_name(self) -> str:\n"
            "        return 'old'\n"
            "    async def _execute(self, args, context=None):\n"
            "        return None\n"
        )
        mc_mod.save_module(
            "tools",
            "x",
            {
                "mode": "simple",
                "form": {"tool_name": "new"},
                "execute_body": "return None",
            },
            existing_path=existing,
            fallback_path=existing,
        )
        # In-place update: tool_name patched, class identity preserved.
        from kohakuterrarium.studio.editors import codegen_tool

        back = codegen_tool.parse_back(existing.read_text())
        assert back["form"]["tool_name"] == "new"
        assert back["form"]["class_name"] == "XTool"


class TestSaveModuleDoc:
    def test_writes_sidecar(self, tmp_path):
        py = tmp_path / "x.py"
        py.write_text("x = 1")
        mc_mod.save_module_doc(py, "## Skill doc")
        # Sidecar is x.md.
        md = tmp_path / "x.md"
        assert md.exists() and "Skill doc" in md.read_text()


class TestDeleteModule:
    def test_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            mc_mod.delete_module("tools", "ghost", None)

    def test_deletes(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("x")
        mc_mod.delete_module("tools", "x", f)
        assert not f.exists()
