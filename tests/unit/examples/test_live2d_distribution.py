"""The extension's public UI tree contains no private model resources."""

from pathlib import Path


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
