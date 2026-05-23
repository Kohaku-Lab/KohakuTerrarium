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
    rc |= patch_android_requirements(gen)
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


# Packages to entirely DROP from requirements.txt on Android.
# Each entry is the canonical package name (the prefix Briefcase
# emits as ``<name>(<spec>)?`` in the generated requirements.txt).
# These are "in core deps for general install, stripped on
# Android" — the project keeps a desktop-friendly hard-dep list
# while postcreate.py carves out the Android-incompatible ones.
_ANDROID_DROP_PACKAGES: tuple[str, ...] = (
    # PDF reading.  Native C bindings to MuPDF; no Chaquopy wheel.
    # ``read_pdf`` tool consumers already lazy-import → absence is
    # a graceful tool-unavailable error, not a boot crash.
    "pymupdf",
    # gitpython itself is pure-Python and installs fine, but it
    # shells out to the system ``git`` binary which Android
    # doesn't ship.  Currently unused in our source; keeping out
    # of Android prevents future regressions where someone adds
    # an importer expecting git to work.
    "gitpython",
    # bcrypt 4.x migrated from C/cffi to Rust/PyO3 (Oct 2022); the
    # Chaquopy curated index tops out at 3.2.2 and we pin the
    # framework at ``>=4.0.0`` in pyproject.  Android doesn't run
    # the L4 multi-user auth surface (a phone is single-tenant),
    # so dropping the dep + lazy-importing in api/auth/crypto.py
    # is the chosen carve-out instead of building bcrypt as a
    # fourth maturin wheel.  Importing :mod:`kohakuterrarium.api.auth`
    # without bcrypt is fine — only ``hash_password`` /
    # ``verify_password`` raise ``RuntimeError`` if invoked, and
    # the L4 routes only get invoked when ``auth_config.users``
    # is enabled.
    "bcrypt",
    # pywebview is the desktop launcher's pywebview shell.  It
    # has no Android wheel (Android uses a native WebView via
    # MainActivity.java + a WebView widget).  Briefcase
    # concatenates ``[tool.briefcase.app.kohakuterrarium].requires``
    # (which contains ``pywebview==6.1`` for the desktop launcher
    # venv) into every platform's requires — see Briefcase docs:
    # "the final set of requirements will be the concatenation of
    # requirements from all levels, starting from least to most
    # specific."  That leaks pywebview into the Android Chaquopy
    # install where it has no wheel.  Stripping here is the
    # simplest fix (vs. restructuring the parent ``requires`` to
    # be per-desktop-platform).
    "pywebview",
)

# Packages where Android needs the BASE package but NOT the
# specified extras.  Briefcase emits ``uvicorn[standard]>=X`` from
# project deps; the [standard] extra pulls uvloop + httptools +
# watchfiles, none of which have Chaquopy wheels.  Stripping the
# extras leaves bare uvicorn which falls back to stdlib asyncio +
# h11 (pure-Python, ships with uvicorn itself).
_ANDROID_STRIP_EXTRAS: dict[str, str] = {
    "uvicorn": "uvicorn",  # drop any ``[...]`` suffix on the uvicorn line
}

# Packages that need to be installed from a direct URL on Android
# (no Chaquopy wheel + native-code build = pip can't resolve via
# the curated index).  Each entry is:
#
#   pkg name → (
#       wheel-filename-prefix on the release server,
#       template-fn(version) → (arm64_url, x86_64_url),
#       version-extractor reading pyproject.toml's [project] deps
#   )
#
# The version-extractor reads our pyproject so bumping the floor
# in one place drives both the dep spec AND the URL ref version.
# Adding a new URL-ref package = one entry here + (if applicable)
# the upstream wheel needs to exist at the URL the template
# produces.
_ANDROID_URL_REFS: dict[str, dict[str, object]] = {
    "kohakuvault": {
        # Wheel name in the release uses underscores (PEP 491 wheel
        # filename normalisation) even though the dep name is the
        # hyphenated form on PyPI.  ``kohakuvault`` happens to be
        # one word so no transform is needed; the dict key is the
        # exact name Briefcase emits in requirements.txt.
        "wheel_basename": "kohakuvault",
        "release_base": (
            "https://github.com/KohakuBlueleaf/KohakuVault/releases/download/"
            "v{version}"
        ),
        "filename": ("kohakuvault-{version}-cp313-cp313-android_24_{abi_tag}.whl"),
    },
    "pydantic-core": {
        # PyPI dep name: ``pydantic-core``.  Wheel filename uses
        # the underscore form ``pydantic_core``.  Briefcase passes
        # the hyphenated form through into requirements.txt, so we
        # match on the hyphenated key but emit the underscored
        # filename.
        "wheel_basename": "pydantic_core",
        "release_base": (
            "https://github.com/Kohaku-Lab/android-dep-collection/releases/download/"
            "v2026.05.23"
        ),
        # pydantic-core's version on Android tracks our
        # android-dep-collection manifest (currently 2.41.1).
        # When pydantic bumps in pyproject.toml, the manifest +
        # release tag on the collection repo also need to bump.
        # See dep/android-dep-collection/manifest.toml.
        "filename": ("pydantic_core-{version}-cp313-cp313-android_24_{abi_tag}.whl"),
        "pinned_version": "2.41.1",
    },
    "safetensors": {
        # safetensors is a transitive dep via model2vec (embeddings).
        # Rust/PyO3, no Chaquopy wheel.  Upstream Cargo enables
        # ``pyo3/abi3-py38`` so the cross-built wheel actually emits
        # the ABI3 tag ``cp38-abi3`` (one wheel ≥ Python 3.8 per
        # ABI), not ``cp313-cp313``.  Verified against the actual
        # build artifacts attached to release v2026.05.24.
        "wheel_basename": "safetensors",
        "release_base": (
            "https://github.com/Kohaku-Lab/android-dep-collection/releases/download/"
            "v2026.05.24"
        ),
        "filename": ("safetensors-{version}-cp38-abi3-android_24_{abi_tag}.whl"),
        "pinned_version": "0.7.0",
    },
    "tokenizers": {
        # tokenizers is a transitive dep via model2vec.  Rust/PyO3.
        # Upstream Cargo declares ``pyo3/abi3-py310`` so the wheel
        # tag is ``cp310-abi3`` (one wheel ≥ Python 3.10 per ABI).
        # Verified against the v2026.05.24 release artifacts.
        "wheel_basename": "tokenizers",
        "release_base": (
            "https://github.com/Kohaku-Lab/android-dep-collection/releases/download/"
            "v2026.05.24"
        ),
        "filename": ("tokenizers-{version}-cp310-abi3-android_24_{abi_tag}.whl"),
        "pinned_version": "0.23.1",
    },
    "primp": {
        # primp is a transitive dep via ddgs (DuckDuckGo search
        # tool).  Wraps reqwest with rustls-tls (no system OpenSSL).
        # Upstream's manylinux releases on PyPI carry ``cp38-abi3``,
        # but the v1.3.0 source-tree Cargo features pin
        # ``pyo3/abi3-py310`` — so our cross-built Android wheel
        # ships as ``cp310-abi3``.  Verified against the v2026.05.24
        # release artifacts.
        "wheel_basename": "primp",
        "release_base": (
            "https://github.com/Kohaku-Lab/android-dep-collection/releases/download/"
            "v2026.05.24"
        ),
        "filename": ("primp-{version}-cp310-abi3-android_24_{abi_tag}.whl"),
        "pinned_version": "1.3.0",
    },
}

# Wheel-tag suffix per Android ABI (Chaquopy's wheels use these
# exact strings — e.g. ``android_24_arm64_v8a`` for arm64-v8a).
_ABI_WHEEL_TAGS: dict[str, str] = {
    "aarch64": "arm64_v8a",
    "x86_64": "x86_64",
}


def patch_android_requirements(generated: Path) -> int:
    """Rewrite ``app/requirements.txt`` to pin Android-only packages
    to direct URL refs of their GitHub-Releases wheels.

    The parent Briefcase config sets
    ``requirement_installer_args = ["--find-links", "wheels"]`` —
    that works on desktop, where pip is invoked from the project
    root, but on Android Chaquopy invokes pip from
    ``<module>/build/python/env/<variant>/`` (a path that doesn't
    exist at postcreate time, so we can't pre-populate it).  The
    result is the warning every CI run shows:

        WARNING: Location 'wheels' is ignored: it is either a
        non-existing path or lacks a specific scheme.

    Direct URL refs sidestep the path-resolution entirely — pip
    downloads the wheel from the GH release server using only the
    URL.  PEP 508 environment markers select the right ABI per
    device:

        kohakuvault @ <arm64-url> ; platform_machine == 'aarch64'
        kohakuvault @ <x86_64-url> ; platform_machine == 'x86_64'

    Chaquopy reports ``platform_machine`` correctly per ABI
    (``aarch64`` for arm64-v8a, ``x86_64`` for x86_64); the matching
    line wins, the other is skipped.

    KohakuVault ships only arm64 + x86_64 Android wheels (the two
    ABIs PyO3/maturin-action's cross-prefix covers); armv7 / x86
    devices fall through with no matching ref + pip errors with a
    clear message.  That's intentional — armv7 isn't in our target
    matrix.

    Idempotent — running twice produces the same file.
    """
    req_path = generated / "requirements.txt"
    if not req_path.is_file():
        print(f"warning: requirements.txt missing at {req_path}", file=sys.stderr)
        return 0  # not fatal — older Briefcase versions might place it elsewhere

    # Pre-resolve each URL-ref package's version + URL templates.
    # If a package's version can't be inferred we skip its
    # replacement (still applies drop/strip carve-outs).
    url_ref_plan = _build_url_ref_plan()

    lines = req_path.read_text(encoding="utf-8").splitlines()
    patched: list[str] = []
    replaced_pkgs: set[str] = set()
    seen_url_pkgs: set[str] = set()

    for line in lines:
        stripped = line.strip()

        # Skip lines already in any URL-ref form so re-runs are
        # idempotent.  Without this guard, the first run replaces
        # the spec line with N URL lines; the second run would see
        # those URL lines (which start with the same package
        # name) and replace each AGAIN, multiplying lines.  The
        # ``@ <known-release-base>`` substring is the signature.
        already_url_form = False
        for pkg, plan in url_ref_plan.items():
            if f" @ {plan['release_base']}" in stripped:
                seen_url_pkgs.add(pkg)
                already_url_form = True
                break
        if already_url_form:
            patched.append(line)
            continue

        # Android carve-outs: drop entire lines for packages with
        # no Android wheel (pymupdf, gitpython), strip ``[extras]``
        # from packages where only the bare form has Android
        # support (uvicorn[standard] → uvicorn).
        pkg_name = _extract_package_name(stripped)
        if pkg_name is not None:
            pkg_lower = pkg_name.lower()
            if pkg_lower in _ANDROID_DROP_PACKAGES:
                # Silently drop the line — Android doesn't get this
                # dep at all.  The consumer code is expected to
                # lazy-import + degrade gracefully (see pymupdf
                # consumers; gitpython is unused in our source).
                continue
            if pkg_lower in _ANDROID_STRIP_EXTRAS:
                patched.append(_strip_extras(stripped, pkg_lower))
                continue
            # URL-ref replacement.  ``pkg_name`` here is the raw
            # form Briefcase emitted (e.g. ``pydantic-core``);
            # match against the table case-insensitively.
            url_plan = url_ref_plan.get(pkg_lower)
            if url_plan is not None and _is_dep_spec_for(stripped, pkg_name):
                patched.append(
                    f"{pkg_lower} @ {url_plan['arm64_url']}"
                    " ; platform_machine == 'aarch64'"
                )
                patched.append(
                    f"{pkg_lower} @ {url_plan['x86_64_url']}"
                    " ; platform_machine == 'x86_64'"
                )
                replaced_pkgs.add(pkg_lower)
                continue

        patched.append(line)

    # Warn for any URL-ref package we expected to replace but
    # didn't see (Briefcase format drift / dep was removed).
    missing = set(url_ref_plan) - replaced_pkgs - seen_url_pkgs
    for pkg in missing:
        print(
            f"warning: no {pkg} line found in requirements.txt; "
            "Android install may fail with No matching distribution",
            file=sys.stderr,
        )

    req_path.write_text("\n".join(patched) + "\n", encoding="utf-8")
    for pkg in sorted(replaced_pkgs):
        print(f"  requirements  {pkg} -> direct URL refs (aarch64 + x86_64)")
    return 0


def _is_dep_spec_for(stripped: str, pkg_name: str) -> bool:
    """True iff ``stripped`` is a dep-spec line for ``pkg_name``
    (vs a line that just happens to start with the name as a
    prefix of something else).  Form check: name must be followed
    by end-of-line, ``[``, an operator, ``@``, or ``!`` / ``;``.
    """
    if stripped == pkg_name:
        return True
    if len(stripped) <= len(pkg_name):
        return False
    next_char = stripped[len(pkg_name)]
    return next_char in "=<>~![@; "


def _build_url_ref_plan() -> dict[str, dict[str, str]]:
    """Resolve concrete arm64 + x86_64 URLs for each URL-ref
    package in ``_ANDROID_URL_REFS``.

    For kohakuvault, the version is read from pyproject.toml
    (matches the dep floor).  For pydantic-core, the version is
    pinned in the table (it tracks the
    ``dep/android-dep-collection`` manifest, not our pyproject).
    """
    plan: dict[str, dict[str, str]] = {}
    for pkg_name, entry in _ANDROID_URL_REFS.items():
        if pkg_name == "kohakuvault":
            version = _kohakuvault_version_from_pyproject()
        else:
            version = str(entry.get("pinned_version") or "")
        if not version:
            print(
                f"warning: could not infer {pkg_name} version; "
                "skipping URL-ref replacement for it",
                file=sys.stderr,
            )
            continue
        release_base = str(entry["release_base"]).format(version=version)
        filename_arm = str(entry["filename"]).format(
            version=version, abi_tag="arm64_v8a"
        )
        filename_x86 = str(entry["filename"]).format(version=version, abi_tag="x86_64")
        plan[pkg_name] = {
            "release_base": release_base,
            "arm64_url": f"{release_base}/{filename_arm}",
            "x86_64_url": f"{release_base}/{filename_x86}",
        }
    return plan


def _extract_package_name(req_line: str) -> str | None:
    """Pull the canonical package name off the front of a
    requirements.txt line.  Handles:

    - ``uvicorn[standard]>=0.34.0``   → ``uvicorn``
    - ``pymupdf>=1.24.0``             → ``pymupdf``
    - ``pyyaml`` (no spec)            → ``pyyaml``
    - ``# comment``                   → ``None``
    - ``-e ./local`` / ``--option``   → ``None``
    - ``pkg @ http://...``            → ``pkg``

    The matcher is intentionally permissive — we don't fully
    parse PEP 508, just identify the package-name prefix so the
    Android carve-out tables can match against it.
    """
    text = req_line.strip()
    if not text or text.startswith("#") or text.startswith("-"):
        return None
    # Find where the name ends — first char that's not a valid
    # package-name char (PEP 503 normalised names allow [A-Za-z0-9._-]).
    name_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    end = 0
    while end < len(text) and text[end] in name_chars:
        end += 1
    if end == 0:
        return None
    return text[:end]


def _strip_extras(req_line: str, pkg_name: str) -> str:
    """Rewrite ``pkg[extra1,extra2]>=1.0`` → ``pkg>=1.0``.

    Used for Android where the bare package has a wheel but the
    extras pull in native deps that don't (e.g. uvicorn vs
    uvicorn[standard]).  Preserves whitespace + the version
    spec + any trailing PEP 508 marker.
    """
    text = req_line
    # The ``[...]`` is always right after the package name (no
    # whitespace in the canonical form Briefcase emits).
    bracket_start = text.find("[", len(pkg_name) - 1)
    if bracket_start == -1:
        return text  # nothing to strip
    bracket_end = text.find("]", bracket_start)
    if bracket_end == -1:
        return text  # malformed — leave alone
    return text[:bracket_start] + text[bracket_end + 1 :]


def _kohakuvault_version_from_pyproject() -> str | None:
    """Read the kohakuvault dep spec from ``pyproject.toml`` and
    extract a concrete version string for the URL build.

    The spec is ``kohakuvault>=X.Y.Z`` — we use ``X.Y.Z`` as the
    version-to-fetch.  Avoids pinning the URL version in two
    places (pyproject + postcreate); operator bumps the floor and
    this script follows.
    """
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return None
    text = pyproject.read_text(encoding="utf-8")
    # Match the line: ``"kohakuvault>=0.8.3"``  (possibly preceded
    # by indentation + followed by a comma).  Regex avoids needing
    # a full TOML parser since we only want one value.
    m = re.search(
        r'"kohakuvault>=([0-9]+\.[0-9]+\.[0-9]+)"',
        text,
    )
    return m.group(1) if m else None


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
