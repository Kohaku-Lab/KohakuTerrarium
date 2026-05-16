"""Unit tests for :mod:`kohakuterrarium.studio.editors.workspace_manifest`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.editors import workspace_manifest as wm

# ── compute_effective ───────────────────────────────────────


class TestComputeEffective:
    def test_load_failure_returns_error(self, monkeypatch, tmp_path):
        def boom(p):
            raise RuntimeError("bad")

        monkeypatch.setattr(wm, "load_agent_config", boom)
        out = wm.compute_effective(tmp_path / "x", {})
        assert "error" in out

    def test_chain_with_base_config(self, monkeypatch, tmp_path):
        cfg = SimpleNamespace(
            model="m",
            llm_profile="",
            tools=[],
            subagents=[],
        )
        monkeypatch.setattr(wm, "load_agent_config", lambda p: cfg)
        out = wm.compute_effective(tmp_path / "x", {"base_config": "@pkg/x"})
        assert out["model"] == "m"
        assert out["inheritance_chain"] == ["@pkg/x"]

    def test_chain_with_seen_break(self, monkeypatch, tmp_path):
        cfg = SimpleNamespace(model="", llm_profile="default", tools=[], subagents=[])
        monkeypatch.setattr(wm, "load_agent_config", lambda p: cfg)
        out = wm.compute_effective(tmp_path / "x", {})
        assert out["inheritance_chain"] == []

    def test_chain_with_tools_subagents(self, monkeypatch, tmp_path):
        tool = SimpleNamespace(name="bash")
        sa = SimpleNamespace(name="explorer")
        cfg = SimpleNamespace(
            model="m",
            llm_profile="",
            tools=[tool],
            subagents=[sa],
        )
        monkeypatch.setattr(wm, "load_agent_config", lambda p: cfg)
        out = wm.compute_effective(tmp_path, {})
        assert out["tools"] == ["bash"]
        assert out["subagents"] == ["explorer"]


# ── sidecar helpers ─────────────────────────────────────────


class TestLoadSidecarDoc:
    def test_missing(self, tmp_path):
        py = tmp_path / "x.py"
        py.write_text("x = 1")
        out = wm.load_sidecar_doc(py, tmp_path)
        assert out["exists"] is False
        assert out["content"] == ""

    def test_existing(self, tmp_path):
        py = tmp_path / "x.py"
        py.write_text("x = 1")
        sidecar = tmp_path / "x.md"
        sidecar.write_text("doc")
        out = wm.load_sidecar_doc(py, tmp_path)
        assert out["exists"]
        assert out["content"] == "doc"


class TestSaveSidecarDoc:
    def test_writes_file(self, tmp_path):
        py = tmp_path / "x.py"
        wm.save_sidecar_doc(py, "doc text")
        assert (tmp_path / "x.md").read_text() == "doc text"


class TestReadSidecarSchema:
    def test_missing(self, tmp_path):
        py = tmp_path / "x.py"
        assert wm.read_sidecar_schema(py) is None

    def test_invalid_json(self, tmp_path):
        py = tmp_path / "x.py"
        sidecar = tmp_path / "x.schema.json"
        sidecar.write_text("not json")
        assert wm.read_sidecar_schema(py) is None

    def test_not_list(self, tmp_path):
        py = tmp_path / "x.py"
        sidecar = tmp_path / "x.schema.json"
        sidecar.write_text('{"not": "list"}')
        assert wm.read_sidecar_schema(py) is None

    def test_valid_list(self, tmp_path):
        py = tmp_path / "x.py"
        sidecar = tmp_path / "x.schema.json"
        sidecar.write_text('[{"name": "k"}]')
        out = wm.read_sidecar_schema(py)
        assert out == [{"name": "k"}]


class TestWriteCodegenSidecars:
    def test_no_writer(self, tmp_path):
        # cg has no sidecar_files attr → silent no-op.
        cg = SimpleNamespace()
        wm.write_codegen_sidecars(cg, {}, tmp_path / "x.py")

    def test_writer_returns_dict(self, tmp_path):
        cg = SimpleNamespace(sidecar_files=lambda f: {".schema.json": "[]\n"})
        wm.write_codegen_sidecars(cg, {}, tmp_path / "x.py")
        assert (tmp_path / "x.schema.json").read_text() == "[]\n"

    def test_writer_raises_silent(self, tmp_path):
        def boom(f):
            raise RuntimeError("bad")

        cg = SimpleNamespace(sidecar_files=boom)
        # Should not raise.
        wm.write_codegen_sidecars(cg, {}, tmp_path / "x.py")

    def test_non_dict_return_ignored(self, tmp_path):
        cg = SimpleNamespace(sidecar_files=lambda f: "not-a-dict")
        wm.write_codegen_sidecars(cg, {}, tmp_path / "x.py")

    def test_non_string_content_skipped(self, tmp_path):
        cg = SimpleNamespace(sidecar_files=lambda f: {".x": 42})
        wm.write_codegen_sidecars(cg, {}, tmp_path / "y.py")
        assert not (tmp_path / "y.x").exists()

    def test_plain_suffix(self, tmp_path):
        cg = SimpleNamespace(sidecar_files=lambda f: {"extra": "content"})
        wm.write_codegen_sidecars(cg, {}, tmp_path / "y.py")
        assert (tmp_path / "y.extra").read_text() == "content"


# ── sync_manifest_entry ─────────────────────────────────────


class TestSyncManifestEntry:
    def test_unknown_kind_raises(self, tmp_path):
        py = tmp_path / "modules" / "tools" / "x.py"
        py.parent.mkdir(parents=True)
        py.write_text("class XTool: pass\n")
        with pytest.raises(ValueError):
            wm.sync_manifest_entry(
                tmp_path,
                "ghost-kind",
                "x",
                py,
                ("tools", "subagents"),
            )

    def test_creates_minimal_manifest(self, tmp_path):
        py = tmp_path / "modules" / "tools" / "my_tool.py"
        py.parent.mkdir(parents=True)
        py.write_text("class MyTool:\n    pass\n")
        out = wm.sync_manifest_entry(
            tmp_path,
            "tools",
            "my_tool",
            py,
            ("tools", "subagents", "inputs", "outputs", "plugins", "triggers"),
        )
        assert out["added"] is True
        # Manifest now exists with seeded fields.
        manifest = tmp_path / "kohaku.yaml"
        assert manifest.exists()
        content = manifest.read_text()
        assert "my_tool" in content

    def test_yml_fallback(self, tmp_path):
        py = tmp_path / "modules" / "tools" / "x.py"
        py.parent.mkdir(parents=True)
        py.write_text("class XTool: pass\n")
        (tmp_path / "kohaku.yml").write_text("name: ws\nversion: 1\n")
        out = wm.sync_manifest_entry(
            tmp_path,
            "tools",
            "x",
            py,
            ("tools",),
        )
        assert out["added"] is True

    def test_idempotent_existing_entry(self, tmp_path):
        py = tmp_path / "modules" / "tools" / "x.py"
        py.parent.mkdir(parents=True)
        py.write_text("class XTool: pass\n")
        # First call creates.
        wm.sync_manifest_entry(tmp_path, "tools", "x", py, ("tools",))
        # Second call is idempotent.
        out = wm.sync_manifest_entry(tmp_path, "tools", "x", py, ("tools",))
        assert out["added"] is False


# ── module_dotted_path ──────────────────────────────────────


class TestModuleDottedPath:
    def test_basic(self, tmp_path):
        py = tmp_path / "modules" / "tools" / "my_tool.py"
        py.parent.mkdir(parents=True)
        py.write_text("")
        out = wm.module_dotted_path(tmp_path, py)
        assert out == "modules.tools.my_tool"

    def test_top_level(self, tmp_path):
        py = tmp_path / "tool.py"
        py.write_text("")
        out = wm.module_dotted_path(tmp_path, py)
        assert out == "tool"


# ── resolve_manifest_path ───────────────────────────────────


class TestResolveManifestPath:
    def test_empty_module(self, tmp_path):
        assert wm.resolve_manifest_path(tmp_path, "") is None

    def test_none_module(self, tmp_path):
        assert wm.resolve_manifest_path(tmp_path, None) is None

    def test_non_string(self, tmp_path):
        assert wm.resolve_manifest_path(tmp_path, 123) is None

    def test_missing_file(self, tmp_path):
        assert wm.resolve_manifest_path(tmp_path, "modules.tools.x") is None

    def test_outside_root(self, tmp_path):
        # An absolute path resolving outside tmp_path.
        out = wm.resolve_manifest_path(tmp_path, "../escape")
        assert out is None

    def test_valid_inside_root(self, tmp_path):
        py = tmp_path / "modules" / "tools" / "x.py"
        py.parent.mkdir(parents=True)
        py.write_text("class XTool: pass\n")
        out = wm.resolve_manifest_path(tmp_path, "modules.tools.x")
        assert out is not None


# ── find_module_file ────────────────────────────────────────


class TestFindModuleFile:
    def test_workspace_file(self, tmp_path):
        kd = tmp_path / "tools"
        kd.mkdir()
        (kd / "x.py").write_text("")
        ws = SimpleNamespace(root_path=tmp_path)
        out = wm.find_module_file(tmp_path, kd, "tools", "x", ws)
        assert out == (kd / "x.py").resolve() or out == kd / "x.py"

    def test_subagent_yaml_fallback(self, tmp_path):
        kd = tmp_path / "subagents"
        kd.mkdir()
        (kd / "x.yaml").write_text("name: x")
        ws = SimpleNamespace(root_path=tmp_path)
        out = wm.find_module_file(tmp_path, kd, "subagents", "x", ws)
        assert out is not None
        assert out.suffix == ".yaml"

    def test_via_manifest_entry(self, tmp_path):
        # No direct file in kind_dir; entry points to a real .py.
        kd = tmp_path / "tools"
        kd.mkdir()
        target = tmp_path / "modules" / "tools" / "y.py"
        target.parent.mkdir(parents=True)
        target.write_text("class YTool: pass\n")
        (tmp_path / "kohaku.yaml").write_text(
            "name: ws\nversion: 1\n" "tools:\n  - {name: y, module: modules.tools.y}\n"
        )
        ws = SimpleNamespace(root_path=tmp_path)
        out = wm.find_module_file(tmp_path, kd, "tools", "y", ws)
        assert out is not None

    def test_unknown_kind(self, tmp_path):
        kd = tmp_path / "tools"
        kd.mkdir()
        ws = SimpleNamespace(root_path=tmp_path)
        out = wm.find_module_file(tmp_path, kd, "ghost-kind", "x", ws)
        assert out is None

    def test_io_classification_filter(self, tmp_path):
        kd = tmp_path / "io"
        kd.mkdir()
        target = tmp_path / "modules" / "io" / "z.py"
        target.parent.mkdir(parents=True)
        target.write_text("class ZInput: pass\n")
        (tmp_path / "kohaku.yaml").write_text(
            "name: ws\nversion: 1\n"
            "io:\n  - {name: z, class: ZInput, module: modules.io.z}\n"
        )
        ws = SimpleNamespace(root_path=tmp_path)
        # Lookup as input → matches.
        out = wm.find_module_file(tmp_path, kd, "inputs", "z", ws)
        assert out is not None
        # Lookup as output → doesn't match (class ends in Input).
        out2 = wm.find_module_file(tmp_path, kd, "outputs", "z", ws)
        assert out2 is None


# ── modules_summary ─────────────────────────────────────────


class TestModulesSummary:
    def test_workspace_only(self, tmp_path):
        ws = SimpleNamespace(root_path=tmp_path)
        files = [{"name": "x", "path": "x.py"}]
        out = wm.modules_summary(ws, "tools", files)
        assert any(e["source"] == "workspace" for e in out)
        assert all(
            e.get("editable", False) for e in out if e.get("source") == "workspace"
        )

    def test_with_manifest_entries(self, tmp_path):
        target = tmp_path / "modules" / "tools" / "y.py"
        target.parent.mkdir(parents=True)
        target.write_text("class YTool: pass\n")
        (tmp_path / "kohaku.yaml").write_text(
            "name: ws\nversion: 1\n" "tools:\n  - {name: y, module: modules.tools.y}\n"
        )
        ws = SimpleNamespace(root_path=tmp_path)
        out = wm.modules_summary(ws, "tools", [])
        # Manifest entry should be editable (resolved inside root).
        assert any(e["name"] == "y" for e in out)


# ── detect_class_name ───────────────────────────────────────


class TestDetectClassName:
    def test_subagents_returns_none(self, tmp_path):
        py = tmp_path / "x.py"
        py.write_text("class SubAgentConfig: pass\n")
        assert wm.detect_class_name(py, "subagents") is None

    def test_parse_error(self, tmp_path):
        py = tmp_path / "x.py"
        py.write_text("def broken(:\n")
        assert wm.detect_class_name(py, "tools") is None

    def test_finds_class(self, tmp_path):
        py = tmp_path / "x.py"
        py.write_text("import os\nclass MyTool:\n    pass\n")
        assert wm.detect_class_name(py, "tools") == "MyTool"

    def test_no_class(self, tmp_path):
        py = tmp_path / "x.py"
        py.write_text("x = 1\n")
        assert wm.detect_class_name(py, "tools") is None


# ── Workspace Protocol ─────────────────────────────────────


class TestWorkspaceProtocol:
    def test_runtime_checkable(self):
        # Build a class that conforms.
        class _Ws:
            root = "/tmp"

            def list_creatures(self): ...
            def load_creature(self, n): ...
            def save_creature(self, n, d): ...
            def scaffold_creature(self, n, b): ...
            def delete_creature(self, n): ...
            def list_modules(self, k): ...
            def load_module(self, k, n): ...
            def save_module(self, k, n, d): ...
            def scaffold_module(self, k, n, t): ...
            def delete_module(self, k, n): ...
            def read_prompt(self, c, r): ...
            def write_prompt(self, c, r, b): ...

        assert isinstance(_Ws(), wm.Workspace)
