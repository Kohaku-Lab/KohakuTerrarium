"""Unit tests for ``packaging/desktop/stage_runtime.py`` (offline).

Network access is replaced by an injected fetch function; the bootstrap case
copies the real launcher package and checks that it stands alone.
"""

import ast
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "packaging" / "desktop" / "stage_runtime.py"


@pytest.fixture(scope="module")
def stage():
    spec = importlib.util.spec_from_file_location("stage_runtime", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["stage_runtime"] = module
    spec.loader.exec_module(module)
    return module


PIN = {
    "source": "https://example.test/releases/download",
    "tag": "20260101",
    "python": "3.13.9",
    "variant": "install_only_stripped",
    "triples": {
        "win-x64": "x86_64-pc-windows-msvc",
        "macos-arm64": "aarch64-apple-darwin",
    },
}


def _python_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in {
            "python/python.exe": b"MZ",
            "python/Lib/os.py": b"# os",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.mark.parametrize(
    "version,core",
    [
        ("2.2.0", "2.2.0"),
        ("2.2.1.dev20261031", "2.2.1"),
        ("3.0.0rc1", "3.0.0"),
    ],
)
def test_installer_version_is_the_numeric_core(stage, version, core):
    assert stage.core_version(version) == core


def test_unparseable_versions_are_rejected(stage):
    with pytest.raises(ValueError):
        stage.core_version("latest")


def test_stamping_rewrites_only_the_version(stage, tmp_path):
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(
        json.dumps({"productName": "KT", "version": "0.0.0"}), encoding="utf-8"
    )

    assert stage.stamp_version(conf, "2.3.0.dev20261101") == "2.3.0"

    assert json.loads(conf.read_text(encoding="utf-8")) == {
        "productName": "KT",
        "version": "2.3.0",
    }


def test_the_repository_config_carries_a_stampable_version(stage):
    conf = json.loads(stage.TAURI_CONF.read_text(encoding="utf-8"))
    assert stage.core_version(conf["version"]) == conf["version"]
    assert stage.core_version(stage.project_version())


def test_pinned_interpreter_asset_names_and_encodes_the_plus(stage):
    name, url = stage.python_asset(PIN, "win-x64")
    assert name == (
        "cpython-3.13.9+20260101-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    )
    assert url == (
        "https://example.test/releases/download/20260101/"
        "cpython-3.13.9%2B20260101-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    )
    with pytest.raises(ValueError):
        stage.python_asset(PIN, "plan9")


def test_every_pinned_platform_has_an_asset(stage):
    pin = json.loads(stage.RUNTIME_PIN.read_text(encoding="utf-8"))
    for platform in pin["triples"]:
        name, url = stage.python_asset(pin, platform)
        assert name in url.replace("%2B", "+")


def test_checksum_lookup_accepts_binary_marker_and_rejects_unlisted(stage):
    sums = "aaa  one.tar.gz\nbbb *two.tar.gz\n"
    assert stage.expected_sha256(sums, "one.tar.gz") == "aaa"
    assert stage.expected_sha256(sums, "two.tar.gz") == "bbb"
    with pytest.raises(ValueError):
        stage.expected_sha256(sums, "three.tar.gz")


def test_download_verifies_the_checksum_before_keeping_the_archive(stage, tmp_path):
    archive = _python_archive()
    name, url = stage.python_asset(PIN, "win-x64")
    sums_url = f"{PIN['source']}/{PIN['tag']}/SHA256SUMS"

    def good(requested):
        if requested == sums_url:
            return f"{hashlib.sha256(archive).hexdigest()}  {name}\n".encode()
        assert requested == url
        return archive

    saved = stage.download_python(PIN, "win-x64", tmp_path, fetch=good)
    assert saved.read_bytes() == archive

    def tampered(requested):
        return good(requested) if requested == sums_url else archive + b"x"

    with pytest.raises(ValueError, match="checksum"):
        stage.download_python(PIN, "win-x64", tmp_path / "other", fetch=tampered)
    assert not (tmp_path / "other").exists()


def test_extraction_places_the_interpreter_directly_under_the_destination(
    stage, tmp_path
):
    archive = tmp_path / "py.tar.gz"
    archive.write_bytes(_python_archive())
    target = tmp_path / "stage" / "python"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("old", encoding="utf-8")

    stage.extract_python(archive, target)

    assert (target / "python.exe").read_bytes() == b"MZ"
    assert (target / "Lib" / "os.py").is_file()
    assert not (target / "stale.txt").exists()


def test_pruning_drops_test_suites_and_caches_but_keeps_the_library(stage, tmp_path):
    for lib in (tmp_path / "Lib", tmp_path / "lib" / "python3.13"):
        for name in ("test", "idlelib", "asyncio"):
            (lib / name).mkdir(parents=True)
            (lib / name / "__init__.py").write_text("", encoding="utf-8")
        (lib / "asyncio" / "__pycache__").mkdir()

    removed = stage.prune_python(tmp_path)

    assert {path.name for path in removed} == {"test", "idlelib"}
    for lib in (tmp_path / "Lib", tmp_path / "lib" / "python3.13"):
        assert not (lib / "test").exists() and not (lib / "idlelib").exists()
        assert (lib / "asyncio" / "__init__.py").is_file()
        assert not (lib / "asyncio" / "__pycache__").exists()


def test_bootstrap_is_the_launcher_without_android_support(stage, tmp_path):
    staged = stage.stage_bootstrap(stage.PACKAGE, tmp_path / "bootstrap", install=False)

    names = {path.name for path in (staged / "kohakuterrarium" / "launcher").iterdir()}
    assert {"bootloader.py", "sidecar.py", "interpreter_mode.py", "paths.py"} <= names
    assert "android.py" not in names
    assert "__pycache__" not in names
    assert (staged / "kohakuterrarium" / "__init__.py").is_file()
    assert not any(
        child.name not in {"launcher", "__init__.py"}
        for child in (staged / "kohakuterrarium").iterdir()
    )


def test_staged_bootstrap_imports_nothing_outside_the_launcher(stage, tmp_path):
    staged = stage.stage_bootstrap(stage.PACKAGE, tmp_path / "bootstrap", install=False)

    offenders = []
    for path in (staged / "kohakuterrarium" / "launcher").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.startswith("kohakuterrarium") and not module.startswith(
                    "kohakuterrarium.launcher"
                ):
                    offenders.append(f"{path.name}: {module}")
    assert offenders == []


def test_release_staging_copies_archives_and_refuses_an_empty_directory(
    stage, tmp_path
):
    source = tmp_path / "release"
    source.mkdir()
    (source / "kohakuterrarium-2.3.0-win-x64-py3.13.tar.zst").write_bytes(b"tree")
    (source / "notes.txt").write_text("ignored", encoding="utf-8")

    copied = stage.stage_release(source, tmp_path / "stage" / "bundled-release")

    assert [path.name for path in copied] == [
        "kohakuterrarium-2.3.0-win-x64-py3.13.tar.zst"
    ]
    with pytest.raises(FileNotFoundError):
        stage.stage_release(tmp_path / "nothing", tmp_path / "stage2")
