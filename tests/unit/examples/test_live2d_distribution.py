"""The distributed extension contains safe assets and usable entry points."""

from pathlib import Path

import yaml

from kohakuterrarium.llm.profiles.resolve import (
    resolve_controller_llm,
    set_default_model,
)


def test_public_ui_excludes_model_assets():
    ui = Path(__file__).parents[3] / "examples/packages/live2d_pet/ui"
    private_suffixes = (
        ".moc3",
        ".cmo3",
        ".psd",
        ".model3.json",
        ".exp3.json",
        ".motion3.json",
    )
    assets = [
        path.relative_to(ui).as_posix()
        for path in ui.rglob("*")
        if path.is_file()
        and (path.name.endswith(private_suffixes) or path.suffix in (".webp", ".png"))
    ]
    assert assets == []
    assert not (ui / "model").exists()


def test_companion_uses_selected_default_model(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    path = (
        Path(__file__).parents[3]
        / "examples/packages/live2d_pet/creatures/companion/config.yaml"
    )
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name in ("glm-5.3-flash", "glm-5.3-flash-1m"):
        set_default_model(f"glm-coding/{name}")
        profile = resolve_controller_llm(config)
        assert profile is not None
        assert (profile.provider, profile.name) == ("glm-coding", name)
    assert resolve_controller_llm({**config, "llm": "missing-test-preset"}) is None
