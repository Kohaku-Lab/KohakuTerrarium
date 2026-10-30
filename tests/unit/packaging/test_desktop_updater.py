"""The updater manifest and the config that makes the shell trust it."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "packaging" / "desktop"


@pytest.fixture(scope="module")
def manifest_tool():
    spec = importlib.util.spec_from_file_location(
        "updater_manifest", DESKTOP / "updater_manifest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["updater_manifest"] = module
    spec.loader.exec_module(module)
    return module


def _assets(tmp_path, version="2.3.0", windows=True, mac=True, sign=True):
    names = []
    if windows:
        names.append(f"KohakuTerrarium_{version}_x64-setup.exe")
    if mac:
        names.append(f"KohakuTerrarium_{version}_aarch64.app.tar.gz")
    for name in names:
        (tmp_path / name).write_bytes(b"installer")
        if sign:
            (tmp_path / f"{name}.sig").write_text(f"sig-of-{name}\n", encoding="utf-8")
    (tmp_path / f"KohakuTerrarium_{version}_x64_en-US.msi").write_bytes(b"msi")
    return tmp_path


def test_manifest_lists_each_signed_platform_with_its_download_url(
    manifest_tool, tmp_path
):
    version, entries = manifest_tool.collect(_assets(tmp_path))
    manifest = manifest_tool.build(
        version,
        entries,
        "https://example.test/dl/v2.3.0/",
        "https://example.test/notes",
    )

    assert manifest["version"] == "2.3.0"
    assert manifest["notes"] == "https://example.test/notes"
    assert set(manifest["platforms"]) == {"windows-x86_64", "darwin-aarch64"}
    windows = manifest["platforms"]["windows-x86_64"]
    assert (
        windows["url"]
        == "https://example.test/dl/v2.3.0/KohakuTerrarium_2.3.0_x64-setup.exe"
    )
    assert windows["signature"] == "sig-of-KohakuTerrarium_2.3.0_x64-setup.exe"
    assert manifest["pub_date"].endswith("Z")


def test_the_msi_is_not_an_update_target(manifest_tool, tmp_path):
    _, entries = manifest_tool.collect(_assets(tmp_path))
    assert all(not entry["name"].endswith(".msi") for entry in entries.values())


def test_one_platform_is_enough(manifest_tool, tmp_path):
    _, entries = manifest_tool.collect(_assets(tmp_path, mac=False))
    assert set(entries) == {"windows-x86_64"}


def test_an_unsigned_asset_is_refused(manifest_tool, tmp_path):
    with pytest.raises(manifest_tool.ManifestError, match="no signature"):
        manifest_tool.collect(_assets(tmp_path, sign=False))


def test_no_updater_assets_is_an_error(manifest_tool, tmp_path):
    with pytest.raises(manifest_tool.ManifestError, match="no updater assets"):
        manifest_tool.collect(_assets(tmp_path, windows=False, mac=False))


def test_assets_from_different_versions_are_refused(manifest_tool, tmp_path):
    _assets(tmp_path, version="2.3.0", mac=False)
    (tmp_path / "KohakuTerrarium_2.4.0_aarch64.app.tar.gz").write_bytes(b"x")
    (tmp_path / "KohakuTerrarium_2.4.0_aarch64.app.tar.gz.sig").write_text(
        "s", encoding="utf-8"
    )
    with pytest.raises(manifest_tool.ManifestError, match="disagree"):
        manifest_tool.collect(tmp_path)


def test_main_writes_the_file(manifest_tool, tmp_path, capsys):
    assets = _assets(tmp_path / "a") if (tmp_path / "a").mkdir() is None else None
    out = tmp_path / "latest.json"

    code = manifest_tool.main(
        ["--assets", str(assets), "--base-url", "https://x.test/d", "--out", str(out)]
    )

    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == "2.3.0"
    assert "windows-x86_64" in capsys.readouterr().out


def test_the_shell_trusts_the_published_key_and_reads_the_release_feed():
    conf = json.loads(
        (DESKTOP / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    updater = conf["plugins"]["updater"]
    assert updater["pubkey"]
    assert updater["endpoints"] == [
        "https://github.com/Kohaku-Lab/KohakuTerrarium/releases/latest/download/latest.json"
    ]


def test_updater_artifacts_are_built_only_through_the_override_config():
    base = json.loads(
        (DESKTOP / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    override = json.loads(
        (DESKTOP / "src-tauri" / "tauri.updater.conf.json").read_text(encoding="utf-8")
    )
    assert "createUpdaterArtifacts" not in base["bundle"]
    assert override["bundle"]["createUpdaterArtifacts"] is True
