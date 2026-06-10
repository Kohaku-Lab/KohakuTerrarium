import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "nightly_gate.py"


def _run_gate(tmp_path, manifest: dict | str | None, commit: str, *, force=False):
    manifest_path = tmp_path / "nightly.json"
    if isinstance(manifest, dict):
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif isinstance(manifest, str):
        manifest_path.write_text(manifest, encoding="utf-8")

    output_path = tmp_path / "github-output.txt"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--manifest",
        str(manifest_path),
        "--commit-sha",
        commit,
        "--github-output",
        str(output_path),
    ]
    if force:
        cmd.extend(["--force-rebuild", "true"])

    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    outputs = {}
    for line in output_path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        outputs[key] = value
    return result, outputs


def _manifest(*releases):
    return {
        "schema": 1,
        "channel": "nightly",
        "releases": list(releases),
    }


def test_missing_manifest_builds(tmp_path):
    _, outputs = _run_gate(tmp_path, None, "abcdef0123456789")

    assert outputs["should_build"] == "true"
    assert "manifest unavailable" in outputs["skip_reason"]


def test_invalid_manifest_builds(tmp_path):
    _, outputs = _run_gate(tmp_path, "{not-json", "abcdef0123456789")

    assert outputs["should_build"] == "true"
    assert "manifest unavailable" in outputs["skip_reason"]


def test_existing_commit_sha_skips(tmp_path):
    commit = "abcdef0123456789abcdef0123456789abcdef01"
    manifest = _manifest(
        {"version": "2.0.0.dev20260601030000+abcdef0", "commit_sha": commit}
    )

    _, outputs = _run_gate(tmp_path, manifest, commit)

    assert outputs["should_build"] == "false"
    assert commit[:7] in outputs["skip_reason"]


def test_legacy_build_id_short_sha_skips(tmp_path):
    commit = "abcdef0123456789abcdef0123456789abcdef01"
    manifest = _manifest(
        {
            "version": "2.0.0.dev20260601030000+1234567",
            "build_id": "20260601030000-abcdef0",
        }
    )

    _, outputs = _run_gate(tmp_path, manifest, commit)

    assert outputs["should_build"] == "false"
    assert "build_id" in outputs["skip_reason"]


def test_legacy_build_id_timestamp_token_does_not_skip(tmp_path):
    commit = "20260601030000abcdef0123456789abcdef01"
    manifest = _manifest(
        {
            "version": "2.0.0.dev20260601030000+1234567",
            "build_id": "20260601030000-abcdef0",
        }
    )

    _, outputs = _run_gate(tmp_path, manifest, commit)

    assert outputs["should_build"] == "true"
    assert outputs["skip_reason"] == ""


def test_legacy_version_local_short_sha_skips(tmp_path):
    commit = "abcdef0123456789abcdef0123456789abcdef01"
    manifest = _manifest(
        {
            "version": "2.0.0.dev20260601030000+abcdef0",
            "build_id": "20260601030000-1234567",
        }
    )

    _, outputs = _run_gate(tmp_path, manifest, commit)

    assert outputs["should_build"] == "false"
    assert "version" in outputs["skip_reason"]


def test_force_rebuild_builds_even_when_commit_matches(tmp_path):
    commit = "abcdef0123456789abcdef0123456789abcdef01"
    manifest = _manifest(
        {"version": "2.0.0.dev20260601030000+abcdef0", "commit_sha": commit}
    )

    _, outputs = _run_gate(tmp_path, manifest, commit, force=True)

    assert outputs["should_build"] == "true"
    assert "force_rebuild" in outputs["skip_reason"]


def test_different_commit_builds(tmp_path):
    manifest = _manifest(
        {
            "version": "2.0.0.dev20260601030000+abcdef0",
            "commit_sha": "abcdef0123456789abcdef0123456789abcdef01",
        }
    )

    _, outputs = _run_gate(
        tmp_path, manifest, "1234567890abcdef1234567890abcdef12345678"
    )

    assert outputs["should_build"] == "true"
    assert outputs["skip_reason"] == ""
