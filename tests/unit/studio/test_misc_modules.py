"""Unit tests for the remaining small studio modules.

modules.py, catalog/packages_remote.py, editors/codegen_init.py,
editors/codegen_pending.py, attach/workspace_watch.py.
"""

import asyncio

import pytest

from kohakuterrarium.studio.attach import workspace_watch as ww_mod
from kohakuterrarium.studio.catalog import (
    modules as catalog_modules,
    packages_remote as remote_mod,
)
from kohakuterrarium.studio.editors import (
    codegen_init,
    codegen_pending,
)

# ── catalog/modules.py ──────────────────────────────────────


class TestCatalogModules:
    """Drive the real ``LocalWorkspace`` so the read-side primitives
    are exercised against actual on-disk module files."""

    def test_list_modules_returns_workspace_modules(self, tmp_path):
        from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace

        kd = tmp_path / "modules" / "tools"
        kd.mkdir(parents=True)
        (kd / "mytool.py").write_text("class MyTool: pass\n")
        ws = LocalWorkspace.open(tmp_path)
        out = catalog_modules.list_modules(ws, "tools")
        assert [m["name"] for m in out] == ["mytool"]

    def test_load_module_returns_parsed_envelope(self, tmp_path):
        from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace

        kd = tmp_path / "modules" / "tools"
        kd.mkdir(parents=True)
        (kd / "mytool.py").write_text("class MyTool: pass\n")
        ws = LocalWorkspace.open(tmp_path)
        out = catalog_modules.load_module(ws, "tools", "mytool")
        assert out["name"] == "mytool"
        assert out["kind"] == "tools"

    def test_load_module_doc_returns_sidecar_envelope(self, tmp_path):
        from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace

        kd = tmp_path / "modules" / "tools"
        kd.mkdir(parents=True)
        (kd / "mytool.py").write_text("class MyTool: pass\n")
        ws = LocalWorkspace.open(tmp_path)
        ws.save_module_doc("tools", "mytool", "## How to use")
        out = catalog_modules.load_module_doc(ws, "tools", "mytool")
        assert "## How to use" in out["content"]


# ── catalog/packages_remote.py ──────────────────────────────


class TestPackagesRemote:
    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        # Point _REGISTRY_JSON at a non-existent file.
        monkeypatch.setattr(remote_mod, "_REGISTRY_JSON", tmp_path / "missing.json")
        assert remote_mod.load_remote_registry() == {"repos": []}

    def test_valid_json(self, monkeypatch, tmp_path):
        f = tmp_path / "registry.json"
        f.write_text('{"repos": [{"name": "demo"}]}')
        monkeypatch.setattr(remote_mod, "_REGISTRY_JSON", f)
        out = remote_mod.load_remote_registry()
        assert out == {"repos": [{"name": "demo"}]}

    def test_unreadable_returns_empty(self, monkeypatch, tmp_path):
        f = tmp_path / "registry.json"
        f.write_text("not-json")
        monkeypatch.setattr(remote_mod, "_REGISTRY_JSON", f)
        assert remote_mod.load_remote_registry() == {"repos": []}


# ── editors/codegen_init.py ─────────────────────────────────


class TestCodegenInit:
    # Each kind must resolve to a codegen module exposing the codegen
    # API the editors layer calls (render_new / parse_back / ...).
    _API = ("render_new", "parse_back", "update_existing")

    @pytest.mark.parametrize(
        "kind",
        ["tools", "subagents", "plugins", "triggers", "inputs", "outputs"],
    )
    def test_get_codegen_returns_module_with_api(self, kind):
        cg = codegen_init.get_codegen(kind)
        for attr in self._API:
            assert callable(getattr(cg, attr)), f"{kind} missing {attr}"

    def test_each_kind_resolves_to_a_distinct_module(self):
        mods = {
            k: codegen_init.get_codegen(k) for k in ("tools", "subagents", "plugins")
        }
        # tools / subagents / plugins are different code generators.
        assert len({id(m) for m in mods.values()}) == 3

    def test_get_codegen_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown module kind"):
            codegen_init.get_codegen("garbage")


# ── editors/codegen_pending.py ──────────────────────────────


class TestCodegenPending:
    def test_render_new_stub(self):
        out = codegen_pending.render_new_stub({"name": "demo"})
        assert "demo" in out

    def test_render_new_stub_with_header(self):
        out = codegen_pending.render_new_stub(
            {"name": "x"}, header_comment="custom header"
        )
        assert "custom header" in out

    def test_update_existing_stub(self):
        src = "original = 1\n"
        out = codegen_pending.update_existing_stub(src, {}, "")
        assert out == src

    def test_parse_back_stub(self):
        out = codegen_pending.parse_back_stub("x = 1\n")
        assert out["mode"] == "raw"
        assert out["warnings"][0]["code"] == "codegen_pending"


# ── attach/workspace_watch.py ───────────────────────────────


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class TestWorkspaceWatch:
    async def test_non_directory(self, tmp_path):
        ws = _FakeWebSocket()
        await ww_mod.watch_directory(str(tmp_path / "ghost"), ws)
        assert any(s["type"] == "error" for s in ws.sent)

    async def test_watchfiles_missing(self, monkeypatch, tmp_path):
        # Force the import to fail.
        import builtins as builtins_mod

        original_import = builtins_mod.__import__

        def _fake_import(name, *a, **kw):
            if name == "watchfiles":
                raise ImportError("no watchfiles")
            return original_import(name, *a, **kw)

        monkeypatch.setattr(builtins_mod, "__import__", _fake_import)
        ws = _FakeWebSocket()
        await ww_mod.watch_directory(str(tmp_path), ws)
        assert any("watchfiles" in s.get("text", "") for s in ws.sent)

    async def test_watches_and_sends_ready(self, monkeypatch, tmp_path):
        # Stub awatch to yield once then exit.
        async def _awatch(root, **kw):
            yield [(1, str(tmp_path / "x.txt"))]

        # Inject a fake watchfiles module into sys.modules.
        import sys
        import types

        fake_module = types.ModuleType("watchfiles")
        fake_module.awatch = _awatch
        monkeypatch.setitem(sys.modules, "watchfiles", fake_module)

        ws = _FakeWebSocket()
        await ww_mod.watch_directory(str(tmp_path), ws)
        # ready sent.
        assert any(s["type"] == "ready" for s in ws.sent)
        # change frame sent with the file action.
        assert any(s["type"] == "change" for s in ws.sent)

    async def test_skips_hidden_and_build_dirs(self, monkeypatch, tmp_path):
        async def _awatch(root, **kw):
            yield [
                (1, str(tmp_path / ".git" / "HEAD")),
                (1, str(tmp_path / "__pycache__" / "x.pyc")),
                (1, str(tmp_path / "node_modules" / "p.js")),
                (1, str(tmp_path / "real.txt")),
            ]

        import sys
        import types

        fake_module = types.ModuleType("watchfiles")
        fake_module.awatch = _awatch
        monkeypatch.setitem(sys.modules, "watchfiles", fake_module)

        ws = _FakeWebSocket()
        await ww_mod.watch_directory(str(tmp_path), ws)
        # Only real.txt landed in the batch.
        changes = [s for s in ws.sent if s["type"] == "change"]
        assert changes
        paths = [c["path"] for c in changes[0]["changes"]]
        assert paths == ["real.txt"]

    async def test_cancelled_silent(self, monkeypatch, tmp_path):
        async def _awatch(root, **kw):
            raise asyncio.CancelledError()
            yield  # pragma: no cover

        import sys
        import types

        fake_module = types.ModuleType("watchfiles")
        fake_module.awatch = _awatch
        monkeypatch.setitem(sys.modules, "watchfiles", fake_module)

        ws = _FakeWebSocket()
        await ww_mod.watch_directory(str(tmp_path), ws)
        # No re-raise.
