"""Unit tests for ``cli/extension/scaffold.py`` — ``kt extension new``."""

import importlib
import sys

import pytest
import yaml

from kohakuterrarium.cli.extension import scaffold as extension_scaffold
from kohakuterrarium.cli.extension.extension import extension_new_cli
from kohakuterrarium.modules.ui_owner import OwnerArgumentError, owner_call, owner_state
from kohakuterrarium.packages.slots.ui import parse_ui_slot


@pytest.fixture
def fake_testing_dir(tmp_path, monkeypatch):
    testing = tmp_path / "testing"
    testing.mkdir()
    (testing / "sdk-mock.js").write_text("export {}\n")
    monkeypatch.setattr(extension_scaffold, "sdk_testing_dir", lambda: testing)
    return testing


async def test_ui_scaffold_is_a_valid_owner_package(
    tmp_path, fake_testing_dir, monkeypatch
):
    files = extension_scaffold.scaffold_package("demo-ext", tmp_path, ui=True)
    root = tmp_path / "demo-ext"
    rel = {p.relative_to(root).as_posix() for p in files}
    assert {
        "kohaku.yaml",
        "demo_ext/ui_owner.py",
        "demo_ext/plugin.py",
        "demo_ext/api.py",
        "demo_ext/__init__.py",
        "ui/index.js",
        "ui/test/index.test.js",
        "ui/test/kt-sdk/sdk-mock.js",
        "vite.config.js",
    } <= rel

    manifest = yaml.safe_load((root / "kohaku.yaml").read_text())
    slot = parse_ui_slot({"name": "demo-ext", "path": str(root), **manifest})
    assert slot.error == ""
    assert [(c.kind, c.owner) for c in slot.contributions] == [
        ("settings", "package:demo-ext"),
        ("panel", "plugin:demo_ext_panel"),
    ]
    assert manifest["plugins"][0]["name"] == "demo_ext_panel"
    index = (root / "ui" / "index.js").read_text()
    assert index.count("useOwner()") == 2 and "{{module}}" not in index

    monkeypatch.syspath_prepend(str(root))
    try:
        ui_owner = importlib.import_module("demo_ext.ui_owner")
        plugin_mod = importlib.import_module("demo_ext.plugin")
        handler = ui_owner.PackageUI()
        pushes = []
        handler.__dict__["_ui_publisher"] = lambda e, d, user_id=None: pushes.append(
            (e, d)
        )
        assert await owner_call(handler, "set_greeting", {"greeting": "Yo"}) == {
            "greeting": "Yo"
        }
        assert pushes == [("state", {"greeting": "Yo"})]
        plugin = plugin_mod.PanelPlugin()
        await plugin.post_llm_call([], "", {})
        assert await owner_state(plugin) == {"turns": 1}
        with pytest.raises(OwnerArgumentError):
            await owner_call(plugin, "explode", {})
    finally:
        for mod in [m for m in sys.modules if m.startswith("demo_ext")]:
            sys.modules.pop(mod)


def test_plain_scaffold_and_refusals(tmp_path, capsys):
    files = extension_scaffold.scaffold_package("plain", tmp_path, ui=False)
    assert sorted(p.name for p in files) == ["README.md", "kohaku.yaml"]
    assert "ui" not in yaml.safe_load((tmp_path / "plain" / "kohaku.yaml").read_text())
    with pytest.raises(FileExistsError):
        extension_scaffold.scaffold_package("plain", tmp_path, ui=True)
    with pytest.raises(ValueError):
        extension_scaffold.scaffold_package("Bad Name", tmp_path, ui=False)
    assert extension_new_cli("plain", directory=str(tmp_path)) == 1
    assert extension_new_cli("fresh", ui=True, directory=str(tmp_path)) == 0
    assert "Created" in capsys.readouterr().out
