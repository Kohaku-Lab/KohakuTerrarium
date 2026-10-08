"""Wiring a module file into creature configs, from both ends."""

from pathlib import Path

import pytest
from ruamel.yaml.comments import CommentedMap

from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.studio.catalog.packages_scan import invalidate_scan_caches
from kohakuterrarium.studio.editors import wiring
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace
from kohakuterrarium.studio.editors.yaml_creature import load_creature_file


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", tmp_path / "packages")
    monkeypatch.setattr(loc_mod, "PROJECT_DIR", None)
    invalidate_scan_caches()
    root = loc_mod.ensure_local_project()
    yield LocalWorkspace.open(root)
    invalidate_scan_caches()


class TestEntries:
    def test_each_kind_gets_its_config_shape(self):
        ref = "@/modules/x.py"
        assert wiring.wiring_entry("tools", "t", ref, class_name="T") == {
            "name": "t",
            "type": "custom",
            "module": ref,
            "class": "T",
        }
        assert wiring.wiring_entry("subagents", "s", ref, config_var="S_CONFIG") == {
            "name": "s",
            "type": "custom",
            "module": ref,
            "config": "S_CONFIG",
        }
        assert wiring.wiring_entry("inputs", "i", ref, class_name="I") == {
            "type": "custom",
            "module": ref,
            "class": "I",
        }

    def test_source_detection(self, tmp_path):
        src = "X = 1\nclass A:\n    pass\nCFG = mod.SubAgentConfig(name='a')\n"
        assert wiring.first_class_name(src) == "A"
        assert wiring.config_var_of(src) == "CFG"
        assert wiring.first_class_name("def (") is None
        path = tmp_path / "s.py"
        path.write_text(src, encoding="utf-8")
        assert wiring.detect_config_var(path) == "CFG"
        assert wiring.detect_config_var(tmp_path / "missing.py") is None


class TestPureEdits:
    def _setup(self, tmp_path):
        creature_dir = tmp_path / "creatures" / "c"
        creature_dir.mkdir(parents=True)
        target = tmp_path / "modules" / "tools" / "t.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1\n", encoding="utf-8")
        return creature_dir, target.resolve()

    def test_list_kinds_plug_once_and_match_any_spelling(self, tmp_path):
        creature_dir, target = self._setup(tmp_path)
        config = CommentedMap({"tools": [{"name": "read"}]})
        relative = {"name": "t", "type": "custom", "module": "../../modules/tools/t.py"}
        config["tools"].append(relative)
        assert wiring.uses(config, "tools", target, creature_dir)
        entry = {"name": "t", "type": "custom", "module": str(target), "class": "T"}
        assert not wiring.plug(config, "tools", "t", entry, target, creature_dir)
        assert wiring.unplug(config, "tools", target, creature_dir)
        assert config["tools"] == [{"name": "read"}]
        assert not wiring.unplug(config, "tools", target, creature_dir)
        assert wiring.plug(config, "tools", "t", entry, target, creature_dir)
        assert config["tools"][-1]["module"] == str(target)
        config["tools"] = None
        assert wiring.plug(config, "tools", "t", entry, target, creature_dir)

    def test_input_replaces_and_unplugs_back_to_the_default(self, tmp_path):
        creature_dir, target = self._setup(tmp_path)
        config = CommentedMap({"input": {"type": "cli"}})
        entry = {"type": "custom", "module": str(target), "class": "I"}
        assert wiring.plug(config, "inputs", "i", entry, target, creature_dir)
        assert config["input"]["class"] == "I"
        assert wiring.unplug(config, "inputs", target, creature_dir)
        assert "input" not in config
        assert not wiring.unplug(config, "inputs", target, creature_dir)

    def test_output_joins_named_outputs_and_unplugs_from_either_slot(self, tmp_path):
        creature_dir, target = self._setup(tmp_path)
        config = CommentedMap({"output": {"type": "stdout"}})
        entry = {"type": "custom", "module": str(target), "class": "O"}
        assert wiring.plug(config, "outputs", "log", entry, target, creature_dir)
        assert config["output"]["type"] == "stdout"
        assert config["output"]["named_outputs"]["log"]["class"] == "O"
        assert not wiring.plug(config, "outputs", "log", entry, target, creature_dir)
        assert wiring.unplug(config, "outputs", target, creature_dir)
        assert config["output"] == {"type": "stdout"}
        config["output"] = CommentedMap({**entry, "controller_direct": True})
        assert wiring.uses(config, "outputs", target, creature_dir)
        assert wiring.unplug(config, "outputs", target, creature_dir)
        assert config["output"] == {"controller_direct": True}
        empty = CommentedMap()
        assert wiring.plug(empty, "outputs", "log", entry, target, creature_dir)

    def test_package_typed_and_unresolvable_entries_never_match(self, tmp_path):
        creature_dir, target = self._setup(tmp_path)
        config = CommentedMap(
            {
                "plugins": [
                    {"name": "p", "type": "package", "module": str(target)},
                    {"name": "q", "type": "custom", "module": "@ghost/x.py"},
                    "bare-name",
                ]
            }
        )
        assert not wiring.uses(config, "plugins", target, creature_dir)
        assert not wiring.unplug(config, "plugins", target, creature_dir)


class TestWorkspace:
    def test_plug_from_the_module_end_and_read_back_from_both(self, project):
        project.scaffold_creature("alpha", None)
        project.scaffold_creature("beta", None)
        project.scaffold_module("tools", "echo", None, ["alpha"])
        info = project.module_wiring("tools", "echo")
        assert info == {
            "ref": "@/modules/tools/echo.py",
            "name": "echo",
            "entry": {
                "name": "echo",
                "type": "custom",
                "module": "@/modules/tools/echo.py",
                "class": "EchoTool",
            },
        }
        assert project.module_users("tools", "echo") == ["alpha"]
        config = project.load_creature("alpha")["config"]
        assert info["entry"] in config["tools"]

        assert project.plug_module("tools", "echo", ["alpha", "beta"]) == ["beta"]
        assert project.module_users("tools", "echo") == ["alpha", "beta"]
        assert project.unplug_module("tools", "echo", ["alpha"]) == ["alpha"]
        assert project.module_users("tools", "echo") == ["beta"]

    def test_comments_survive_a_plug(self, project):
        project.scaffold_creature("alpha", None)
        cfg = project.creatures_dir / "alpha" / "config.yaml"
        cfg.write_text(
            "# keep me\n" + cfg.read_text(encoding="utf-8"), encoding="utf-8"
        )
        project.scaffold_module("plugins", "guard", "tool_guard", ["alpha"])
        text = cfg.read_text(encoding="utf-8")
        assert text.startswith("# keep me\n")
        plugin = load_creature_file(cfg)["plugins"][0]
        assert plugin["class"] == "GuardPlugin" and plugin["name"] == "guard"

    def test_subagent_and_io_entries(self, project):
        project.scaffold_creature("alpha", None)
        project.scaffold_module("subagents", "look", None, ["alpha"])
        project.scaffold_module("outputs", "log", None, ["alpha"])
        project.scaffold_module("inputs", "inbox", None, ["alpha"])
        config = project.load_creature("alpha")["config"]
        assert config["subagents"][-1]["config"] == "LOOK_CONFIG"
        assert config["output"]["named_outputs"]["log"]["class"] == "LogOutput"
        assert config["input"]["class"] == "InboxInput"

    def test_unknown_creatures_are_refused_before_anything_is_written(self, project):
        project.scaffold_creature("alpha", None)
        with pytest.raises(FileNotFoundError, match="ghost"):
            project.scaffold_module("tools", "echo", None, ["alpha", "ghost"])
        assert not (project.modules_dir / "tools" / "echo.py").exists()
        project.scaffold_module("tools", "echo", None)
        with pytest.raises(FileNotFoundError, match="ghost"):
            project.plug_module("tools", "echo", ["ghost"])
        with pytest.raises(FileNotFoundError):
            project.module_users("tools", "nope")

    def test_a_plain_folder_wires_by_absolute_path(self, tmp_path, project):
        (tmp_path / "plain").mkdir()
        plain = LocalWorkspace.open(tmp_path / "plain")
        plain.scaffold_creature("c", None)
        plain.scaffold_module("tools", "t", None, ["c"])
        expected = str((plain.modules_dir / "tools" / "t.py").resolve())
        assert plain.module_wiring("tools", "t")["ref"] == expected
        assert Path(plain.load_creature("c")["config"]["tools"][0]["module"]) == Path(
            expected
        )
