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


class TestPatchAndroidRequirements:
    """Pin: ``requirements.txt`` gets kohakuvault rewritten to
    direct URL refs against KV's GitHub Releases, with PEP 508
    platform_machine markers picking the right ABI."""

    def _seed_pyproject(self, fake_repo: Path, kv_spec: str) -> None:
        (fake_repo / "pyproject.toml").write_text(
            f'[project]\nname = "x"\ndependencies = [\n    "{kv_spec}",\n]\n',
            encoding="utf-8",
        )

    def _seed_requirements(self, gen: Path, content: str) -> Path:
        path = gen / "requirements.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def test_rewrites_kohakuvault_to_url_refs(self, tmp_path, postcreate, monkeypatch):
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.3")
        self._seed_requirements(
            gen,
            "fastapi>=0.115.0\nkohakuvault>=0.8.3\npyyaml>=6.0.0\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)

        rc = postcreate.patch_android_requirements(gen)
        assert rc == 0
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "kohakuvault>=0.8.3" not in text
        assert (
            "kohakuvault @ https://github.com/KohakuBlueleaf/KohakuVault/releases/"
            "download/v0.8.3/kohakuvault-0.8.3-cp313-cp313-android_24_arm64_v8a.whl"
            " ; platform_machine == 'aarch64'"
        ) in text
        assert (
            "kohakuvault @ https://github.com/KohakuBlueleaf/KohakuVault/releases/"
            "download/v0.8.3/kohakuvault-0.8.3-cp313-cp313-android_24_x86_64.whl"
            " ; platform_machine == 'x86_64'"
        ) in text
        assert "fastapi>=0.115.0" in text
        assert "pyyaml>=6.0.0" in text

    def test_idempotent_re_run_keeps_url_refs(self, tmp_path, postcreate, monkeypatch):
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.3")
        self._seed_requirements(gen, "kohakuvault>=0.8.3\n")
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)

        postcreate.patch_android_requirements(gen)
        first = (gen / "requirements.txt").read_text(encoding="utf-8")
        postcreate.patch_android_requirements(gen)
        second = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert first == second

    def test_missing_requirements_is_noop(self, tmp_path, postcreate):
        gen = _build_fake_generated(tmp_path)
        rc = postcreate.patch_android_requirements(gen)
        assert rc == 0

    def test_missing_kohakuvault_line_is_noop(self, tmp_path, postcreate, monkeypatch):
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.3")
        self._seed_requirements(gen, "fastapi>=0.115.0\npyyaml>=6.0.0\n")
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        rc = postcreate.patch_android_requirements(gen)
        assert rc == 0

    def test_version_extracted_from_pyproject(self, tmp_path, postcreate, monkeypatch):
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=1.2.3")
        self._seed_requirements(gen, "kohakuvault>=1.2.3\n")
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "/releases/download/v1.2.3/" in text
        assert "kohakuvault-1.2.3-cp313-cp313-android_24_arm64_v8a.whl" in text

    def test_drops_pymupdf_line(self, tmp_path, postcreate, monkeypatch):
        # pymupdf has no Chaquopy wheel and our consumers
        # lazy-import → Android build needs to drop the line
        # entirely so Chaquopy's pip doesn't try (and fail) to
        # install it.  General ``pip install kohakuterrarium``
        # gets pymupdf via the core dep; only the Android
        # postcreate carves it out.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "fastapi>=0.115.0\npymupdf>=1.24.0\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        # pymupdf line gone entirely.
        assert "pymupdf" not in text.lower()
        # Other deps preserved.
        assert "fastapi>=0.115.0" in text
        assert "kohakuvault @ " in text

    def test_drops_gitpython_line(self, tmp_path, postcreate, monkeypatch):
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "fastapi>=0.115.0\ngitpython>=3.1.0\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "gitpython" not in text.lower()

    def test_drops_bcrypt_line(self, tmp_path, postcreate, monkeypatch):
        # bcrypt 4.x+ is Rust/PyO3; Chaquopy's curated index only
        # has 3.2.2.  Pinning ``>=4`` would either need
        # android-dep-collection to build it OR a drop.  We chose
        # drop because Android is single-tenant (L4 multi-user auth
        # not used) and api/auth/crypto.py imports bcrypt lazily.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "fastapi>=0.115.0\nbcrypt>=4.0.0\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "bcrypt" not in text.lower()
        assert "fastapi>=0.115.0" in text

    def test_drops_pywebview_line(self, tmp_path, postcreate, monkeypatch):
        # pywebview leaks into Android via Briefcase's parent-level
        # ``requires`` concatenation (the launcher venv spec lists
        # pywebview).  No Android wheel exists for pywebview — the
        # Android side uses a native WebView via MainActivity.java
        # — so postcreate strips it just like pymupdf.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "fastapi>=0.115.0\npywebview==6.1\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "pywebview" not in text.lower()
        assert "fastapi>=0.115.0" in text

    def test_rewrites_pydantic_core_to_url_refs(
        self, tmp_path, postcreate, monkeypatch
    ):
        # pydantic-core has no Chaquopy wheel and we host our own
        # via Kohaku-Lab/android-dep-collection.  postcreate must
        # rewrite the dep spec to direct URL refs for arm64 + x86_64.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "pydantic>=2.0.0\npydantic-core>=2.41.1\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        # Original pydantic-core spec line gone.
        assert "pydantic-core>=2.41.1" not in text
        # Two URL refs landed for pydantic-core.
        assert (
            "pydantic-core @ https://github.com/Kohaku-Lab/android-dep-collection/"
            "releases/download/v2026.05.23/"
            "pydantic_core-2.41.1-cp313-cp313-android_24_arm64_v8a.whl"
            " ; platform_machine == 'aarch64'"
        ) in text
        assert (
            "pydantic-core @ https://github.com/Kohaku-Lab/android-dep-collection/"
            "releases/download/v2026.05.23/"
            "pydantic_core-2.41.1-cp313-cp313-android_24_x86_64.whl"
            " ; platform_machine == 'x86_64'"
        ) in text
        # pydantic shell (pure-Python) preserved.
        assert "pydantic>=2.0.0" in text

    def test_rewrites_safetensors_to_url_refs(self, tmp_path, postcreate, monkeypatch):
        # safetensors is a model2vec transitive (Rust/PyO3, no
        # Chaquopy wheel).  Briefcase emits it into Android's
        # requirements.txt; postcreate replaces with URL refs to
        # android-dep-collection's v2026.05.24 release.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "model2vec>=0.8.0\nsafetensors>=0.6.0\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "safetensors>=0.6.0" not in text
        # safetensors actually ships as cp38-abi3 (upstream Cargo
        # enables pyo3/abi3-py38) — verified against v2026.05.24
        # release artifacts.
        assert (
            "safetensors @ https://github.com/Kohaku-Lab/android-dep-collection/"
            "releases/download/v2026.05.24/"
            "safetensors-0.7.0-cp38-abi3-android_24_arm64_v8a.whl"
            " ; platform_machine == 'aarch64'"
        ) in text
        assert (
            "safetensors @ https://github.com/Kohaku-Lab/android-dep-collection/"
            "releases/download/v2026.05.24/"
            "safetensors-0.7.0-cp38-abi3-android_24_x86_64.whl"
            " ; platform_machine == 'x86_64'"
        ) in text
        # model2vec shell preserved (it's pure-Python; only its
        # native deps get URL-ref'd).
        assert "model2vec>=0.8.0" in text

    def test_rewrites_tokenizers_to_url_refs(self, tmp_path, postcreate, monkeypatch):
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "tokenizers>=0.20.0\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "tokenizers>=0.20.0" not in text
        # tokenizers actually ships as cp310-abi3 (upstream Cargo
        # pins pyo3/abi3-py310) — verified against v2026.05.24
        # release artifacts.
        assert (
            "tokenizers-0.23.1-cp310-abi3-android_24_arm64_v8a.whl"
            " ; platform_machine == 'aarch64'"
        ) in text
        assert (
            "tokenizers-0.23.1-cp310-abi3-android_24_x86_64.whl"
            " ; platform_machine == 'x86_64'"
        ) in text

    def test_rewrites_primp_with_abi3_tag(self, tmp_path, postcreate, monkeypatch):
        # primp ships as ABI3 — the URL filename must use an abi3
        # tag, NOT ``cp313-cp313``.  v1.3.0's source-tree Cargo
        # pins ``pyo3/abi3-py310`` so our cross-built wheel emits
        # ``cp310-abi3`` (PyPI's manylinux releases use cp38-abi3,
        # but that's a different Cargo features set on a separate
        # CI path).  Verified against the v2026.05.24 release.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "ddgs>=9.0.0\nprimp>=1.2.3\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "primp>=1.2.3" not in text
        assert "primp-1.3.0-cp310-abi3-android_24_arm64_v8a.whl" in text
        assert "primp-1.3.0-cp310-abi3-android_24_x86_64.whl" in text
        # No mismatched python/abi tags should appear on primp lines.
        primp_lines = [line for line in text.splitlines() if "primp-1.3.0" in line]
        for line in primp_lines:
            assert "cp313-cp313" not in line
            assert "cp38-abi3" not in line
        # ddgs shell preserved (pure-Python).
        assert "ddgs>=9.0.0" in text

    def test_new_url_refs_all_idempotent(self, tmp_path, postcreate, monkeypatch):
        # All four URL-ref packages together — re-run produces
        # identical output (no line multiplication).
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "pydantic-core>=2.41.1\nsafetensors>=0.6.0\n"
            "tokenizers>=0.20.0\nprimp>=1.2.3\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        first = (gen / "requirements.txt").read_text(encoding="utf-8")
        postcreate.patch_android_requirements(gen)
        second = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert first == second

    def test_pydantic_core_idempotent_on_re_run(
        self, tmp_path, postcreate, monkeypatch
    ):
        # Re-running postcreate.py shouldn't multiply the
        # pydantic-core URL refs the same way it shouldn't
        # multiply kohakuvault's.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(gen, "pydantic-core>=2.41.1\nkohakuvault>=0.8.5\n")
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        first = (gen / "requirements.txt").read_text(encoding="utf-8")
        postcreate.patch_android_requirements(gen)
        second = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert first == second

    def test_strips_uvicorn_standard_extra(self, tmp_path, postcreate, monkeypatch):
        # uvicorn[standard] pulls uvloop + httptools + watchfiles
        # — none have Chaquopy wheels.  Bare ``uvicorn`` works
        # via pure-Python asyncio + h11.  postcreate strips the
        # extra without dropping uvicorn entirely.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.5")
        self._seed_requirements(
            gen,
            "uvicorn[standard]>=0.34.0\nkohakuvault>=0.8.5\n",
        )
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        # ``[standard]`` extra gone; the bare uvicorn line kept.
        assert "uvicorn[standard]" not in text
        assert "uvicorn>=0.34.0" in text

    def test_extras_form_matched(self, tmp_path, postcreate, monkeypatch):
        # Briefcase may emit ``kohakuvault[extra]>=0.8.3`` if a
        # consumer ever uses extras.  Match the package name
        # regardless of extras / equality operator.
        gen = _build_fake_generated(tmp_path)
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        self._seed_pyproject(fake_repo, "kohakuvault>=0.8.3")
        self._seed_requirements(gen, "kohakuvault[fast]>=0.8.3\n")
        monkeypatch.setattr(postcreate, "REPO_ROOT", fake_repo)
        postcreate.patch_android_requirements(gen)
        text = (gen / "requirements.txt").read_text(encoding="utf-8")
        assert "kohakuvault[fast]>=0.8.3" not in text
        assert "kohakuvault @ https://github.com/" in text


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
