"""Unit tests for :mod:`kohakuterrarium.studio.catalog.catalog_sources` and
:mod:`kohakuterrarium.studio.catalog.creatures`.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.catalog import (
    catalog_sources as cs_mod,
    creatures as creatures_mod,
)

# ── load_workspace_manifest ─────────────────────────────────


class TestLoadWorkspaceManifest:
    def test_none_workspace(self):
        assert cs_mod.load_workspace_manifest(None) == {}

    def test_workspace_no_root(self):
        ws = SimpleNamespace()
        assert cs_mod.load_workspace_manifest(ws) == {}

    def test_no_manifest_file(self, tmp_path):
        ws = SimpleNamespace(root_path=tmp_path)
        assert cs_mod.load_workspace_manifest(ws) == {}

    def test_loads_yaml(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text(
            "tools:\n  - name: bash\n    module: tools.bash\n"
        )
        ws = SimpleNamespace(root_path=tmp_path)
        out = cs_mod.load_workspace_manifest(ws)
        assert out == {"tools": [{"name": "bash", "module": "tools.bash"}]}

    def test_loads_kohaku_yml_fallback(self, tmp_path):
        (tmp_path / "kohaku.yml").write_text("tools: []")
        ws = SimpleNamespace(root_path=tmp_path)
        out = cs_mod.load_workspace_manifest(ws)
        assert out == {"tools": []}

    def test_yaml_parse_error_returns_empty(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text(":\n: not yaml")
        ws = SimpleNamespace(root_path=tmp_path)
        assert cs_mod.load_workspace_manifest(ws) == {}

    def test_empty_yaml_returns_empty(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text("")
        ws = SimpleNamespace(root_path=tmp_path)
        assert cs_mod.load_workspace_manifest(ws) == {}


# ── manifest_entry ──────────────────────────────────────────


class TestManifestEntry:
    def test_basic(self):
        raw = {"name": "x", "module": "m", "class": "C"}
        out = cs_mod.manifest_entry(
            raw, source="workspace-manifest", entry_type="package"
        )
        assert out == {
            "name": "x",
            "description": "",
            "source": "workspace-manifest",
            "type": "package",
            "module": "m",
            "class_name": "C",
        }

    def test_class_name_fallback(self):
        raw = {"name": "x", "class_name": "C"}
        out = cs_mod.manifest_entry(raw, source="x", entry_type="y")
        assert out["class_name"] == "C"

    def test_missing_fields_defaults(self):
        out = cs_mod.manifest_entry({}, source="x", entry_type="y")
        assert out["name"] == ""
        assert out["module"] is None


# ── classify_io ─────────────────────────────────────────────


class TestClassifyIo:
    def test_input_by_name(self):
        assert cs_mod.classify_io({"name": "stdin_input"}) == "input"

    def test_output_by_name(self):
        assert cs_mod.classify_io({"name": "stdout_output"}) == "output"

    def test_input_by_class(self):
        assert cs_mod.classify_io({"class": "MyInput"}) == "input"

    def test_output_by_class(self):
        assert cs_mod.classify_io({"class_name": "MyOutput"}) == "output"

    def test_unknown(self):
        assert cs_mod.classify_io({"name": "weird"}) == "unknown"


# ── workspace_manifest_entries ──────────────────────────────


class TestWorkspaceManifestEntries:
    def test_unknown_kind(self):
        ws = SimpleNamespace(root_path=None)
        assert cs_mod.workspace_manifest_entries(ws, "garbage") == []

    def test_tools(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text("tools:\n  - name: bash\n")
        ws = SimpleNamespace(root_path=tmp_path)
        out = cs_mod.workspace_manifest_entries(ws, "tools")
        assert out[0]["name"] == "bash"

    def test_non_dict_items_skipped(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text("tools:\n  - 'not-a-dict'\n  - name: x\n")
        ws = SimpleNamespace(root_path=tmp_path)
        out = cs_mod.workspace_manifest_entries(ws, "tools")
        assert len(out) == 1
        assert out[0]["name"] == "x"

    def test_inputs_filters_io(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text(
            "io:\n  - {name: my_input, class: MyInput}\n"
            "  - {name: my_output, class: MyOutput}\n"
        )
        ws = SimpleNamespace(root_path=tmp_path)
        out = cs_mod.workspace_manifest_entries(ws, "inputs")
        assert len(out) == 1
        assert out[0]["name"] == "my_input"

    def test_outputs_filters_io(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text(
            "io:\n  - {name: my_input, class: MyInput}\n"
            "  - {name: my_output, class: MyOutput}\n"
        )
        ws = SimpleNamespace(root_path=tmp_path)
        out = cs_mod.workspace_manifest_entries(ws, "outputs")
        assert len(out) == 1
        assert out[0]["name"] == "my_output"


# ── package_entries ─────────────────────────────────────────


class TestPackageEntries:
    def test_unknown_kind(self):
        assert cs_mod.package_entries("garbage") == []

    def test_list_packages_failure_returns_empty(self, monkeypatch):
        def boom():
            raise RuntimeError("bad")

        monkeypatch.setattr(cs_mod, "list_packages", boom)
        assert cs_mod.package_entries("tools") == []

    def test_basic(self, monkeypatch):
        monkeypatch.setattr(
            cs_mod,
            "list_packages",
            lambda: [{"name": "pkg", "tools": [{"name": "bash"}]}],
        )
        out = cs_mod.package_entries("tools")
        assert out[0]["source"] == "package:pkg"

    def test_skip_non_dict_items(self, monkeypatch):
        monkeypatch.setattr(
            cs_mod,
            "list_packages",
            lambda: [{"name": "pkg", "tools": ["not-a-dict", {"name": "x"}]}],
        )
        out = cs_mod.package_entries("tools")
        assert len(out) == 1

    def test_inputs_filters(self, monkeypatch):
        monkeypatch.setattr(
            cs_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg",
                    "io": [
                        {"name": "x_input"},
                        {"name": "x_output"},
                    ],
                }
            ],
        )
        out = cs_mod.package_entries("inputs")
        assert len(out) == 1
        assert "input" in out[0]["name"]


# ── dedupe_preserve_order ───────────────────────────────────


class TestDedupePreserveOrder:
    def test_empty(self):
        assert cs_mod.dedupe_preserve_order([]) == []

    def test_dedups_by_name(self):
        out = cs_mod.dedupe_preserve_order(
            [
                {"name": "a", "source": "1"},
                {"name": "a", "source": "2"},
                {"name": "b", "source": "3"},
            ]
        )
        assert len(out) == 2
        assert out[0]["source"] == "1"

    def test_skips_empty_names(self):
        out = cs_mod.dedupe_preserve_order(
            [
                {"name": "", "source": "1"},
                {"name": "x"},
            ]
        )
        assert len(out) == 1


# ── catalog.creatures ───────────────────────────────────────


class TestCreaturesPassthrough:
    def test_list_creatures_returns_workspace_listing(self):
        listing = [{"name": "alice"}, {"name": "bob"}]
        ws = SimpleNamespace(list_creatures=lambda: listing)
        # The helper forwards the workspace's listing unchanged.
        assert creatures_mod.list_creatures(ws) == listing

    def test_load_creature_delegates(self):
        ws = SimpleNamespace(load_creature=lambda name: {"name": name})
        out = creatures_mod.load_creature(ws, "alice")
        assert out == {"name": "alice"}


class TestReadPrompt:
    def test_unknown_creature_raises(self, tmp_path):
        ws = SimpleNamespace(creatures_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            creatures_mod.read_prompt(ws, "ghost", "system.md")

    def test_unknown_prompt_raises(self, tmp_path):
        cdir = tmp_path / "alice"
        cdir.mkdir()
        ws = SimpleNamespace(creatures_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            creatures_mod.read_prompt(ws, "alice", "ghost.md")

    def test_reads_existing(self, tmp_path):
        cdir = tmp_path / "alice"
        cdir.mkdir()
        (cdir / "system.md").write_text("hello")
        ws = SimpleNamespace(creatures_dir=tmp_path)
        out = creatures_mod.read_prompt(ws, "alice", "system.md")
        assert out == "hello"
