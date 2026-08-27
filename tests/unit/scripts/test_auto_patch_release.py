import json
import subprocess
import sys

import pytest

NOW = "2026-08-27T03:00:00Z"


def _stable(tag: str, created: str, **overrides) -> dict:
    entry = {
        "tagName": tag,
        "createdAt": created,
        "publishedAt": created,
        "isPrerelease": False,
        "isDraft": False,
    }
    entry.update(overrides)
    return entry


def _nightly(date: str) -> dict:
    return _stable(
        f"nightly-{date}",
        f"{date[:4]}-{date[4:6]}-{date[6:]}T03:00:00Z",
        isPrerelease=True,
    )


@pytest.fixture
def decide(scripts_dir, tmp_path):
    """Run auto_patch_release.py over a release listing and return its outputs."""

    def _decide(releases: list[dict], *args: str, project_version: str = "2.1.0"):
        listing = tmp_path / "releases.json"
        listing.write_text(json.dumps(releases), encoding="utf-8")
        output = tmp_path / "gh-output.txt"
        result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "auto_patch_release.py"),
                "--releases",
                str(listing),
                "--now",
                NOW,
                "--project-version",
                project_version,
                "--github-output",
                str(output),
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        outputs = dict(
            line.split("=", 1)
            for line in output.read_text(encoding="utf-8").splitlines()
        )
        return result, outputs

    return _decide


def _aged_release_with_nightlies() -> list[dict]:
    return [
        _stable("v2.1.0", "2026-08-01T00:00:00Z"),
        _nightly("20260820"),
        _nightly("20260821"),
        _nightly("20260822"),
    ]


def test_aged_release_with_enough_nightlies_bumps_the_patch(decide):
    _, outputs = decide(_aged_release_with_nightlies())

    assert outputs["should_release"] == "true"
    assert outputs["next_version"] == "2.1.1"
    assert outputs["previous_tag"] == "v2.1.0"
    assert outputs["nightly_count"] == "3"


def test_recent_release_is_left_alone(decide):
    releases = [
        _stable("v2.1.0", "2026-08-25T00:00:00Z"),
        _nightly("20260826"),
        _nightly("20260827"),
        _nightly("20260828"),
    ]

    _, outputs = decide(releases)

    assert outputs["should_release"] == "false"
    assert outputs["next_version"] == ""
    assert "2d old" in outputs["reason"]


def test_two_nightlies_is_not_enough(decide):
    releases = [
        _stable("v2.1.0", "2026-08-01T00:00:00Z"),
        _nightly("20260820"),
        _nightly("20260821"),
    ]

    _, outputs = decide(releases)

    assert outputs["should_release"] == "false"
    assert "only 2 nightly(s)" in outputs["reason"]


def test_nightlies_predating_the_release_do_not_count(decide):
    releases = [
        _stable("v2.1.0", "2026-08-15T00:00:00Z"),
        _nightly("20260810"),
        _nightly("20260811"),
        _nightly("20260812"),
        _nightly("20260820"),
    ]

    _, outputs = decide(releases)

    assert outputs["should_release"] == "false"
    assert "only 1 nightly(s)" in outputs["reason"]


def test_prereleases_do_not_anchor_the_bump(decide):
    releases = [
        _stable("v2.2.0rc1", "2026-08-20T00:00:00Z", isPrerelease=True),
        _stable("v2.1.0", "2026-08-01T00:00:00Z"),
        _nightly("20260820"),
        _nightly("20260821"),
        _nightly("20260822"),
    ]

    _, outputs = decide(releases)

    assert outputs["previous_tag"] == "v2.1.0"
    assert outputs["next_version"] == "2.1.1"


def test_draft_releases_do_not_anchor_the_bump(decide):
    releases = [
        _stable("v2.3.0", "2026-08-20T00:00:00Z", isDraft=True),
        *_aged_release_with_nightlies(),
    ]

    _, outputs = decide(releases)

    assert outputs["previous_tag"] == "v2.1.0"
    assert outputs["next_version"] == "2.1.1"


def test_pending_manual_bump_is_released_as_is(decide):
    _, outputs = decide(_aged_release_with_nightlies(), project_version="2.2.0")

    assert outputs["should_release"] == "true"
    assert outputs["next_version"] == "2.2.0"


def test_stale_project_version_still_bumps_from_the_tag(decide):
    _, outputs = decide(_aged_release_with_nightlies(), project_version="2.0.0")

    assert outputs["next_version"] == "2.1.1"


def test_nightly_project_version_still_bumps_from_the_tag(decide):
    _, outputs = decide(
        _aged_release_with_nightlies(),
        project_version="2.0.0.dev20260601030000+abcdef0",
    )

    assert outputs["next_version"] == "2.1.1"


def test_an_existing_target_tag_blocks_the_release(decide):
    releases = [
        _stable("v2.1.1", "2026-08-02T00:00:00Z", isDraft=True),
        *_aged_release_with_nightlies(),
    ]

    _, outputs = decide(releases)

    assert outputs["should_release"] == "false"
    assert "v2.1.1 already released" in outputs["reason"]


def test_no_commits_since_the_tag_blocks_the_release(decide):
    _, outputs = decide(_aged_release_with_nightlies(), "--commits-since", "0")

    assert outputs["should_release"] == "false"
    assert "no commits since v2.1.0" in outputs["reason"]


def test_commits_since_the_tag_allow_the_release(decide):
    _, outputs = decide(_aged_release_with_nightlies(), "--commits-since", "12")

    assert outputs["should_release"] == "true"


def test_thresholds_are_configurable(decide):
    releases = [
        _stable("v2.1.0", "2026-08-25T00:00:00Z"),
        _nightly("20260826"),
    ]

    _, outputs = decide(releases, "--min-age-days", "1", "--min-nightlies", "0")

    assert outputs["should_release"] == "true"
    assert outputs["next_version"] == "2.1.1"


def test_no_stable_release_is_reported_not_crashed(decide):
    _, outputs = decide([_nightly("20260826"), _nightly("20260827")])

    assert outputs["should_release"] == "false"
    assert "no stable release found" in outputs["reason"]


def test_unreadable_listing_is_reported_not_crashed(scripts_dir, tmp_path):
    listing = tmp_path / "releases.json"
    listing.write_text("{not json", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(scripts_dir / "auto_patch_release.py"),
            "--releases",
            str(listing),
            "--now",
            NOW,
            "--project-version",
            "2.1.0",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "should_release=false" in result.stdout
    assert "no stable release found" in result.stdout
