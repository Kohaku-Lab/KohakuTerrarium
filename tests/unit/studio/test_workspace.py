"""LocalWorkspace unit tests."""

from pathlib import Path

import pytest

from kohakuterrarium.api.studio.workspace.base import Workspace
from kohakuterrarium.api.studio.workspace.local import LocalWorkspace


def test_open_rejects_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        LocalWorkspace.open(tmp_path / "does-not-exist")


def test_open_rejects_file(tmp_path: Path):
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        LocalWorkspace.open(f)


def test_satisfies_protocol(tmp_path: Path):
    ws = LocalWorkspace.open(tmp_path)
    assert isinstance(ws, Workspace)


def test_summary_empty(tmp_path: Path):
    """Fresh workspace with no modules/ folder and no kohaku.yaml.

    The summary still lists entries from installed kt packages (if any
    are in ``~/.kohakuterrarium/packages/``) because the dashboard
    surfaces everything a creature can wire in. Workspace-authored
    files (``source == "workspace"``) must be empty.
    """
    ws = LocalWorkspace.open(tmp_path)
    summary = ws.summary()
    assert summary["root"] == str(tmp_path.resolve())
    assert summary["creatures"] == []
    for kind in ("tools", "subagents", "triggers", "plugins", "inputs", "outputs"):
        entries = summary["modules"][kind]
        authored = [e for e in entries if e.get("source") == "workspace"]
        assert authored == []


def test_summary_merges_workspace_manifest(tmp_path: Path):
    """kohaku.yaml entries surface in the dashboard summary."""
    (tmp_path / "kohaku.yaml").write_text(
        "name: test-ws\n"
        "version: 0.1.0\n"
        "tools:\n"
        "  - name: ws_tool_a\n"
        "    module: x.y.z\n"
        "    class: Z\n",
        encoding="utf-8",
    )
    ws = LocalWorkspace.open(tmp_path)
    entries = ws.summary()["modules"]["tools"]
    ws_entry = next((e for e in entries if e["name"] == "ws_tool_a"), None)
    assert ws_entry is not None
    assert ws_entry["source"] == "workspace-manifest"


def test_summary_authored_files_tagged(tmp_path: Path):
    """Files under modules/<kind>/ are tagged source=workspace."""
    kind_dir = tmp_path / "modules" / "tools"
    kind_dir.mkdir(parents=True)
    (kind_dir / "my_tool.py").write_text("# stub", encoding="utf-8")
    ws = LocalWorkspace.open(tmp_path)
    entries = ws.summary()["modules"]["tools"]
    mine = next((e for e in entries if e["name"] == "my_tool"), None)
    assert mine is not None
    assert mine["source"] == "workspace"


def test_list_creatures_finds_config_yaml(tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(
        'name: alpha\nversion: "1.0"\ndescription: a test\n',
        encoding="utf-8",
    )
    ws = LocalWorkspace.open(tmp_workspace)
    creatures = ws.list_creatures()
    assert len(creatures) == 1
    assert creatures[0]["name"] == "alpha"
    assert creatures[0]["description"] == "a test"


def test_list_creatures_skips_broken(tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "broken"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(
        "not: [valid: yaml",
        encoding="utf-8",
    )
    ws = LocalWorkspace.open(tmp_workspace)
    # Broken file still listed (with error field) — important for UI recovery
    items = ws.list_creatures()
    assert len(items) == 1
    assert items[0]["name"] == "broken"


def test_load_creature(tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(
        'name: alpha\nversion: "1.0"\n',
        encoding="utf-8",
    )
    (cdir / "prompts").mkdir()
    (cdir / "prompts" / "system.md").write_text("hello", encoding="utf-8")

    ws = LocalWorkspace.open(tmp_workspace)
    data = ws.load_creature("alpha")
    assert data["name"] == "alpha"
    assert data["config"]["name"] == "alpha"
    assert data["prompts"]["prompts/system.md"] == "hello"
    assert "effective" in data


def test_load_creature_missing_raises(tmp_workspace: Path):
    ws = LocalWorkspace.open(tmp_workspace)
    with pytest.raises(FileNotFoundError):
        ws.load_creature("ghost")


def test_load_creature_sanitizes_name(tmp_workspace: Path):
    ws = LocalWorkspace.open(tmp_workspace)
    with pytest.raises(ValueError):
        ws.load_creature("../etc")


def test_list_modules_empty(tmp_workspace: Path):
    ws = LocalWorkspace.open(tmp_workspace)
    assert ws.list_modules("tools") == []


def test_list_modules_finds_py(tmp_workspace: Path):
    (tmp_workspace / "modules" / "tools" / "my_tool.py").write_text(
        "class Foo: pass\n",
        encoding="utf-8",
    )
    ws = LocalWorkspace.open(tmp_workspace)
    mods = ws.list_modules("tools")
    assert len(mods) == 1
    assert mods[0]["name"] == "my_tool"
    assert mods[0]["kind"] == "tools"


def test_list_modules_unknown_kind_raises(tmp_workspace: Path):
    ws = LocalWorkspace.open(tmp_workspace)
    with pytest.raises(ValueError):
        ws.list_modules("nonexistent_kind")
