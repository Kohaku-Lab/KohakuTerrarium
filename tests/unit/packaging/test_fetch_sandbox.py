"""Unit tests for ``packaging/android/fetch_sandbox.py``.

Pins the contract under offline conditions — every URL read is
monkeypatched so the test suite NEVER hits the network.  The
fetcher's job is download → verify → extract → place; we exercise
each piece against a hand-built fixture manifest.
"""

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

_FETCH_SANDBOX_PATH = (
    Path(__file__).resolve().parents[3] / "packaging" / "android" / "fetch_sandbox.py"
)


@pytest.fixture(scope="module")
def fetch_sandbox():
    """Load the script as a module without it being on the package
    tree.  Module-scoped — load once for the whole test file."""
    spec = importlib.util.spec_from_file_location("fetch_sandbox", _FETCH_SANDBOX_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_sandbox"] = module
    spec.loader.exec_module(module)
    return module


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_tarball(payload: bytes, member_name: str) -> bytes:
    """Build an in-memory tar.gz containing one file at
    ``member_name`` with the given payload.  Used so tests can
    exercise the archive-extract path without touching the network."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name=member_name)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


@pytest.fixture
def offline(monkeypatch, fetch_sandbox):
    """Replace ``_download`` with a fixture-table lookup so tests
    can pre-stage what each URL "returns" without network IO."""
    table: dict[str, bytes] = {}

    def fake_download(url, cache):
        if url not in table:
            from urllib.error import URLError

            raise URLError(f"unmocked URL in test: {url}")
        path = cache / hashlib.sha256(url.encode()).hexdigest()[:16]
        path.write_bytes(table[url])
        return path

    monkeypatch.setattr(fetch_sandbox, "_download", fake_download)
    return table


def _write_manifest(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestNormalRun:
    def test_direct_binary_download_and_verify(self, tmp_path, fetch_sandbox, offline):
        payload = b"#!/system/bin/sh\necho hi\n"
        offline["https://example.com/busybox-arm64"] = payload

        manifest = tmp_path / "manifest.toml"
        _write_manifest(
            manifest,
            f"""
abis = ["arm64-v8a"]
[[binaries]]
name = "busybox"
version = "1.36.1"
  [[binaries.artifacts]]
  abi = "arm64-v8a"
  url = "https://example.com/busybox-arm64"
  sha256 = "{_sha256(payload)}"
""",
        )

        rc = fetch_sandbox.fetch_all(
            manifest=manifest,
            out=tmp_path / "out",
            cache=tmp_path / "cache",
            refresh=False,
            check_only=False,
        )
        assert rc == 0
        target = tmp_path / "out" / "arm64-v8a" / "busybox"
        assert target.is_file()
        assert target.read_bytes() == payload
        # Runtime manifest landed alongside.
        runtime_manifest = json.loads(
            (tmp_path / "out" / "manifest.json").read_text(encoding="utf-8")
        )
        assert runtime_manifest == {
            "binaries": ["busybox"],
            "abis": ["arm64-v8a"],
        }

    def test_archive_extraction(self, tmp_path, fetch_sandbox, offline):
        rg_binary = b"#!fake-ripgrep\n"
        tarball = _make_tarball(
            rg_binary, member_name="ripgrep-14.1.0-aarch64-unknown-linux-musl/rg"
        )
        offline["https://example.com/ripgrep-arm64.tar.gz"] = tarball

        manifest = tmp_path / "manifest.toml"
        _write_manifest(
            manifest,
            f"""
abis = ["arm64-v8a"]
[[binaries]]
name = "rg"
version = "14.1.0"
extract_from_archive = "ripgrep-14.1.0-{{arch_tag}}-unknown-linux-musl/rg"
  [[binaries.artifacts]]
  abi = "arm64-v8a"
  url = "https://example.com/ripgrep-arm64.tar.gz"
  archive_type = "tar.gz"
  arch_tag = "aarch64"
  sha256 = "{_sha256(tarball)}"
""",
        )

        rc = fetch_sandbox.fetch_all(
            manifest=manifest,
            out=tmp_path / "out",
            cache=tmp_path / "cache",
            refresh=False,
            check_only=False,
        )
        assert rc == 0
        target = tmp_path / "out" / "arm64-v8a" / "rg"
        assert target.is_file()
        assert target.read_bytes() == rg_binary


class TestVerificationFailure:
    def test_sha_mismatch_returns_nonzero(self, tmp_path, fetch_sandbox, offline):
        offline["https://example.com/busybox-arm64"] = b"actual content"

        manifest = tmp_path / "manifest.toml"
        _write_manifest(
            manifest,
            """
abis = ["arm64-v8a"]
[[binaries]]
name = "busybox"
version = "1.36.1"
  [[binaries.artifacts]]
  abi = "arm64-v8a"
  url = "https://example.com/busybox-arm64"
  sha256 = "deadbeef00000000000000000000000000000000000000000000000000000000"
""",
        )
        rc = fetch_sandbox.fetch_all(
            manifest=manifest,
            out=tmp_path / "out",
            cache=tmp_path / "cache",
            refresh=False,
            check_only=False,
        )
        assert rc == 1
        # The corrupt download must NOT land in the out tree — we
        # don't want CI to silently ship a wrong binary.
        assert not (tmp_path / "out" / "arm64-v8a" / "busybox").exists()


class TestRefreshMode:
    def test_refresh_prints_hashes_and_succeeds(
        self, tmp_path, fetch_sandbox, offline, capsys
    ):
        payload = b"new-version-payload"
        offline["https://example.com/busybox-arm64"] = payload

        manifest = tmp_path / "manifest.toml"
        _write_manifest(
            manifest,
            """
abis = ["arm64-v8a"]
[[binaries]]
name = "busybox"
version = "1.36.2"
  [[binaries.artifacts]]
  abi = "arm64-v8a"
  url = "https://example.com/busybox-arm64"
  sha256 = "deadbeef00000000000000000000000000000000000000000000000000000000"
""",
        )
        rc = fetch_sandbox.fetch_all(
            manifest=manifest,
            out=tmp_path / "out",
            cache=tmp_path / "cache",
            refresh=True,
            check_only=False,
        )
        # Refresh always returns 0; operator reads the printed hash.
        assert rc == 0
        captured = capsys.readouterr()
        assert _sha256(payload) in captured.out


class TestPlaceholderUrls:
    def test_placeholder_url_skipped_in_refresh(self, tmp_path, fetch_sandbox, offline):
        manifest = tmp_path / "manifest.toml"
        _write_manifest(
            manifest,
            """
abis = ["arm64-v8a"]
[[binaries]]
name = "git"
version = "2.43.0"
  [[binaries.artifacts]]
  abi = "arm64-v8a"
  url = "https://example.invalid/PLACEHOLDER_git_arm64"
  sha256 = "REPLACE_ME"
""",
        )
        # Placeholder URL → no download attempted; refresh succeeds.
        rc = fetch_sandbox.fetch_all(
            manifest=manifest,
            out=tmp_path / "out",
            cache=tmp_path / "cache",
            refresh=True,
            check_only=False,
        )
        assert rc == 0

    def test_placeholder_url_fails_normal_run(self, tmp_path, fetch_sandbox, offline):
        # In normal CI mode a placeholder must fail the build — we
        # don't want an APK shipping without git just because
        # someone forgot to populate the manifest.
        manifest = tmp_path / "manifest.toml"
        _write_manifest(
            manifest,
            """
abis = ["arm64-v8a"]
[[binaries]]
name = "git"
version = "2.43.0"
  [[binaries.artifacts]]
  abi = "arm64-v8a"
  url = "https://example.invalid/PLACEHOLDER_git_arm64"
  sha256 = "REPLACE_ME"
""",
        )
        rc = fetch_sandbox.fetch_all(
            manifest=manifest,
            out=tmp_path / "out",
            cache=tmp_path / "cache",
            refresh=False,
            check_only=False,
        )
        assert rc == 1


class TestCheckOnly:
    def test_check_only_verifies_but_does_not_write(
        self, tmp_path, fetch_sandbox, offline
    ):
        payload = b"some-bytes"
        offline["https://example.com/busybox-arm64"] = payload
        manifest = tmp_path / "manifest.toml"
        _write_manifest(
            manifest,
            f"""
abis = ["arm64-v8a"]
[[binaries]]
name = "busybox"
version = "1.36.1"
  [[binaries.artifacts]]
  abi = "arm64-v8a"
  url = "https://example.com/busybox-arm64"
  sha256 = "{_sha256(payload)}"
""",
        )
        rc = fetch_sandbox.fetch_all(
            manifest=manifest,
            out=tmp_path / "out",
            cache=tmp_path / "cache",
            refresh=False,
            check_only=True,
        )
        assert rc == 0
        # Verified but did NOT write — useful for CI PR checks
        # that don't want a large bin tree in the artifact upload.
        assert not (tmp_path / "out" / "arm64-v8a" / "busybox").exists()


class TestManifestSchemaErrors:
    def test_missing_manifest(self, tmp_path, fetch_sandbox):
        with pytest.raises(fetch_sandbox.FetchError, match="manifest not found"):
            fetch_sandbox.fetch_all(
                manifest=tmp_path / "nope.toml",
                out=tmp_path / "out",
                cache=tmp_path / "cache",
                refresh=False,
                check_only=False,
            )

    def test_empty_manifest(self, tmp_path, fetch_sandbox):
        manifest = tmp_path / "manifest.toml"
        manifest.write_text("# empty\n", encoding="utf-8")
        with pytest.raises(fetch_sandbox.FetchError, match="missing required"):
            fetch_sandbox.fetch_all(
                manifest=manifest,
                out=tmp_path / "out",
                cache=tmp_path / "cache",
                refresh=False,
                check_only=False,
            )
