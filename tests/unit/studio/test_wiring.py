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

    def test_the_summary_carries_each_own_modules_users(self, project):
        project.scaffold_creature("alpha", None)
        project.scaffold_creature("beta", None)
        project.scaffold_module("tools", "echo", None, ["alpha", "beta"])
        project.scaffold_module("plugins", "guard", None)
        modules = project.summary()["modules"]
        echo = next(m for m in modules["tools"] if m["name"] == "echo")
        guard = next(m for m in modules["plugins"] if m["name"] == "guard")
        assert echo["users"] == ["alpha", "beta"]
        assert guard["users"] == []
        assert all(
            "users" not in m for m in modules["tools"] if m.get("source") != "workspace"
        )

    def test_a_package_style_workspace_lists_its_manifest_modules_and_terrariums(
        self, tmp_path
    ):
        root = tmp_path / "pkg"
        (root / "pkg_mod" / "plugins").mkdir(parents=True)
        (root / "pkg_mod" / "io").mkdir(parents=True)
        (root / "pkg_mod" / "plugins" / "guard.py").write_text(
            "from kohakuterrarium.modules.plugin.base import BasePlugin\n"
            "class GuardPlugin(BasePlugin):\n    name = 'guard'\n",
            encoding="utf-8",
        )
        (root / "pkg_mod" / "io" / "chat.py").write_text(
            "class ChatInput:\n    pass\nclass ChatOutput:\n    pass\n",
            encoding="utf-8",
        )
        (root / "kohaku.yaml").write_text(
            "name: pkg\n"
            "plugins:\n  - {name: guard, module: pkg_mod.plugins.guard, class: GuardPlugin}\n"
            "io:\n"
            "  - {name: chat_input, module: pkg_mod.io.chat, class: ChatInput}\n"
            "  - {name: chat_output, module: pkg_mod.io.chat, class: ChatOutput}\n"
            "terrariums:\n  - {name: team, path: terrariums/team, description: Two of them}\n",
            encoding="utf-8",
        )
        with (root / "kohaku.yaml").open("a", encoding="utf-8") as f:
            f.write(
                "creatures:\n  - {name: named, path: creatures/named, description: Named one}\n"
            )
        for name, plugins in {
            "dotted": "  - {name: guard, type: package, module: pkg_mod.plugins.guard}\n",
            "named": "  - name: guard\n",
            "builtin": "  - {name: guard, type: builtin}\n",
        }.items():
            (root / "creatures" / name).mkdir(parents=True)
            (root / "creatures" / name / "config.yaml").write_text(
                f"name: {name}\nplugins:\n{plugins}", encoding="utf-8"
            )
        (root / "terrariums" / "team").mkdir(parents=True)
        (root / "terrariums" / "team" / "terrarium.yaml").write_text(
            "terrarium:\n  name: team\n  creatures:\n    - {name: a}\n    - {name: b}\n",
            encoding="utf-8",
        )
        ws = LocalWorkspace.open(root)
        summary = ws.summary()

        described = {c["name"]: c["description"] for c in summary["creatures"]}
        assert described == {"builtin": "", "dotted": "", "named": "Named one"}
        [guard] = [m for m in summary["modules"]["plugins"] if m["name"] == "guard"]
        assert guard["source"] == "workspace-manifest" and guard["editable"] is True
        assert guard["users"] == ["dotted", "named"]
        assert summary["terrariums"] == [
            {
                "name": "team",
                "path": str((root / "terrariums" / "team").resolve()),
                "ref": str((root / "terrariums" / "team").resolve()),
                "description": "Two of them",
                "creatures": 2,
            }
        ]
        out = ws.module_wiring("outputs", "chat_output")
        assert out["entry"]["class"] == "ChatOutput" and out["name"] == "chat_output"

        assert ws.unplug_module("plugins", "guard", ["dotted", "named", "builtin"]) == [
            "dotted",
            "named",
        ]
        builtin = ws.load_creature("builtin")["config"]["plugins"]
        assert builtin == [{"name": "guard", "type": "builtin"}]
        assert ws.module_users("plugins", "guard") == []
        assert ws.plug_module("plugins", "guard", ["dotted"]) == ["dotted"]
        assert ws.module_users("plugins", "guard") == ["dotted"]

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
