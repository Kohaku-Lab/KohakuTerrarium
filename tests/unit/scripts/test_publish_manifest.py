import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "publish_manifest.py"


def _write_sidecar(
    artifacts_dir: Path,
    *,
    platform: str,
    commit_sha: str | None = "abcdef0123456789abcdef0123456789abcdef01",
):
    payload = {
        "schema": 1,
        "name": "kohakuterrarium",
        "version": "2.0.0.dev20260601030000+abcdef0",
        "build_id": "20260601030000-abcdef0",
        "channel": "nightly",
        "platform": platform,
        "py_abi": "cp313",
        "sha256": "f" * 64,
        "size_bytes": 123,
    }
    if commit_sha is not None:
        payload["commit_sha"] = commit_sha
    path = artifacts_dir / f"kohakuterrarium-{platform}.tar.zst.manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def _publish(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    out = tmp_path / "nightly.json"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--channel",
        "nightly",
        "--release-url-prefix",
        "https://example.test/nightly-20260601",
        "--artifacts-dir",
        str(artifacts_dir),
        "--release-notes-url",
        "https://example.test/notes",
        "--out",
        str(out),
    ]
    return artifacts_dir, out, cmd


def test_preserves_common_commit_sha(tmp_path):
    artifacts_dir, out, cmd = _publish(tmp_path)
    _write_sidecar(artifacts_dir, platform="linux-x64")
    _write_sidecar(artifacts_dir, platform="win-x64")

    subprocess.run(cmd, cwd=REPO_ROOT, check=True, capture_output=True, text=True)

    release = json.loads(out.read_text(encoding="utf-8"))["releases"][0]
    assert release["commit_sha"] == "abcdef0123456789abcdef0123456789abcdef01"


def test_missing_commit_sha_remains_compatible(tmp_path):
    artifacts_dir, out, cmd = _publish(tmp_path)
    _write_sidecar(artifacts_dir, platform="linux-x64", commit_sha=None)

    subprocess.run(cmd, cwd=REPO_ROOT, check=True, capture_output=True, text=True)

    release = json.loads(out.read_text(encoding="utf-8"))["releases"][0]
    assert "commit_sha" not in release


def test_mixed_commit_shas_fail_clearly(tmp_path):
    artifacts_dir, _, cmd = _publish(tmp_path)
    _write_sidecar(
        artifacts_dir,
        platform="linux-x64",
        commit_sha="abcdef0123456789abcdef0123456789abcdef01",
    )
    _write_sidecar(
        artifacts_dir,
        platform="win-x64",
        commit_sha="1234567890abcdef1234567890abcdef12345678",
    )

    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)

    assert result.returncode != 0
    assert "sidecars span multiple commit SHAs" in result.stderr
