from kohakuterrarium.builtins.tools.tree import TreeTool
from kohakuterrarium.modules.tool.base import ToolContext


class TestTreeToolRootLabel:
    async def test_scoped_rules_negation_and_opt_out(self, tmp_path):
        (tmp_path / ".gitignore").write_text(
            "/temp/\n*.log\n!keep.log\n", encoding="utf-8"
        )
        (tmp_path / "temp").mkdir()
        (tmp_path / "temp/bad.txt").write_text("x", encoding="utf-8")
        (tmp_path / "drop.log").write_text("x", encoding="utf-8")
        (tmp_path / "keep.log").write_text("x", encoding="utf-8")
        ctx = ToolContext(agent_name="agent", session=None, working_dir=tmp_path)
        filtered = await TreeTool().execute({}, context=ctx)
        assert filtered.success
        tree = filtered.output.split("\n(")[0]
        assert "keep.log" in tree
        assert "drop.log" not in tree
        assert "temp/" not in tree
        unfiltered = await TreeTool().execute({"gitignore": False}, context=ctx)
        assert unfiltered.success
        assert "bad.txt" in unfiltered.output
        assert "drop.log" in unfiltered.output

    async def test_dot_path_preserves_dot_root_label(self, tmp_path):
        (tmp_path / "child.txt").write_text("hello", encoding="utf-8")
        ctx = ToolContext(agent_name="agent", session=None, working_dir=tmp_path)

        result = await TreeTool().execute({"path": ".", "limit": 10}, context=ctx)

        assert result.success is True
        assert result.output.splitlines()[0] == "."
        assert "child.txt" in result.output

    async def test_relative_path_preserves_typed_root_label(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "note.md").write_text("---\nsummary: hello\n---\nbody", encoding="utf-8")
        ctx = ToolContext(agent_name="agent", session=None, working_dir=tmp_path)

        result = await TreeTool().execute({"path": "subdir", "limit": 10}, context=ctx)

        assert result.success is True
        assert result.output.splitlines()[0] == "subdir/"
        assert "note.md - hello" in result.output

    async def test_absolute_path_preserves_absolute_root_label(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
        ctx = ToolContext(agent_name="agent", session=None, working_dir=tmp_path)
        path_text = str(tmp_path)

        result = await TreeTool().execute({"path": path_text, "limit": 10}, context=ctx)

        assert result.success is True
        assert result.output.splitlines()[0] == path_text.rstrip("/") + "/"
        assert "file.txt" in result.output
