"""Decide whether the repo is due for an automatic patch release.

A patch release is cut when the newest stable release has aged past a
threshold *and* enough nightlies have shipped since then, i.e. there is
a meaningful backlog of merged work that stable-channel users are not
getting. The workflow feeds this script the GitHub release listing and
applies its decision.

Usage::

    python scripts/auto_patch_release.py \
        --releases releases.json \
        --now 2026-08-27T00:00:00Z \
        --commits-since 12 \
        --github-output "$GITHUB_OUTPUT"
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STABLE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
NIGHTLY_TAG_RE = re.compile(r"^nightly-\d{8}$")
RELEASE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_args() -> argparse.Namespace:
    """Parse release-listing inputs and the release-cadence thresholds."""
    p = argparse.ArgumentParser(description="Decide on an automatic patch release.")
    p.add_argument(
        "--releases",
        type=Path,
        required=True,
        help="JSON array produced by `gh release list --json ...`.",
    )
    p.add_argument("--now", required=True, help="Current UTC time, ISO 8601.")
    p.add_argument(
        "--project-version",
        default="",
        help="Version currently in pyproject.toml (read from it when omitted).",
    )
    p.add_argument(
        "--commits-since",
        type=int,
        default=None,
        help="Commit count between the latest stable tag and HEAD.",
    )
    p.add_argument("--min-age-days", type=float, default=7.0)
    p.add_argument(
        "--min-nightlies",
        type=int,
        default=2,
        help="Nightlies since the last release must exceed this count.",
    )
    p.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Where to read the current project version from.",
    )
    p.add_argument("--github-output", type=Path, default=None)
    return p.parse_args()


def _parse_time(value: str) -> datetime | None:
    """Parse a GitHub ISO 8601 timestamp into an aware datetime."""
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _release_time(entry: dict) -> datetime | None:
    """Return a release's publish time, falling back to its creation time."""
    for key in ("publishedAt", "createdAt"):
        stamp = _parse_time(str(entry.get(key) or ""))
        if stamp is not None:
            return stamp
    return None


def latest_stable(releases: list[dict]) -> dict | None:
    """Return the newest published, non-prerelease ``vX.Y.Z`` release."""
    candidates = []
    for entry in releases:
        if not isinstance(entry, dict):
            continue
        if entry.get("isDraft") or entry.get("isPrerelease"):
            continue
        if not STABLE_TAG_RE.match(str(entry.get("tagName") or "")):
            continue
        stamp = _release_time(entry)
        if stamp is None:
            continue
        candidates.append((stamp, entry))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def count_nightlies_since(releases: list[dict], after: datetime) -> int:
    """Count dated nightly releases created after the given moment."""
    total = 0
    for entry in releases:
        if not isinstance(entry, dict):
            continue
        if not NIGHTLY_TAG_RE.match(str(entry.get("tagName") or "")):
            continue
        stamp = _release_time(entry)
        if stamp is not None and stamp > after:
            total += 1
    return total


def _version_tuple(version: str) -> tuple[int, ...] | None:
    """Parse ``X.Y.Z`` into a comparable tuple, or None when it is not one."""
    match = RELEASE_VERSION_RE.match(version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def next_version(tag: str, project_version: str) -> str:
    """Pick the version to release: a pending manual bump, else tag patch + 1."""
    major, minor, patch = (int(part) for part in STABLE_TAG_RE.match(tag).groups())
    pending = _version_tuple(project_version)
    if pending is not None and pending > (major, minor, patch):
        return ".".join(str(part) for part in pending)
    return f"{major}.{minor}.{patch + 1}"


def _tag_exists(releases: list[dict], tag: str) -> bool:
    """Return whether a release already occupies the given tag."""
    return any(str(entry.get("tagName") or "") == tag for entry in releases)


def _skip(reason: str) -> dict[str, str]:
    """Build the workflow outputs for a no-release decision."""
    return {
        "should_release": "false",
        "next_version": "",
        "previous_tag": "",
        "nightly_count": "0",
        "reason": reason,
    }


def decide(
    releases: list[dict],
    now: datetime,
    project_version: str,
    *,
    commits_since: int | None,
    min_age_days: float,
    min_nightlies: int,
) -> dict[str, str]:
    """Return workflow outputs deciding whether to cut a patch release."""
    stable = latest_stable(releases)
    if stable is None:
        return _skip("no stable release found; nothing to bump from")
    tag = str(stable["tagName"])
    released_at = _release_time(stable)
    age = now - released_at
    if age < timedelta(days=min_age_days):
        return _skip(f"{tag} is {age.days}d old (< {min_age_days}d)")
    nightlies = count_nightlies_since(releases, released_at)
    if nightlies <= min_nightlies:
        return _skip(
            f"only {nightlies} nightly(s) since {tag} (need > {min_nightlies})"
        )
    if commits_since is not None and commits_since <= 0:
        return _skip(f"no commits since {tag}")
    version = next_version(tag, project_version)
    if _tag_exists(releases, f"v{version}"):
        return _skip(f"v{version} already released")
    return {
        "should_release": "true",
        "next_version": version,
        "previous_tag": tag,
        "nightly_count": str(nightlies),
        "reason": (
            f"{tag} is {age.days}d old with {nightlies} nightly(s) since; "
            f"releasing {version}"
        ),
    }


def _load_releases(path: Path) -> list[dict]:
    """Load the release listing, treating an unreadable file as empty."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _project_version(pyproject: Path) -> str:
    """Read the ``[project] version`` currently declared in pyproject.toml."""
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]*)"', text, flags=re.MULTILINE)
    return match.group(1) if match else ""


def _write_github_outputs(path: Path, outputs: dict[str, str]) -> None:
    """Append decision key-value pairs to a GitHub Actions output file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for key, value in outputs.items():
            f.write(f"{key}={value}\n")


def main() -> int:
    """Evaluate the release cadence and emit the workflow decision."""
    args = parse_args()
    now = _parse_time(args.now)
    if now is None:
        raise SystemExit(f"unparseable --now value: {args.now!r}")
    outputs = decide(
        _load_releases(args.releases),
        now,
        args.project_version or _project_version(args.pyproject),
        commits_since=args.commits_since,
        min_age_days=args.min_age_days,
        min_nightlies=args.min_nightlies,
    )
    if args.github_output is not None:
        _write_github_outputs(args.github_output, outputs)
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
