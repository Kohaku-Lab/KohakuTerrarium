"""Unit tests for studio.editors.{creatures_crud, modules_crud}."""

import pytest

from kohakuterrarium.studio.editors import (
    creatures_crud as cc_mod,
    modules_crud as mc_mod,
)

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
