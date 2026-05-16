"""Unit tests for studio.editors.{workspace_fs, yaml_creature}."""

import pytest
from ruamel.yaml.comments import CommentedMap

from kohakuterrarium.studio.editors import (
    workspace_fs as wfs_mod,
    yaml_creature as yc_mod,
)

# ── yaml_creature ───────────────────────────────────────────


class TestLoadCreatureFile:
    def test_basic(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("name: alice\nmodel: m\n")
        out = yc_mod.load_creature_file(path)
        assert out["name"] == "alice"

    def test_empty_file_returns_dict(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("")
        out = yc_mod.load_creature_file(path)
        assert out == {}


class TestSaveCreatureFile:
    def test_writes(self, tmp_path):
        path = tmp_path / "c.yaml"
        yc_mod.save_creature_file(path, {"name": "alice"})
        assert path.exists()
        assert "alice" in path.read_text()


class TestSaveCreatureMerged:
    def test_new_file(self, tmp_path):
        path = tmp_path / "new.yaml"
        yc_mod.save_creature_merged(path, {"name": "x"})
        assert path.exists()
        assert "name: x" in path.read_text()

    def test_merge_into_existing(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("name: alice\nmodel: m\n")
        yc_mod.save_creature_merged(path, {"description": "new"})
        text = path.read_text()
        assert "alice" in text
        assert "new" in text

    def test_empty_yaml_file(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("")
        yc_mod.save_creature_merged(path, {"name": "x"})
        assert "x" in path.read_text()

    def test_nested_merge(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("name: alice\ntools:\n  - bash\noptions:\n  k: 1\n")
        yc_mod.save_creature_merged(path, {"options": {"new": 2}})
        text = path.read_text()
        assert "k: 1" in text  # preserved
        assert "new" in text  # added


class TestDeepMerge:
    def test_target_not_mapping(self):
        target = "string"
        # No-op for non-mapping target.
        yc_mod._deep_merge(target, {"k": 1})

    def test_lists_replaced(self):
        target = [1, 2, 3]
        yc_mod._deep_merge(target, [4, 5])
        assert target == [4, 5]


# ── workspace_fs ────────────────────────────────────────────


class TestLocalWorkspaceOpen:
    def test_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            wfs_mod.LocalWorkspace.open(tmp_path / "ghost")

    def test_not_directory_raises(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        with pytest.raises(NotADirectoryError):
            wfs_mod.LocalWorkspace.open(f)

    def test_valid(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        assert ws.root == str(tmp_path)
        assert ws.creatures_dir == tmp_path / "creatures"
        assert ws.modules_dir == tmp_path / "modules"


class TestModuleKindDir:
    def test_known(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        assert ws.module_kind_dir("tools") == tmp_path / "modules" / "tools"

    def test_unknown_raises(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(ValueError):
            ws.module_kind_dir("garbage")


class TestSummary:
    def test_basic(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.summary()
        assert out["root"] == str(tmp_path)
        assert "modules" in out


class TestListCreatures:
    def test_no_dir(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        assert ws.list_creatures() == []

    def test_skips_files(self, tmp_path):
        (tmp_path / "creatures").mkdir()
        (tmp_path / "creatures" / "f.txt").write_text("x")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        assert ws.list_creatures() == []

    def test_skips_dirs_without_config(self, tmp_path):
        (tmp_path / "creatures" / "lonely").mkdir(parents=True)
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        assert ws.list_creatures() == []

    def test_lists_valid(self, tmp_path):
        c = tmp_path / "creatures" / "alice"
        c.mkdir(parents=True)
        (c / "config.yaml").write_text("name: alice\ndescription: d")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.list_creatures()
        assert out[0]["name"] == "alice"
        assert out[0]["description"] == "d"

    def test_parse_failure_recorded(self, tmp_path):
        c = tmp_path / "creatures" / "alice"
        c.mkdir(parents=True)
        (c / "config.yaml").write_text(":\n: bad yaml")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.list_creatures()
        assert "error" in out[0]


class TestLoadCreature:
    def test_missing_raises(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.load_creature("ghost")

    def test_full(self, tmp_path, monkeypatch):
        c = tmp_path / "creatures" / "alice"
        c.mkdir(parents=True)
        (c / "config.yaml").write_text("name: alice\nmodel: m")
        prompts = c / "prompts"
        prompts.mkdir()
        (prompts / "system.md").write_text("system prompt")

        # Stub compute_effective.
        monkeypatch.setattr(wfs_mod, "compute_effective", lambda p, d: {"model": "m"})
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.load_creature("alice")
        assert out["name"] == "alice"
        assert "system prompt" in out["prompts"].get("prompts/system.md", "")


class TestScaffoldCreature:
    def test_scaffolds_and_loads(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wfs_mod, "compute_effective", lambda p, d: {})
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.scaffold_creature("alice", base=None)
        assert out["name"] == "alice"


class TestSaveCreature:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wfs_mod, "compute_effective", lambda p, d: {})
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.save_creature("alice", {"config": {"name": "alice"}})
        assert out["name"] == "alice"


class TestDeleteCreature:
    def test_unknown_raises(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.delete_creature("ghost")


class TestReadPrompt:
    def test_missing_creature(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.read_prompt("ghost", "system.md")

    def test_missing_file(self, tmp_path):
        (tmp_path / "creatures" / "alice").mkdir(parents=True)
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.read_prompt("alice", "system.md")

    def test_reads(self, tmp_path):
        c = tmp_path / "creatures" / "alice"
        c.mkdir(parents=True)
        (c / "system.md").write_text("hello")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        assert ws.read_prompt("alice", "system.md") == "hello"


class TestWritePrompt:
    def test_writes(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        ws.write_prompt("alice", "system.md", "content")
        assert (tmp_path / "creatures" / "alice" / "system.md").read_text() == "content"


class TestListModules:
    def test_no_dir(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        assert ws.list_modules("tools") == []

    def test_lists_py_yaml_yml(self, tmp_path):
        kd = tmp_path / "modules" / "tools"
        kd.mkdir(parents=True)
        (kd / "a.py").write_text("")
        (kd / "b.yaml").write_text("")
        (kd / "c.yml").write_text("")
        (kd / "skip.txt").write_text("")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        names = sorted(m["name"] for m in ws.list_modules("tools"))
        assert names == ["a", "b", "c"]


class TestLoadModule:
    def test_missing(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.load_module("tools", "ghost")

    def test_plugins_uses_sidecar(self, tmp_path, monkeypatch):
        kd = tmp_path / "modules" / "plugins"
        kd.mkdir(parents=True)
        (kd / "p.py").write_text("class P: pass\n")
        sidecar = [{"name": "k"}]
        monkeypatch.setattr(wfs_mod, "read_sidecar_schema", lambda p: sidecar)
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.load_module("plugins", "p")
        assert out["name"] == "p"

    def test_tools_no_sidecar(self, tmp_path):
        kd = tmp_path / "modules" / "tools"
        kd.mkdir(parents=True)
        (kd / "t.py").write_text("class TTool: pass\n")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.load_module("tools", "t")
        assert out["name"] == "t"
        assert out["kind"] == "tools"


class TestScaffoldModule:
    def test_creates_and_loads(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.scaffold_module("tools", "newt", None)
        assert out["name"] == "newt"


class TestSaveAndDeleteModule:
    def test_save_then_delete(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        # First scaffold to create the file.
        ws.scaffold_module("tools", "modx", None)
        # Save with simple mode → just touches the file.
        out = ws.save_module(
            "tools",
            "modx",
            {
                "mode": "simple",
                "form": {"tool_name": "modx", "description": "d"},
                "execute_body": "return None",
            },
        )
        assert out["name"] == "modx"
        ws.delete_module("tools", "modx")
        with pytest.raises(FileNotFoundError):
            ws.load_module("tools", "modx")


class TestLoadAndSaveModuleDoc:
    def test_missing_module_raises(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.load_module_doc("tools", "ghost")
        with pytest.raises(FileNotFoundError):
            ws.save_module_doc("tools", "ghost", "doc")

    def test_round_trip(self, tmp_path):
        kd = tmp_path / "modules" / "tools"
        kd.mkdir(parents=True)
        (kd / "t.py").write_text("class TTool: pass\n")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        ws.save_module_doc("tools", "t", "## Doc")
        out = ws.load_module_doc("tools", "t")
        assert "## Doc" in out["content"]


class TestSyncManifest:
    def test_missing_raises(self, tmp_path):
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.sync_manifest("tools", "ghost")

    def test_basic(self, tmp_path):
        kd = tmp_path / "modules" / "tools"
        kd.mkdir(parents=True)
        (kd / "t.py").write_text("class TTool: pass\n")
        ws = wfs_mod.LocalWorkspace.open(tmp_path)
        out = ws.sync_manifest("tools", "t")
        assert out["added"] is True


# ── _coerce_plain / _collect_prompts ───────────────────────


class TestCoercePlain:
    def test_dict(self):
        cm = CommentedMap([("k", "v")])
        assert wfs_mod._coerce_plain(cm) == {"k": "v"}

    def test_list(self):
        assert wfs_mod._coerce_plain([1, 2, 3]) == [1, 2, 3]

    def test_scalar_passthrough(self):
        assert wfs_mod._coerce_plain(42) == 42


class TestCollectPrompts:
    def test_no_prompts_dir(self, tmp_path):
        c = tmp_path / "creature"
        c.mkdir()
        assert wfs_mod._collect_prompts(c) == {}

    def test_collects_md_txt(self, tmp_path):
        c = tmp_path / "creature"
        c.mkdir()
        p = c / "prompts"
        p.mkdir()
        (p / "system.md").write_text("system")
        (p / "user.txt").write_text("user")
        (p / "skip.bin").write_bytes(b"x")
        out = wfs_mod._collect_prompts(c)
        assert "prompts/system.md" in out
        assert "prompts/user.txt" in out
        assert "prompts/skip.bin" not in out


class TestRmtree:
    def test_removes_tree(self, tmp_path):
        d = tmp_path / "to-remove"
        d.mkdir()
        (d / "f.txt").write_text("x")
        wfs_mod._rmtree(d)
        assert not d.exists()


# ── _find_config_file ──────────────────────────────────────


class TestFindConfigFile:
    def test_yaml(self, tmp_path):
        (tmp_path / "config.yaml").write_text("name: x")
        out = wfs_mod._find_config_file(tmp_path)
        assert out.name == "config.yaml"

    def test_yml(self, tmp_path):
        (tmp_path / "config.yml").write_text("name: x")
        out = wfs_mod._find_config_file(tmp_path)
        assert out.name == "config.yml"

    def test_neither(self, tmp_path):
        assert wfs_mod._find_config_file(tmp_path) is None
