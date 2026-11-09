"""Host-owned UI activation policy and reviewed desktop-pet identity."""

import shutil
from dataclasses import replace
from pathlib import Path

import yaml

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.ext.policy import (
    default_enabled,
    desktop_pet,
    dev_overrides,
    slot_enabled,
)
from kohakuterrarium.packages.slots.ui import parse_ui_slot
from kohakuterrarium.studio.identity import ui_extensions

PET = Path(__file__).resolve().parents[4] / "examples/packages/virtual_pet"


def _slot(root=PET):
    manifest = yaml.safe_load((root / "kohaku.yaml").read_text("utf-8"))
    return parse_ui_slot({**manifest, "path": str(root)})


def test_only_reviewed_pet_ui_receives_the_desktop_default(tmp_path):
    slot = _slot()
    assert desktop_pet(slot)
    assert not desktop_pet(replace(slot, package="another-package"))
    assert not desktop_pet(replace(slot, entry="ui/replacement.js"))
    root = tmp_path / "copy"
    shutil.copytree(PET, root)
    copied = _slot(root)
    assert desktop_pet(copied)
    entry = root / "ui/index.js"
    original = entry.read_bytes()
    entry.write_bytes(original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert desktop_pet(copied)
    entry.write_bytes(original + b"\n// replaced package\n")
    assert not desktop_pet(copied)
    entry.write_bytes(original)
    (root / "ui/extra.js").write_text("export const firstParty = true")
    assert not desktop_pet(copied)
    (root / "ui/extra.js").unlink()
    entry.unlink()
    assert not desktop_pet(copied)
    entry.write_bytes(b"a" * (512 * 1024 + 1))
    assert not desktop_pet(copied)


def test_activation_still_requires_a_valid_enabled_package(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    config = AuthConfig()
    slot = _slot()
    assert default_enabled(config)
    assert slot_enabled(slot, config)
    ui_extensions.set_enabled(slot.package, False)
    assert not slot_enabled(slot, config)
    assert not slot_enabled(replace(slot, error="invalid"), config)
    monkeypatch.setenv("KT_UI_DEV", "virtual-pet=http://127.0.0.1:1234, bad, =ignored")
    assert dev_overrides() == {"virtual-pet": "http://127.0.0.1:1234"}
