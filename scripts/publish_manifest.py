"""Build / refresh the channel manifest from per-tarball sidecar JSONs.

Input: a directory of ``*.tar.zst`` (or ``.tar.gz``) artifacts plus
their ``*.manifest.json`` sidecars (produced by ``build_release_tree.py``).

Output: a single ``<channel>.json`` whose schema matches the launcher's
:func:`launcher.feeds.fetch_manifest` expectations.

CI runs this after the build matrix finishes uploading per-tag
artifacts, then pushes the resulting JSON to the
``manifests-<channel>`` release (a fixed-name release whose assets are
overwritten on every cut).

Usage::

    python scripts/publish_manifest.py \\
        --channel stable \\
        --release-url-prefix https://github.com/Kohaku-Lab/KohakuTerrarium/releases/download/v1.5.1 \\
        --artifacts-dir release/ \\
        --previous-manifest previous-stable.json \\
        --out stable.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--channel", required=True, choices=("stable", "beta", "nightly"))
    p.add_argument(
        "--release-url-prefix",
        required=True,
        help="URL prefix prepended to each artifact basename to form the download URL.",
    )
    p.add_argument(
        "--artifacts-dir",
        type=Path,
        required=True,
        help="Dir containing the tarballs + their .manifest.json sidecars.",
    )
    p.add_argument(
        "--previous-manifest",
        type=Path,
        default=None,
        help="Existing channel manifest to merge into (preserves older releases).",
    )
    p.add_argument(
        "--release-notes-url",
        default=None,
        help="Optional URL added to the release entry.",
    )
    p.add_argument("--out", type=Path, required=True, help="Output JSON path.")
    p.add_argument(
        "--max-releases",
        type=int,
        default=20,
        help="Keep at most N most-recent releases in the manifest.",
    )
    return p.parse_args()


def _load_sidecars(artifacts_dir: Path) -> list[dict]:
    out: list[dict] = []
    for sidecar in sorted(artifacts_dir.glob("*.manifest.json")):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[publish] skipping {sidecar}: {e}", file=sys.stderr)
            continue
        out.append(data)
    return out


def _group_into_release(sidecars: list[dict], prefix: str, notes: str | None) -> dict:
    """Collapse all per-(plat, abi) sidecars into one release entry."""
    versions = {s.get("version") for s in sidecars}
    if len(versions) != 1:
        raise SystemExit(f"sidecars span multiple versions: {versions}")
    version = next(iter(versions))
    build_ids = {s.get("build_id") for s in sidecars}
    build_id = sorted(b for b in build_ids if b)[-1] if build_ids else ""
    artifacts: list[dict] = []
    for s in sidecars:
        url = _artifact_url(prefix, s)
        artifacts.append(
            {
                "platform": s["platform"],
                "py_abi": s["py_abi"],
                "url": url,
                "sha256": s["sha256"],
                "size_bytes": int(s.get("size_bytes") or 0),
            }
        )
    artifacts.sort(key=lambda a: (a["platform"], a["py_abi"]))
    return {
        "version": version,
        "build_id": build_id,
        "release_notes_url": notes,
        "artifacts": artifacts,
    }


def _artifact_url(prefix: str, sidecar: dict) -> str:
    # Reconstruct the tarball name from the sidecar fields.
    name = (
        f"kohakuterrarium-{sidecar['version']}-{sidecar['platform']}-py"
        f"{_py_minor(sidecar['py_abi'])}"
    )
    # We do NOT carry the suffix in the sidecar — infer from name pattern:
    # sidecar files are ``<tarball>.manifest.json``, so we can look for
    # a tarball stem match in the same dir.
    return f"{prefix.rstrip('/')}/{name}.tar.zst"


def _py_minor(abi: str) -> str:
    digits = "".join(ch for ch in abi if ch.isdigit())
    if len(digits) >= 2:
        return f"{digits[0]}.{digits[1:]}"
    return abi


def main() -> int:
    args = parse_args()
    sidecars = _load_sidecars(args.artifacts_dir)
    if not sidecars:
        raise SystemExit(f"no sidecars found in {args.artifacts_dir}")
    release = _group_into_release(
        sidecars, args.release_url_prefix, args.release_notes_url
    )

    if args.previous_manifest and args.previous_manifest.is_file():
        prev = json.loads(args.previous_manifest.read_text(encoding="utf-8"))
        releases = prev.get("releases") or []
    else:
        releases = []

    # Replace any existing entry for this version, then prepend so the
    # newest is at index 0.
    releases = [r for r in releases if r.get("version") != release["version"]]
    releases.insert(0, release)
    releases = releases[: args.max_releases]

    manifest = {
        "schema": 1,
        "channel": args.channel,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "releases": releases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[publish] wrote {args.out} ({len(releases)} releases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
