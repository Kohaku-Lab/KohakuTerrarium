"""Fetch the bundled Android sandbox binaries.

Reads ``packaging/android/sandbox_manifest.toml``, downloads each
declared binary, verifies its SHA256, and writes it to the layout
the Briefcase Android packager expects:

    packaging/android/bin/<abi>/<canonical_name>

Plus a ``manifest.json`` next to the binaries that the runtime
helper (``utils/mobile_sandbox.ensure_extracted``) reads on first
launch to know which files to copy from APK assets to the writable
private dir.

Usage:

    # Normal run (CI): download + verify; fail on hash mismatch
    python packaging/android/fetch_sandbox.py

    # Refresh mode (operator bumping a version): download but
    # accept any hash, then PRINT the computed hashes so the
    # operator can paste them into the manifest
    python packaging/android/fetch_sandbox.py --refresh

    # Check-only: download + verify but skip writing the bin tree
    # (CI uses this on PRs to validate manifest integrity without
    #  needing the bin output)
    python packaging/android/fetch_sandbox.py --check-only

    # Custom output root (Briefcase config can point here)
    python packaging/android/fetch_sandbox.py --out path/to/bin

Designed to run on any Python 3.10+, no extra deps — uses urllib
+ tarfile + tomllib stdlib only.  Caches downloads in
``~/.cache/kohakuterrarium/android-sandbox/`` so re-runs are fast.
"""

import argparse
import hashlib
import json
import shutil
import stat
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "packaging" / "android" / "sandbox_manifest.toml"
DEFAULT_OUT = REPO_ROOT / "packaging" / "android" / "bin"
DEFAULT_CACHE = Path.home() / ".cache" / "kohakuterrarium" / "android-sandbox"


class FetchError(RuntimeError):
    """Raised on any non-recoverable fetch failure."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch + verify Android sandbox binaries declared in "
            "sandbox_manifest.toml.  Outputs to packaging/android/bin/."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to sandbox_manifest.toml",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output bin directory root (per-ABI subdirs created)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help="Download cache directory",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Refresh mode: don't fail on hash mismatch; "
            "print computed hashes so the operator can update "
            "the manifest after a version bump"
        ),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify hashes but don't write the bin tree",
    )
    args = parser.parse_args(argv)

    try:
        rc = fetch_all(
            manifest=args.manifest,
            out=args.out,
            cache=args.cache,
            refresh=args.refresh,
            check_only=args.check_only,
        )
    except FetchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return rc


def fetch_all(
    *,
    manifest: Path,
    out: Path,
    cache: Path,
    refresh: bool,
    check_only: bool,
) -> int:
    """Download + verify every binary in the manifest.

    Returns 0 on success, non-zero on verification failure
    (suppressed in refresh mode — refresh always returns 0 and
    expects the operator to read the printed hashes).
    """
    data = _load_manifest(manifest)
    abis: list[str] = list(data.get("abis", []))
    binaries: list[dict] = list(data.get("binaries", []))
    if not abis:
        raise FetchError("manifest missing required key 'abis'")
    if not binaries:
        # Empty manifest is legal — we use ``/system/bin/sh`` on
        # Android now, no bundled binaries required.  Skip the
        # download/verify loop entirely so the build doesn't fail
        # on a perfectly-fine no-op manifest.
        if not check_only:
            out.mkdir(parents=True, exist_ok=True)
            _write_runtime_manifest(out, binaries, abis)
        print("no binaries declared in manifest — nothing to fetch")
        return 0

    cache.mkdir(parents=True, exist_ok=True)
    if not check_only:
        out.mkdir(parents=True, exist_ok=True)

    failed: list[str] = []
    computed_hashes: dict[tuple[str, str], str] = {}

    for binary in binaries:
        name = binary["name"]
        artifacts: list[dict] = binary.get("artifacts", [])
        extract_from = binary.get("extract_from_archive")

        for art in artifacts:
            abi = art["abi"]
            url = art["url"]
            expected = art["sha256"]
            archive_type = art.get("archive_type") or binary.get("archive_type")
            arch_tag = art.get("arch_tag")

            if not _url_is_resolvable(url):
                msg = (
                    f"{name}/{abi}: placeholder URL "
                    f"({url!r}); operator must populate this artifact "
                    "before CI can fetch it"
                )
                if refresh:
                    print(f"skip   {name}/{abi}: {url} (placeholder)")
                    continue
                failed.append(msg)
                continue

            try:
                downloaded = _download(url, cache)
            except urllib.error.URLError as e:
                msg = f"{name}/{abi}: download failed: {e}"
                if refresh:
                    print(f"skip   {name}/{abi}: {e}")
                    continue
                failed.append(msg)
                continue

            actual = _sha256_file(downloaded)
            computed_hashes[(name, abi)] = actual

            if refresh:
                print(f"ok     {name}/{abi}: sha256={actual}")
            else:
                if actual != expected:
                    msg = (
                        f"{name}/{abi}: sha256 mismatch "
                        f"(expected {expected}, got {actual})"
                    )
                    failed.append(msg)
                    continue

            if check_only:
                continue

            target = out / abi / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _materialize(
                downloaded=downloaded,
                target=target,
                archive_type=archive_type,
                extract_from=extract_from,
                arch_tag=arch_tag,
            )
            # Mark executable so the APK packager preserves the bit
            # (zip entries default to 0644; Briefcase respects the
            # source mode).
            _make_executable(target)

    if not check_only and not refresh:
        _write_runtime_manifest(out, binaries, abis)

    if refresh:
        print()
        print("# Paste these into sandbox_manifest.toml:")
        for (name, abi), value in computed_hashes.items():
            print(f'#   binaries[{name}].artifacts[{abi}].sha256 = "{value}"')
        return 0

    if failed:
        for msg in failed:
            print(f"error: {msg}", file=sys.stderr)
        return 1
    return 0


def _load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise FetchError(f"manifest not found: {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def _url_is_resolvable(url: str) -> bool:
    """``True`` iff ``url`` looks like a real downloadable artifact.

    The manifest's placeholder URLs (``https://example.invalid/...``)
    are skipped instead of treated as download failures so an
    operator working through the manifest can advance incrementally
    rather than all-or-nothing.
    """
    return not url.startswith("https://example.invalid/")


def _download(url: str, cache: Path) -> Path:
    """Fetch ``url`` into ``cache``, returning the local path.

    Uses the URL's last path segment + sha-of-URL as the cache
    filename so re-runs are cache hits.  No range / resume; if a
    download is partial we discover that via the SHA verification
    step.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    name = url.rsplit("/", 1)[-1] or "artifact"
    cache_path = cache / f"{digest}-{name}"
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path
    print(f"  download {url}", file=sys.stderr)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "kohakuterrarium-sandbox-fetcher/1.0",
        },
    )
    tmp = cache_path.with_suffix(cache_path.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out)
    tmp.replace(cache_path)
    return cache_path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _materialize(
    *,
    downloaded: Path,
    target: Path,
    archive_type: str | None,
    extract_from: str | None,
    arch_tag: str | None,
) -> None:
    """Copy / extract the downloaded artifact into ``target``."""
    if not archive_type:
        # Direct binary download — just copy.
        shutil.copyfile(downloaded, target)
        return

    if archive_type == "tar.gz":
        if not extract_from:
            raise FetchError(
                f"{downloaded.name}: archive_type=tar.gz requires "
                "extract_from_archive in the manifest"
            )
        member_name = extract_from.format(arch_tag=arch_tag or "")
        with tarfile.open(downloaded, "r:gz") as tf:
            member = _find_member(tf, member_name)
            if member is None:
                raise FetchError(
                    f"{downloaded.name}: archive does not contain " f"{member_name!r}"
                )
            extracted = tf.extractfile(member)
            if extracted is None:
                raise FetchError(
                    f"{downloaded.name}: member {member_name!r} is "
                    "not a regular file"
                )
            target.write_bytes(extracted.read())
        return

    raise FetchError(f"unsupported archive_type {archive_type!r} (need tar.gz or none)")


def _find_member(tf: tarfile.TarFile, name: str) -> tarfile.TarInfo | None:
    # tarfile.getmember is strict; fall back to scanning so we
    # match either exact or trailing-segment paths.
    try:
        return tf.getmember(name)
    except KeyError:
        pass
    for member in tf.getmembers():
        if member.name == name or member.name.endswith("/" + name):
            return member
    return None


def _make_executable(path: Path) -> None:
    """Add the user-exec bit; no-op on Windows where POSIX modes
    aren't honoured."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_runtime_manifest(out: Path, binaries: list[dict], abis: list[str]) -> None:
    """Drop a small ``manifest.json`` next to the per-ABI bin dirs.

    The runtime extraction path
    (``utils.mobile_sandbox.ensure_extracted``) reads this on first
    launch to know which files to copy from APK assets to the
    writable private dir.  ABI-agnostic content — the APK only
    includes the binaries for the device's actual ABI, so the
    extraction-time check is "do we have all these names" rather
    than "do we have the right ABI."
    """
    names = [b["name"] for b in binaries]
    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps({"binaries": names, "abis": abis}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
