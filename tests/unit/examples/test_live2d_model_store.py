"""Private imports are bounded, complete, versioned and never partial."""

import json
import os
import stat
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from examples.packages.live2d_pet.kt_live2d.model_adapters import (
    DEEPSEEK_EXPRESSIONS,
    DEEPSEEK_WORK,
)
from examples.packages.live2d_pet.kt_live2d.model_store import (
    current_model,
    import_model,
    model_file,
    safe_path,
)


def test_import_generates_both_qualities_and_keeps_previous_on_failure(
    tmp_path, model_zip
):
    root = tmp_path / "private"
    assert current_model(root, "deepseek") is None
    first = import_model(root, None, model_zip())
    assert first["model"] == "deepseek"
    assert current_model(root, "deepseek") == first
    for quality in ("high", "standard"):
        path = model_file(root, "deepseek", first["version"], f"{quality}.model3.json")
        manifest = json.loads(path.read_text())
        assert manifest["Demo"]["profile"] == "deepseek"
        assert manifest["KohakuTerrarium"] == {"adapter": "deepseek"}
        assert manifest["Demo"]["textureBytes"] == 16 * 32 * (
            4 if quality == "high" else 1
        )
        assert len(manifest["FileReferences"]["Expressions"]) == len(
            DEEPSEEK_EXPRESSIONS
        )
        texture = model_file(
            root,
            "deepseek",
            first["version"],
            manifest["FileReferences"]["Textures"][0],
        )
        with Image.open(texture) as image:
            assert image.getpixel((0, 0))[3] == 120
            assert image.getpixel((0, 0))[:3] == pytest.approx((180, 100, 30), abs=1)
    assert model_file(root, "deepseek", first["version"], "NOTICE.txt").is_file()
    for hidden in ("unrelated.html", "inventory.json", "../current", "https://evil/x"):
        with pytest.raises(ValueError):
            model_file(root, "deepseek", first["version"], hidden)
    for changes in (
        {DEEPSEEK_WORK[0]: None},
        {"Exp3/脸红.exp3.json": None},
        {"texture.png": b"broken"},
        {"fish.moc3": b"broken"},
        {"fish.model3.json": b"[]"},
        {"fish.model3.json": b'{"Version":3,"FileReferences":{"Textures":[]}}'},
        {"Exp3/脸红.exp3.json": b"{}"},
        {"other.model3.json": b"{}"},
        {"../escape": b"never written"},
    ):
        with pytest.raises(ValueError):
            import_model(root, "deepseek", model_zip(changes))
        assert current_model(root, "deepseek") == first
        assert len(list((root / "deepseek").iterdir())) == 2
    for metadata in (
        {"KohakuTerrarium": {"adapter": "unknown"}},
        {"KohakuTerrarium": {"adapter": "dragon"}},
        {"KohakuTerrarium": {"adapter": "deepseek"}, "Demo": {"profile": "dragon"}},
        {"KohakuTerrarium": []},
    ):
        with pytest.raises(ValueError):
            import_model(root, None, model_zip(**metadata))
        assert current_model(root, "deepseek") == first
        assert len(list(root.iterdir())) == 1
    with pytest.raises(ValueError, match="不匹配"):
        import_model(
            root,
            None,
            model_zip(
                {"Exp3/脸红.exp3.json": None}, KohakuTerrarium={"adapter": "deepseek"}
            ),
        )
    assert current_model(root, "deepseek") == first
    second = import_model(
        root, None, model_zip(KohakuTerrarium={"adapter": "deepseek"})
    )
    assert second["version"] != first["version"]
    assert current_model(root, "deepseek") == second
    assert model_file(root, "deepseek", first["version"], "high.model3.json").exists()
    with pytest.raises(FileNotFoundError):
        model_file(
            tmp_path / "other-user", "deepseek", first["version"], "high.model3.json"
        )


@pytest.mark.parametrize(
    "path", ["", "../x", "/x", "a//b", "a/./b", "a\\b", "C:/x", "a%2fb", "a?x", "a#x"]
)
def test_rejects_ambiguous_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_rejects_links_and_archive_bombs(tmp_path, model_zip):
    source = model_zip()
    link = zipfile.ZipInfo("linked")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr(link, "../../outside")
    with pytest.raises(ValueError, match="链接"):
        import_model(tmp_path / "private", "deepseek", source)
    source = model_zip({str(i): b"" for i in range(512)})
    with pytest.raises(ValueError, match="512"):
        import_model(tmp_path / "private", "deepseek", source)
    with pytest.raises(ValueError, match="适配器"):
        import_model(tmp_path / "private", "unknown", source)


def test_real_imported_fish_manifest_has_all_adapter_assets(tmp_path):
    source = Path(
        os.environ.get(
            "KT_LIVE2D_DEEPSEEK_DIR",
            str(
                Path(__file__).parents[3]
                / "examples/packages/live2d_pet/ui/model/deepseek"
            ),
        )
    )
    if not (source / "high.model3.json").is_file():
        pytest.skip("Private fish resources are intentionally not distributed")
    archive_path = tmp_path / "fish.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(source))
    root = tmp_path / "private"
    imported = import_model(root, None, archive_path)
    totals = []
    for quality in ("high", "standard"):
        manifest = json.loads(
            model_file(
                root, "deepseek", imported["version"], f"{quality}.model3.json"
            ).read_text()
        )
        totals.append(manifest["Demo"]["textureBytes"])
        for name in DEEPSEEK_WORK:
            assert model_file(root, "deepseek", imported["version"], name).is_file()
    assert totals == [148 * 1024**2, 37 * 1024**2]


def test_dragon_import_preserves_motions_and_both_private_qualities(
    tmp_path, model_zip
):
    root = tmp_path / "private"
    imported = import_model(root, None, model_zip(model="dragon"))
    assert imported["model"] == "dragon"
    for quality, dimensions in (("high", (16, 32)), ("standard", (8, 16))):
        manifest = json.loads(
            model_file(
                root, "dragon", imported["version"], f"{quality}.model3.json"
            ).read_text()
        )
        refs = manifest["FileReferences"]
        assert refs["Motions"]["Sleep"][0]["File"] == "sleep2.motion3.json"
        assert {e["Name"] for e in refs["Expressions"]} == {
            "抬手",
            "抬手2",
            "开扇",
            "扇面",
            "变",
        }
        with Image.open(
            model_file(root, "dragon", imported["version"], refs["Textures"][0])
        ) as image:
            assert image.size == dimensions
    with pytest.raises(ValueError, match="不匹配"):
        import_model(
            root,
            "dragon",
            model_zip(
                {"变.exp3.json": None},
                model="dragon",
                KohakuTerrarium={"adapter": "dragon"},
            ),
        )
    assert current_model(root, "dragon") == imported
    with pytest.raises(FileNotFoundError):
        model_file(tmp_path / "other", "dragon", imported["version"], "kohaku.moc3")
