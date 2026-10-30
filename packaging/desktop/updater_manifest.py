"""Write the ``latest.json`` the desktop shell's updater reads.

The release job runs this over the collected release assets. Each platform entry
points at a signed installer (Windows: the per-user setup executable, macOS: the
app archive) and carries the signature file's contents.

Usage::

    python packaging/desktop/updater_manifest.py --assets release/ \\
        --base-url https://github.com/<repo>/releases/download/<tag> \\
        --notes-url https://github.com/<repo>/releases/tag/<tag> --out release/latest.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PLATFORMS = {
    "windows-x86_64": re.compile(r"^KohakuTerrarium_(\d+\.\d+\.\d+)_x64-setup\.exe$"),
    "darwin-aarch64": re.compile(
        r"^KohakuTerrarium_(\d+\.\d+\.\d+)_aarch64\.app\.tar\.gz$"
    ),
}


class ManifestError(Exception):
    pass


def collect(assets: Path) -> tuple[str, dict[str, dict]]:
    """Return the shared version and the signed asset for each platform."""
    found: dict[str, tuple[str, Path]] = {}
    for path in sorted(assets.iterdir()):
        for platform, pattern in PLATFORMS.items():
            match = pattern.match(path.name)
            if match:
                found[platform] = (match.group(1), path)
    versions = {version for version, _ in found.values()}
    if not found:
        raise ManifestError(f"no updater assets in {assets}")
    if len(versions) != 1:
        raise ManifestError(f"assets disagree on the version: {sorted(versions)}")
    entries = {}
    for platform, (_, path) in found.items():
        signature = path.with_name(path.name + ".sig")
        if not signature.is_file():
            raise ManifestError(f"{path.name} has no signature file")
        entries[platform] = {
            "name": path.name,
            "signature": signature.read_text(encoding="utf-8").strip(),
        }
    return versions.pop(), entries


def build(version: str, entries: dict[str, dict], base_url: str, notes: str) -> dict:
    return {
        "version": version,
        "notes": notes,
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            platform: {
                "signature": entry["signature"],
                "url": f"{base_url.rstrip('/')}/{entry['name']}",
            }
            for platform, entry in entries.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--notes-url", default="")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    version, entries = collect(args.assets)
    manifest = build(version, entries, args.base_url, args.notes_url)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"updater manifest for {version}: {', '.join(manifest['platforms'])}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
