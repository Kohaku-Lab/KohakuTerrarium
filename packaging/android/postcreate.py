"""Post-``briefcase create android`` merge script.

Briefcase Android does NOT have a native "merge a tree of files into
the generated project" mechanism (no such cookiecutter feature, no
such ``[tool.briefcase]`` key).  An earlier iteration of this folder
used a made-up ``template_overrides`` config key — Briefcase silently
ignored it and the custom Java never landed in the APK.

The supported pattern is: run a script between
``briefcase create android`` and ``briefcase update android`` that
copies the override tree into the generated project.  This script is
that script.

Layout this script expects:

    packaging/android/
    ├── template/                       ← source of overrides
    │   └── app/src/main/java/...       ← Java to drop in
    └── bin/                            ← CI-populated busybox tree
        ├── manifest.json
        ├── arm64-v8a/busybox
        ├── armeabi-v7a/busybox
        └── x86_64/busybox

After Briefcase generates the project under
``build/kohakuterrarium/android/gradle/app/``, run this:

    python packaging/android/postcreate.py

It:

  1. Copies ``template/app/src/main/java/org/kohaku/terrarium/*.java``
     into the generated tree's same path.
  2. Copies ``bin/`` into ``build/.../app/src/main/assets/sandbox/bin/``.
  3. Removes Briefcase's default placeholder Activity (it ships
     ``org.beeware.android.MainActivity``; we use our own).
  4. Patches the generated ``AndroidManifest.xml`` so the launcher
     activity points at our ``MainActivity`` instead of beeware's.

CI runs this between ``create`` and ``build``.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "packaging" / "android" / "template"
BIN_DIR = REPO_ROOT / "packaging" / "android" / "bin"
GENERATED_PROJECT = (
    REPO_ROOT / "build" / "kohakuterrarium" / "android" / "gradle" / "app"
)

OUR_PACKAGE = "org.kohaku.terrarium"
OUR_PACKAGE_PATH = "org/kohaku/terrarium"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generated",
        type=Path,
        default=GENERATED_PROJECT,
        help="Path to the Briefcase-generated Android project (app/ root)",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=TEMPLATE_DIR,
        help="Path to the source-of-truth template overrides",
    )
    parser.add_argument(
        "--sandbox",
        type=Path,
        default=BIN_DIR,
        help="Path to the fetcher's bin tree",
    )
    parser.add_argument(
        "--skip-sandbox-check",
        action="store_true",
        help=(
            "Don't fail when sandbox bin/ is missing — useful for "
            "dev iterations where you haven't fetched binaries yet"
        ),
    )
    args = parser.parse_args(argv)

    gen = args.generated
    if not gen.is_dir():
        print(
            f"error: generated project not found at {gen}; "
            "run ``briefcase create android`` first",
            file=sys.stderr,
        )
        return 2

    rc = 0
    rc |= copy_java_overrides(args.template, gen)
    rc |= copy_sandbox_assets(args.sandbox, gen, args.skip_sandbox_check)
    rc |= copy_wheelhouse(gen)
    rc |= patch_launcher_activity(gen)
    rc |= patch_allow_backup(gen)
    rc |= remove_default_activity(gen)
    return rc


def copy_java_overrides(template_dir: Path, generated: Path) -> int:
    """Copy ``template/app/src/main/java/org/kohaku/terrarium/*.java``
    into the generated tree, creating the package dir if needed.
    """
    src_root = template_dir / "app" / "src" / "main" / "java" / OUR_PACKAGE_PATH
    if not src_root.is_dir():
        print(f"error: template java dir missing: {src_root}", file=sys.stderr)
        return 1
    dst_root = generated / "src" / "main" / "java" / OUR_PACKAGE_PATH
    dst_root.mkdir(parents=True, exist_ok=True)

    count = 0
    for src in src_root.glob("*.java"):
        dst = dst_root / src.name
        shutil.copy2(src, dst)
        count += 1
        print(f"  java   {src.name} -> {dst.relative_to(generated)}")
    if count == 0:
        print("warning: no .java files found in template", file=sys.stderr)
    return 0


def copy_sandbox_assets(sandbox_dir: Path, generated: Path, skip_check: bool) -> int:
    """Copy ``packaging/android/bin/<abi>/*`` into
    ``app/src/main/assets/sandbox/bin/<abi>/*``."""
    if not sandbox_dir.is_dir():
        msg = f"sandbox bin dir missing: {sandbox_dir}"
        if skip_check:
            print(f"warning: {msg} (skipping per --skip-sandbox-check)")
            return 0
        print(
            f"error: {msg}.  Run "
            "``python packaging/android/fetch_sandbox.py`` first.",
            file=sys.stderr,
        )
        return 1

    dst_root = generated / "src" / "main" / "assets" / "sandbox" / "bin"
    # Replace wholesale — easier to reason about than incremental
    # sync, and the bin tree is small.
    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    for src in sandbox_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(sandbox_dir)
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    print(f"  sandbox  {copied} file(s) -> {dst_root.relative_to(generated)}")
    return 0


def copy_wheelhouse(generated: Path) -> int:
    """Copy ``./wheels/*.whl`` into the generated Gradle app dir.

    The Briefcase config sets
    ``requirement_installer_args = ["--find-links", "wheels"]``,
    which Chaquopy honours when running pip — but the path is
    resolved RELATIVE to the gradle app dir
    (``build/kohakuterrarium/android/gradle/app/``), not the
    repo root.  CI populates the repo-root ``wheels/`` dir with
    KohakuVault's Android wheels; we copy them into the gradle
    project so Chaquopy's pip ``--find-links wheels`` actually
    resolves.

    Without this, Chaquopy logs:
        WARNING: Location 'wheels' is ignored: it is either a
        non-existing path or lacks a specific scheme.
    and downstream pip fails on every Android-only package that
    isn't in Chaquopy's curated index (kohakuvault being the
    first one we hit).

    Idempotent — overwrites existing wheels under the target dir.
    No-op when the source ``wheels/`` is empty or absent (a desktop
    dev rebuild doesn't need them).
    """
    src = REPO_ROOT / "wheels"
    if not src.is_dir() or not any(src.iterdir()):
        print("  wheels  no ./wheels/ to copy (Android-only deps not needed)")
        return 0
    dst = generated / "wheels"
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for whl in src.glob("*.whl"):
        target = dst / whl.name
        shutil.copy2(whl, target)
        copied += 1
        print(f"  wheels  {whl.name} -> {target.relative_to(generated)}")
    if copied == 0:
        print("  wheels  source dir present but contained no .whl files")
    return 0


def patch_launcher_activity(generated: Path) -> int:
    """Rewrite the generated AndroidManifest so the LAUNCHER
    intent points at our ``org.kohaku.terrarium.MainActivity``
    instead of Briefcase's default ``org.beeware.android.MainActivity``.

    Surgical edit — preserves all other manifest content
    (permissions, services, intent filters injected via
    ``android_manifest_*_extra_content``).
    """
    manifest = generated / "src" / "main" / "AndroidManifest.xml"
    if not manifest.is_file():
        print(f"error: manifest missing at {manifest}", file=sys.stderr)
        return 1
    text = manifest.read_text(encoding="utf-8")
    original = text

    # Briefcase template uses ``android:name=".MainActivity"`` with
    # the bundle's package as the parent (so it resolves to
    # ``<bundle>.MainActivity``).  Re-point to ours via fully-qualified
    # class name.
    pattern = re.compile(
        r'(<activity[^>]*android:name=)"([^"]*)\.MainActivity"',
        re.MULTILINE,
    )

    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        return f'{prefix}"{OUR_PACKAGE}.MainActivity"'

    text = pattern.sub(_replace, text, count=1)

    if text == original:
        # Pattern didn't match — Briefcase changed its template
        # shape.  Don't silently no-op; the operator needs to
        # update this script.
        print(
            "error: could not locate launcher Activity declaration "
            "in AndroidManifest.xml.  Has Briefcase Android's "
            "template changed?  Inspect:\n  " + str(manifest),
            file=sys.stderr,
        )
        return 1

    manifest.write_text(text, encoding="utf-8")
    print(f"  manifest  launcher activity -> {OUR_PACKAGE}.MainActivity")
    return 0


def patch_allow_backup(generated: Path) -> int:
    """Flip ``android:allowBackup`` from ``true`` to ``false`` on
    the ``<application>`` tag.

    Briefcase template emits ``allowBackup="true"``; injecting a
    second ``allowBackup`` via ``android_manifest_application_attrs_extra_content``
    would produce a duplicate attribute that AAPT rejects.  This
    surgical replace edits the existing one in place.  Idempotent
    — if it's already ``false`` we leave it alone.

    We force backup off because agent sessions can contain auth
    tokens / OAuth refresh tokens / API keys; Google Drive
    backup of that material is a confidentiality footgun.
    """
    manifest = generated / "src" / "main" / "AndroidManifest.xml"
    if not manifest.is_file():
        print(f"error: manifest missing at {manifest}", file=sys.stderr)
        return 1
    text = manifest.read_text(encoding="utf-8")
    if 'android:allowBackup="false"' in text:
        print("  manifest  allowBackup already false")
        return 0
    new = re.sub(
        r'android:allowBackup="true"',
        'android:allowBackup="false"',
        text,
        count=1,
    )
    if new == text:
        # No allowBackup attribute at all — inject one onto the
        # <application> open tag.
        new = re.sub(
            r"(<application\b)",
            r'\1 android:allowBackup="false"',
            text,
            count=1,
        )
    if new == text:
        print(
            "error: could not locate <application> in AndroidManifest.xml "
            "to set allowBackup",
            file=sys.stderr,
        )
        return 1
    manifest.write_text(new, encoding="utf-8")
    print("  manifest  allowBackup -> false")
    return 0


def remove_default_activity(generated: Path) -> int:
    """Delete Briefcase's default
    ``org/beeware/android/MainActivity.java`` so it doesn't get
    compiled into the APK alongside ours.  Idempotent — if the
    file is already gone (custom template, future Briefcase
    refactor), this is a no-op.
    """
    candidates = [
        generated
        / "src"
        / "main"
        / "java"
        / "org"
        / "beeware"
        / "android"
        / "MainActivity.java",
    ]
    removed = 0
    for path in candidates:
        if path.is_file():
            path.unlink()
            removed += 1
            print(f"  removed default {path.relative_to(generated)}")
    if removed == 0:
        print("  (no default Briefcase MainActivity to remove)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
