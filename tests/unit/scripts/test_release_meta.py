"""Execute release.yml's ``meta`` resolution and pin what it decides.

The whole release process keys off this one shell step: which version is
built, which channel it lands in, whether it is a prerelease, and how many
dated nightlies survive the prune. A mistake here is only observable in
production, so the script is run here directly rather than reviewed by eye.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash to run the workflow step"
)


def _meta_script() -> str:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["meta"]["steps"]
    return next(s for s in steps if s.get("id") == "m")["run"]


def _run(tmp_path, *, ref="", version="", channel="", git_ref, git_ref_name):
    script = _meta_script()
    for placeholder, value in (
        ("${{ inputs.ref }}", ref),
        ("${{ inputs.version }}", version),
        ("${{ inputs.channel }}", channel),
    ):
        script = script.replace(placeholder, value)

    out = tmp_path / "gh_output"
    out.touch()

    # The step reads the published nightly manifest. Stub curl to fail so the
    # test stays offline and exercises the no-previous-manifest fallback.
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    curl = stub_dir / "curl"
    curl.write_text("#!/bin/sh\nexit 1\n")
    curl.chmod(0o755)
    subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        env={
            "PATH": f"{stub_dir}:{REPO_ROOT / '.venv' / 'bin'}:/usr/bin:/bin:/usr/local/bin",
            "GITHUB_REF": git_ref,
            "GITHUB_REF_NAME": git_ref_name,
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
    )
    parsed = {}
    for line in out.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


class TestReleaseMeta:
    def test_stable_tag_push_builds_the_tag_and_prunes_the_backlog(self, tmp_path):
        meta = _run(tmp_path, git_ref="refs/tags/v2.1.2", git_ref_name="v2.1.2")
        assert meta["version"] == "2.1.2"
        assert meta["channel"] == "stable"
        assert meta["tag"] == "v2.1.2"
        assert meta["prerelease"] == "false"
        # The tag already carries the version; nothing is stamped.
        assert meta["stamp"] == "false"
        # A release supersedes the nightly backlog.
        assert meta["keep_nightlies"] == "1"
        assert meta["max_releases"] == ""

    def test_nightly_dispatch_is_dated_prerelease_and_bounded(self, tmp_path):
        meta = _run(
            tmp_path,
            ref="main",
            version="2.1.2.dev20260904",
            channel="nightly",
            git_ref="refs/heads/main",
            git_ref_name="main",
        )
        assert meta["version"] == "2.1.2.dev20260904"
        assert meta["channel"] == "nightly"
        assert meta["tag"].startswith("nightly-")
        assert meta["title"].startswith("Nightly ")
        assert meta["prerelease"] == "true"
        assert meta["stamp"] == "true"
        # Manifest entries must not outlive the releases they point at.
        assert meta["keep_nightlies"] == meta["max_releases"] == "5"
        # Sidecars carry this into the channel manifest, which is what anchors
        # the NEXT nightly's commit list.
        assert meta["build_id"].split("-")[-1] == meta["sha"][:7]
        # No previous manifest reachable: fall back to the last stable tag
        # rather than inventing a range.
        assert meta["notes_since"] == "auto"

    def test_manual_stable_dispatch_prunes_nothing(self, tmp_path):
        # Not a release: deleting four nightlies because someone pressed
        # "Run workflow" would be a surprise.
        meta = _run(
            tmp_path,
            channel="stable",
            git_ref="refs/heads/main",
            git_ref_name="main",
        )
        assert meta["keep_nightlies"] == ""
        assert meta["stamp"] == "false"
        # Falls back to pyproject as the source of truth.
        assert meta["version"] and meta["version"][0].isdigit()

    def test_prerelease_tags_land_in_beta(self, tmp_path):
        for tag in ("v2.2.0rc1", "v2.2.0b1"):
            meta = _run(tmp_path, git_ref=f"refs/tags/{tag}", git_ref_name=tag)
            assert meta["channel"] == "beta", tag
            assert meta["prerelease"] == "true", tag

    def test_dev_tag_never_publishes_as_latest(self, tmp_path):
        # A v2.0.0.dev6 tag once shipped as a stable release.
        meta = _run(
            tmp_path, git_ref="refs/tags/v2.0.0.dev6", git_ref_name="v2.0.0.dev6"
        )
        assert meta["prerelease"] == "true"
