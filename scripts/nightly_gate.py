"""Decide whether a nightly workflow should build the current commit.

The nightly manifest is the source of truth. If the current commit is already
represented by a retained nightly release, the workflow can skip expensive
matrix builds. Missing or unreadable manifests fail open and allow a build.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--commit-sha", required=True)
    p.add_argument("--force-rebuild", default="false")
    p.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Output file for GitHub Actions key=value pairs.",
    )
    return p.parse_args()


def _truthy(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_manifest(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _version_local_tokens(version: str) -> set[str]:
    if "+" not in version:
        return set()
    local = version.rsplit("+", 1)[1].lower()
    return {token for token in re.split(r"[^0-9a-f]+", local) if token}


def _build_id_tokens(build_id: str) -> set[str]:
    suffix = build_id.lower().rsplit("-", 1)[-1]
    return {token for token in re.split(r"[^0-9a-f]+", suffix) if token}


def _token_matches_commit(token: str, commit: str, short: str) -> bool:
    token = token.lower()
    return (
        token == commit
        or token == short
        or (len(token) >= 7 and commit.startswith(token))
    )


def _commit_sha_matches(value: str, commit: str, short: str) -> bool:
    candidate = value.strip().lower()
    if not candidate:
        return False
    return (
        candidate == commit
        or candidate == short
        or (len(candidate) >= 7 and commit.startswith(candidate))
    )


def _find_matching_release(manifest: dict, commit_sha: str) -> tuple[str, dict] | None:
    commit = commit_sha.lower()
    try:
        _ = int(commit, 16)
    except ValueError:
        return None
    short = commit[:7]
    releases = manifest.get("releases") or []
    if not isinstance(releases, list):
        return None
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        existing_commit = str(rel.get("commit_sha") or "")
        if _commit_sha_matches(existing_commit, commit, short):
            return "commit_sha", rel
        build_tokens = _build_id_tokens(str(rel.get("build_id") or ""))
        if any(_token_matches_commit(token, commit, short) for token in build_tokens):
            return "build_id", rel
        version_tokens = _version_local_tokens(str(rel.get("version") or ""))
        if any(_token_matches_commit(token, commit, short) for token in version_tokens):
            return "version", rel
    return None


def decide(
    manifest_path: Path, commit_sha: str, *, force_rebuild: bool
) -> dict[str, str]:
    if force_rebuild:
        return {
            "should_build": "true",
            "skip_reason": "force_rebuild requested; building",
        }
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        return {
            "should_build": "true",
            "skip_reason": "manifest unavailable; building",
        }
    match = _find_matching_release(manifest, commit_sha)
    if match is None:
        return {"should_build": "true", "skip_reason": ""}
    source, _ = match
    short = commit_sha[:7].lower()
    return {
        "should_build": "false",
        "skip_reason": (
            f"commit {short} already present in nightly manifest via {source}"
        ),
    }


def _write_github_outputs(path: Path, outputs: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for key, value in outputs.items():
            f.write(f"{key}={value}\n")


def main() -> int:
    args = parse_args()
    outputs = decide(
        args.manifest,
        args.commit_sha,
        force_rebuild=_truthy(args.force_rebuild),
    )
    output_path = args.github_output
    if output_path is None:
        raw = os.environ.get("GITHUB_OUTPUT")
        output_path = Path(raw) if raw else None
    if output_path is not None:
        _write_github_outputs(output_path, outputs)
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
