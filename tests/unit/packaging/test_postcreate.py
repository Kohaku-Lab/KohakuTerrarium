"""Unit tests for ``packaging/android/postcreate.py``.

The script merges template Java + sandbox assets + manifest patches
into a Briefcase-generated Android project.  Tests build a fake
"generated project" + fixture trees, run the script's functions
directly, and assert the resulting layout.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_POSTCREATE_PATH = (
    Path(__file__).resolve().parents[3] / "packaging" / "android" / "postcreate.py"
)


@pytest.fixture(scope="module")
def postcreate():
    spec = importlib.util.spec_from_file_location("postcreate", _POSTCREATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["postcreate"] = module
    spec.loader.exec_module(module)
    return module


def _build_fake_generated(root: Path) -> Path:
    """Create a minimal generated-project tree that mimics what
    Briefcase Android produces."""
    app = root / "build" / "kohakuterrarium" / "android" / "gradle" / "app"
    java = app / "src" / "main" / "java"
    (java / "org" / "beeware" / "android").mkdir(parents=True)
    (java / "org" / "beeware" / "android" / "MainActivity.java").write_text(
        "package org.beeware.android;\npublic class MainActivity {}\n",
        encoding="utf-8",
    )
    manifest = app / "src" / "main" / "AndroidManifest.xml"
    manifest.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="org.kohaku.terrarium">\n'
        "    <application>\n"
        '        <activity android:name=".MainActivity"\n'
        '            android:exported="true">\n'
        "            <intent-filter>\n"
        '                <action android:name="android.intent.action.MAIN" />\n'
        '                <category android:name="android.intent.category.LAUNCHER" />\n'
        "            </intent-filter>\n"
        "        </activity>\n"
        "    </application>\n"
        "</manifest>\n",
        encoding="utf-8",
    )
    return app


def _build_fake_template(root: Path) -> Path:
    """Mirror of our ``packaging/android/template/`` for tests."""
    tpl = root / "template"
    java = tpl / "app" / "src" / "main" / "java" / "org" / "kohaku" / "terrarium"
    java.mkdir(parents=True)
    (java / "MainActivity.java").write_text(
        "package org.kohaku.terrarium;\npublic class MainActivity {}\n",
        encoding="utf-8",
    )
    (java / "KohakuHostService.java").write_text(
        "package org.kohaku.terrarium;\npublic class KohakuHostService {}\n",
        encoding="utf-8",
    )
    return tpl


def _build_fake_sandbox(root: Path) -> Path:
    sandbox = root / "bin"
    for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
        (sandbox / abi).mkdir(parents=True)
        (sandbox / abi / "busybox").write_bytes(b"#!fake-" + abi.encode())
    (sandbox / "manifest.json").write_text(
        '{"binaries":["busybox"],"abis":["arm64-v8a","armeabi-v7a","x86_64"]}',
        encoding="utf-8",
    )
    return sandbox


class TestCopyJavaOverrides:
    def test_copies_all_java_files(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        tpl = _build_fake_template(tmp_path)
        rc = postcreate.copy_java_overrides(tpl, gen)
        assert rc == 0
        java = gen / "src" / "main" / "java" / "org" / "kohaku" / "terrarium"
        assert (java / "MainActivity.java").is_file()
        assert (java / "KohakuHostService.java").is_file()
        assert "org.kohaku.terrarium" in (java / "MainActivity.java").read_text(
            encoding="utf-8"
        )

    def test_missing_template_returns_1(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        rc = postcreate.copy_java_overrides(tmp_path / "nonexistent", gen)
        assert rc == 1


class TestCopySandboxAssets:
    def test_copies_per_abi_layout(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        sandbox = _build_fake_sandbox(tmp_path)
        rc = postcreate.copy_sandbox_assets(sandbox, gen, skip_check=False)
        assert rc == 0
        assets = gen / "src" / "main" / "assets" / "sandbox" / "bin"
        assert (assets / "manifest.json").is_file()
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            assert (assets / abi / "busybox").is_file()

    def test_missing_sandbox_fails_without_skip(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        rc = postcreate.copy_sandbox_assets(tmp_path / "nope", gen, skip_check=False)
        assert rc == 1

    def test_missing_sandbox_passes_with_skip(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        rc = postcreate.copy_sandbox_assets(tmp_path / "nope", gen, skip_check=True)
        assert rc == 0

    def test_re_run_replaces_old_assets(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        sandbox = _build_fake_sandbox(tmp_path)
        postcreate.copy_sandbox_assets(sandbox, gen, skip_check=False)
        # Mutate the sandbox bin and re-run; the new content wins.
        (sandbox / "arm64-v8a" / "busybox").write_bytes(b"#!v2")
        postcreate.copy_sandbox_assets(sandbox, gen, skip_check=False)
        assets = gen / "src" / "main" / "assets" / "sandbox" / "bin"
        assert (assets / "arm64-v8a" / "busybox").read_bytes() == b"#!v2"


class TestPatchLauncherActivity:
    def test_repoints_to_our_activity(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        rc = postcreate.patch_launcher_activity(gen)
        assert rc == 0
        manifest = (gen / "src" / "main" / "AndroidManifest.xml").read_text(
            encoding="utf-8"
        )
        assert "org.kohaku.terrarium.MainActivity" in manifest
        # The bare ".MainActivity" reference should be gone.
        assert 'android:name=".MainActivity"' not in manifest

    def test_manifest_missing_returns_1(self, tmp_path, postcreate):
        # An empty "app" dir — no manifest.
        gen = tmp_path / "app"
        gen.mkdir()
        rc = postcreate.patch_launcher_activity(gen)
        assert rc == 1

    def test_unrecognised_template_shape_returns_1(self, tmp_path, postcreate):
        gen = tmp_path / "app"
        (gen / "src" / "main").mkdir(parents=True)
        (gen / "src" / "main" / "AndroidManifest.xml").write_text(
            "<manifest><application/></manifest>",
            encoding="utf-8",
        )
        rc = postcreate.patch_launcher_activity(gen)
        # No <activity> declaration to patch — script must fail
        # loudly so the operator knows Briefcase changed its
        # template shape.
        assert rc == 1


class TestCopyWheelhouse:
    """Pin: ``./wheels/*.whl`` (populated by CI from KohakuVault's
    GitHub Releases) lands under the generated Gradle app dir so
    Chaquopy's ``--find-links wheels`` resolves it."""

    def test_no_source_dir_is_noop(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        # postcreate.copy_wheelhouse() reads from
        # ``REPO_ROOT/wheels`` — which doesn't exist in tests
        # unless we create it.  No-op behaviour: don't fail.
        rc = postcreate.copy_wheelhouse(gen)
        assert rc == 0

    def test_copies_wheels_when_present(self, tmp_path, postcreate, monkeypatch):
        gen = _build_fake_generated(tmp_path)
        # Pretend the repo root has a ``wheels/`` with two
        # wheel files.  Patch REPO_ROOT so the helper reads from
        # our fixture instead of the real repo.
        fake_repo = tmp_path / "fake_repo"
        wheels = fake_repo / "wheels"
        wheels.mkdir(parents=True)
        (
            wheels / "kohakuvault-0.8.3-cp313-cp313-linux_android_24_arm64_v8a.whl"
        ).write_bytes(b"PK\x03\x04 fake wheel arm64")
        (
            wheels / "kohakuvault-0.8.3-cp313-cp313-linux_android_24_x86_64.whl"
        ).write_bytes(b"PK\x03\x04 fake wheel x86_64")
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)

        rc = postcreate.copy_wheelhouse(gen)
        assert rc == 0
        # Both wheels landed at the path Chaquopy resolves
        # ``--find-links wheels`` against — i.e., the generated
        # gradle app dir's ``wheels/`` subdir.
        dst = gen / "wheels"
        assert dst.is_dir()
        assert (
            dst / "kohakuvault-0.8.3-cp313-cp313-linux_android_24_arm64_v8a.whl"
        ).is_file()
        assert (
            dst / "kohakuvault-0.8.3-cp313-cp313-linux_android_24_x86_64.whl"
        ).is_file()

    def test_empty_source_dir_is_noop(self, tmp_path, postcreate, monkeypatch):
        # Source dir exists but contains no wheels — common on
        # the desktop matrix where Android wheels aren't fetched.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        (fake_repo / "wheels").mkdir(parents=True)
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        rc = postcreate.copy_wheelhouse(gen)
        assert rc == 0
        # No destination dir created when source has nothing.
        assert not (gen / "wheels" / "anything.whl").exists()


class TestPatchAllowBackup:
    def test_flips_true_to_false(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        manifest_path = gen / "src" / "main" / "AndroidManifest.xml"
        manifest_path.write_text(
            '<?xml version="1.0"?>\n'
            "<manifest>\n"
            '    <application android:allowBackup="true"\n'
            '        android:icon="@mipmap/ic_launcher">\n'
            "    </application>\n"
            "</manifest>\n",
            encoding="utf-8",
        )
        rc = postcreate.patch_allow_backup(gen)
        assert rc == 0
        text = manifest_path.read_text(encoding="utf-8")
        assert 'android:allowBackup="false"' in text
        assert 'android:allowBackup="true"' not in text

    def test_idempotent_when_already_false(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        manifest_path = gen / "src" / "main" / "AndroidManifest.xml"
        manifest_path.write_text(
            '<?xml version="1.0"?>\n'
            "<manifest>\n"
            '    <application android:allowBackup="false">\n'
            "    </application>\n"
            "</manifest>\n",
            encoding="utf-8",
        )
        rc = postcreate.patch_allow_backup(gen)
        assert rc == 0
        # Still false (no double-patching).
        text = manifest_path.read_text(encoding="utf-8")
        assert text.count('android:allowBackup="false"') == 1

    def test_injects_when_attribute_absent(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        manifest_path = gen / "src" / "main" / "AndroidManifest.xml"
        manifest_path.write_text(
            '<?xml version="1.0"?>\n'
            "<manifest>\n"
            '    <application android:icon="@mipmap/ic_launcher">\n'
            "    </application>\n"
            "</manifest>\n",
            encoding="utf-8",
        )
        rc = postcreate.patch_allow_backup(gen)
        assert rc == 0
        text = manifest_path.read_text(encoding="utf-8")
        assert 'android:allowBackup="false"' in text
        # Must NOT produce a duplicate attribute — the audit caught
        # exactly this: AAPT rejects two ``allowBackup`` on the
        # same tag.
        assert text.count("android:allowBackup") == 1

    def test_missing_manifest_returns_1(self, tmp_path, postcreate):
        gen = tmp_path / "app"
        gen.mkdir()
        rc = postcreate.patch_allow_backup(gen)
        assert rc == 1

    def test_no_application_tag_returns_1(self, tmp_path, postcreate):
        gen = tmp_path / "app"
        (gen / "src" / "main").mkdir(parents=True)
        (gen / "src" / "main" / "AndroidManifest.xml").write_text(
            "<manifest></manifest>", encoding="utf-8"
        )
        rc = postcreate.patch_allow_backup(gen)
        assert rc == 1


class TestRemoveDefaultActivity:
    def test_removes_briefcase_default(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        target = (
            gen
            / "src"
            / "main"
            / "java"
            / "org"
            / "beeware"
            / "android"
            / "MainActivity.java"
        )
        assert target.is_file()
        rc = postcreate.remove_default_activity(gen)
        assert rc == 0
        assert not target.exists()

    def test_idempotent_when_already_removed(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        # Remove once …
        postcreate.remove_default_activity(gen)
        # … then again — no error.
        rc = postcreate.remove_default_activity(gen)
        assert rc == 0


class TestFullRun:
    def test_main_with_all_args(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        tpl = _build_fake_template(tmp_path)
        sandbox = _build_fake_sandbox(tmp_path)
        rc = postcreate.main(
            [
                "--generated",
                str(gen),
                "--template",
                str(tpl),
                "--sandbox",
                str(sandbox),
            ]
        )
        assert rc == 0
        # Everything landed.
        assert (
            gen
            / "src"
            / "main"
            / "java"
            / "org"
            / "kohaku"
            / "terrarium"
            / "MainActivity.java"
        ).is_file()
        assert (
            gen / "src" / "main" / "assets" / "sandbox" / "bin" / "manifest.json"
        ).is_file()
        manifest = (gen / "src" / "main" / "AndroidManifest.xml").read_text(
            encoding="utf-8"
        )
        assert "org.kohaku.terrarium.MainActivity" in manifest

    def test_main_fails_when_generated_missing(self, tmp_path, postcreate):
        rc = postcreate.main(["--generated", str(tmp_path / "nope")])
        assert rc == 2
