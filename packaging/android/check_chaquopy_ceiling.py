"""Audit our full transitive dep tree against Chaquopy's version ceilings.

This catches the family of Android-build failures we've been hit by
repeatedly:

    ERROR: Could not find a version that satisfies the requirement
    <pkg>>=<X> (from versions: <Y>)

…where ``Y`` is Chaquopy's max version and ``X`` is a floor demanded
by some transitive package (often deep in the graph — kohakuvault
demanding ``numpy>=2.0`` when Chaquopy ships ``numpy 1.26.2`` was the
case that finally motivated this tool).

How it works
------------

1. Calls ``pip install --dry-run --ignore-installed --report`` against
   our pyproject to produce the full resolved transitive tree (~110
   packages).
2. For each Chaquopy-bound native package in our :data:`CHAQUOPY_MAX`
   table, walks every resolved package's ``requires_dist`` and finds
   the highest version floor demanded.
3. Evaluates PEP 508 markers against an Android-Chaquopy environment
   (cp313, Linux, aarch64) so that ``extra == 'X'`` and
   ``python_version < '3.13'`` markers are correctly accepted/rejected.
4. Skips packages we URL-ref ourselves (see :data:`URL_REF_PACKAGES`)
   and packages we strip on Android (see :data:`DROPPED_PACKAGES`).
5. Reports any case where ``floor > ceiling`` as a BLOCKER.

Invoke with ``python packaging/android/check_chaquopy_ceiling.py``.
Exit code: 0 = all clear; 1 = at least one blocker; 2 = setup error
(pip unavailable, can't read pyproject, etc).

The hardcoded :data:`CHAQUOPY_MAX` table reflects the cp313 wheels
published at ``https://chaquo.com/pypi-13.1/<pkg>/``.  When Chaquopy
ships new wheels (or when we bump to a new Chaquopy major), update
the table.

This module is deliberately import-safe: ``from check_chaquopy_ceiling
import analyse`` does no network or subprocess work.  Only ``main()``
shells out to pip.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version

# Every package the Chaquopy 13.1 curated index publishes wheels for.
# Source: https://chaquo.com/pypi-13.1/ (verified 2026-05-23).
# Used by ``find_missing_wheels`` to recognise packages Chaquopy
# already serves — even without a known version ceiling, presence
# in the index means pip can resolve them on Android.
CHAQUOPY_INDEX: frozenset[str] = frozenset(
    {
        "aiohttp",
        "argon2-cffi",
        "argon2-cffi-bindings",
        "astropy",
        "aubio",
        "backports-zoneinfo",
        "bcrypt",
        "bitarray",
        "blis",
        "brotli",
        "cffi",
        "chaquopy-crc32c",
        "chaquopy-curl",
        "chaquopy-curl-openssl-3",
        "chaquopy-flac",
        "chaquopy-freetype",
        "chaquopy-geos",
        "chaquopy-hdf5",
        "chaquopy-lame",
        "chaquopy-libcxx",
        "chaquopy-libffi",
        "chaquopy-libgfortran",
        "chaquopy-libiconv",
        "chaquopy-libjpeg",
        "chaquopy-libogg",
        "chaquopy-libomp",
        "chaquopy-libpng",
        "chaquopy-libraw",
        "chaquopy-libsndfile",
        "chaquopy-libtiff",
        "chaquopy-libvorbis",
        "chaquopy-libxml2",
        "chaquopy-libxslt",
        "chaquopy-libyaml",
        "chaquopy-libzmq",
        "chaquopy-llvm",
        "chaquopy-openblas",
        "chaquopy-proj",
        "chaquopy-proj-openssl-3",
        "chaquopy-secp256k1",
        "chaquopy-ta-lib",
        "chaquopy-zbar",
        "coincurve",
        "contourpy",
        "cryptography",
        "cvxopt",
        "cymem",
        "cytoolz",
        "depthai",
        "dlib",
        "editdistance",
        "ephem",
        "frozenlist",
        "gensim",
        "gevent",
        "google-crc32c",
        "greenlet",
        "grpcio",
        "h5py",
        "igraph",
        "jpegio",
        "kiwisolver",
        "lameenc",
        "llvmlite",
        "lru-dict",
        "lxml",
        "lz4",
        "marisa-trie",
        "markupsafe",
        "matplotlib",
        "miniaudio",
        "multidict",
        "murmurhash",
        "netifaces",
        "numba",
        "numpy",
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
        "opencv-python",
        "opencv-python-headless",
        "pandas",
        "photutils",
        "pillow",
        "preshed",
        "psutil",
        "pycares",
        "pycocotools",
        "pycrypto",
        "pycryptodome",
        "pycryptodomex",
        "pycurl",
        "pyerfa",
        "pynacl",
        "pyproj",
        "pysha3",
        "pywavelets",
        "pyyaml",
        "pyzbar",
        "pyzmq",
        "qutip",
        "rawpy",
        "regex",
        "ruamel-yaml-clib",
        "scandir",
        "scikit-image",
        "scikit-learn",
        "scipy",
        "sentencepiece",
        "shapely",
        "soundfile",
        "soxr",
        "spacy",
        "spectrum",
        "srsly",
        "statsmodels",
        "ta-lib",
        "tensorflow",
        "tensorflow-gpu",
        "tflite-runtime",
        "tgcrypto",
        "thinc",
        "tokenizers",
        "torch",
        "torchvision",
        "tornado",
        "twisted",
        "typed-ast",
        "ujson",
        "wordcloud",
        "xgboost",
        "yarl",
        "zope-interface",
        "zstandard",
    }
)

# PyPI metadata cache for the pure-Python wheel detection.  File-
# backed so re-runs are fast.  Set via ``--cache <path>`` on the CLI.
_PYPI_CACHE_PATH: Path | None = None


# Maximum cp313 Android wheel version Chaquopy 13.1 publishes per
# native dep.  Verified against https://chaquo.com/pypi-13.1/<pkg>/
# on 2026-05-23.  When Chaquopy bumps versions, update here.
CHAQUOPY_MAX: dict[str, str] = {
    "pillow": "11.0.0",
    "pyyaml": "6.0.3",
    "numpy": "1.26.2",
    "lxml": "5.3.0",
    "cryptography": "42.0.8",
    "markupsafe": "3.0.3",
    "brotli": "1.1.0",
    "zstandard": "0.23.0",
    "ruamel.yaml.clib": "0.2.12",
    "bcrypt": "3.2.2",
}

# Packages we host our own Android wheels for (via
# dep/android-dep-collection).  Their dep floors don't apply because
# we control the wheel.  See packaging/android/postcreate.py's
# _ANDROID_URL_REFS for the URL-ref consumer side.
URL_REF_PACKAGES: frozenset[str] = frozenset(
    {
        "kohakuvault",
        "pydantic-core",
        "safetensors",
        "tokenizers",
        "primp",
        # jiter: Rust JSON streaming parser.  Required UNCONDITIONALLY
        # by both openai>=2.0 and anthropic>=0.68 — no escape via pin
        # because every recent version of either package demands it.
        # Built via dep/android-dep-collection.
        "jiter",
        # rpds-py: Rust persistent-data-structures.  Hard transitive
        # via jsonschema -> referencing -> rpds-py.  No pure-Python
        # fallback.  Built via dep/android-dep-collection.
        "rpds-py",
    }
)

# Packages we drop entirely from Android requirements.  See
# _ANDROID_DROP_PACKAGES in postcreate.py.  A dropped package's
# own metadata demands don't apply because the package never gets
# installed.
DROPPED_PACKAGES: frozenset[str] = frozenset(
    {
        "pymupdf",
        "gitpython",
        "bcrypt",
        "pywebview",
        "lxml_html_clean",
        "lxml-html-clean",
        "uvloop",
        "httptools",
        "watchfiles",
        # hf-xet: HuggingFace LFS transfer accelerator (Rust+PyO3).
        # huggingface_hub pulls it via a ``platform_machine ==
        # 'aarch64' ...`` marker which is ACTIVE on Android.  hf-xet
        # has no Android wheel and is genuinely OPTIONAL — at
        # runtime, huggingface_hub falls back to its standard HTTP
        # download path when hf-xet isn't importable.  Postcreate
        # strips the line so pip doesn't try to install.  Two name
        # forms because Briefcase / pip have been observed to emit
        # both depending on which resolver pass produced the line.
        "hf-xet",
        "hf_xet",
    }
)

# When a package is declared with extras like ``httpx[brotli,http2]``
# its own metadata says ``Brotli; extra == 'brotli'`` — that
# extras-gated demand becomes active.  This table records WHICH
# extras get activated for each package in our Android build, so the
# marker evaluator can flip them on.
ANDROID_ACTIVE_EXTRAS: dict[str, frozenset[str]] = {
    # ddgs declares httpx[brotli,http2,socks] so all three extras
    # are active when evaluating httpx's METADATA.
    "httpx": frozenset({"brotli", "http2", "socks"}),
    # mcp declares pyjwt[crypto] so the ``crypto`` extra is active.
    "pyjwt": frozenset({"crypto"}),
    # uvicorn's [standard] extra is stripped by postcreate on
    # Android, so we treat it as NOT active here.
    "uvicorn": frozenset(),
}


@dataclass(frozen=True)
class Blocker:
    """One ``floor > ceiling`` violation."""

    dep: str  # the Chaquopy-bound native dep (e.g. "numpy")
    ceiling: Version  # Chaquopy's max published version
    floor: Version  # highest version any active demander requires
    demander: str  # the package whose metadata declared the floor
    spec: str  # the raw requirement string for diagnostic context


@dataclass(frozen=True)
class MissingWheel:
    """A resolved package has NO Android-installable source.

    Detected when a package is in the install tree but is:
        * NOT in Chaquopy's curated index (no prebuilt Android wheel)
        * NOT URL-ref'd by us (no own wheel hosted)
        * NOT dropped by us (we don't strip it from requirements)
        * AND has no pure-Python wheel on PyPI (must build from C/Rust)

    This is the family of failure that bit us with ``jiter``: a Rust
    extension pulled in transitively by openai, never previously on
    our radar.
    """

    package: str
    version: str
    pulled_by: str  # which package's requires_dist pulled this in


def _load_pypi_cache() -> dict[str, bool]:
    if _PYPI_CACHE_PATH is None or not _PYPI_CACHE_PATH.is_file():
        return {}
    try:
        with open(_PYPI_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_pypi_cache(cache: dict[str, bool]) -> None:
    if _PYPI_CACHE_PATH is None:
        return
    try:
        with open(_PYPI_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except OSError:
        pass


def has_pure_python_wheel(package: str, version: str) -> bool:
    """True iff PyPI publishes a pure-Python wheel for ``package==version``.

    A pure-Python wheel has the platform tag ``py3-none-any`` or
    ``py2.py3-none-any`` — Chaquopy pip can install these on Android
    without any native build step.
    """
    cache = _load_pypi_cache()
    cache_key = f"{package}=={version}"
    if cache_key in cache:
        return cache[cache_key]
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        # Conservative: if PyPI is unreachable, treat as NOT pure-Python
        # so we err on the side of flagging.  Caller can re-run.
        result = False
        cache[cache_key] = result
        _save_pypi_cache(cache)
        return result
    has_pure = False
    for entry in data.get("urls", []):
        fn = entry.get("filename", "")
        if fn.endswith(".whl") and (
            "-py3-none-any.whl" in fn or "-py2.py3-none-any.whl" in fn
        ):
            has_pure = True
            break
    cache[cache_key] = has_pure
    _save_pypi_cache(cache)
    return has_pure


def has_any_pure_python_version(package: str) -> bool:
    """True iff PyPI has at least ONE release of ``package`` with a
    pure-Python wheel.

    This is the second check needed beyond ``has_pure_python_wheel``:
    pip on Chaquopy will fall back from binary-only newer versions to
    an older pure-Python version if the dep range allows it.  Example:
    ``libcst`` 1.x is Rust+PyO3 (binary), but 0.3.23 was pure-Python
    (``libcst-0.3.23-py3-none-any.whl``).  Our pin ``libcst<2`` lets
    Chaquopy pip walk back to 0.3.23.

    We don't try to be precise about the demand range — if ANY version
    is pure-Python, we assume the consumer's pin can be relaxed (or
    already allows it).  Audit users who hit a false-negative can add
    the package to ``SOFT_CARVE_OUT``.
    """
    cache = _load_pypi_cache()
    cache_key = f"{package}::any-pure"
    if cache_key in cache:
        return cache[cache_key]
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        result = False
        cache[cache_key] = result
        _save_pypi_cache(cache)
        return result
    has_pure = False
    for _ver, entries in (data.get("releases") or {}).items():
        for entry in entries:
            fn = entry.get("filename", "")
            if fn.endswith(".whl") and (
                "-py3-none-any.whl" in fn or "-py2.py3-none-any.whl" in fn
            ):
                has_pure = True
                break
        if has_pure:
            break
    cache[cache_key] = has_pure
    _save_pypi_cache(cache)
    return has_pure


def _android_reachable(
    install_report: dict,
    env: dict[str, str],
    active_extras: dict[str, frozenset[str]],
) -> set[str]:
    """Return the set of normalised package names reachable from the
    project root under Android markers.

    Pip's install report contains the host-resolved tree, which on
    Windows includes ``pywin32`` (``sys_platform == 'win32'``) and on
    cp314 free-threaded includes ``PyYAML-ft`` (``python_version >=
    '3.14'``).  These are FALSE POSITIVES for an Android audit: they'd
    never be in the Chaquopy install set.  This BFS walks the demand
    graph from the project root, accepting an edge only if its
    PEP 508 marker evaluates true on Android (cp313 Linux aarch64).
    """
    by_name = {
        _normalize_name(p["metadata"]["name"]): p
        for p in install_report.get("install", [])
    }
    # Roots: packages marked ``is_direct`` AND with no PyPI-style
    # URL (i.e. our editable project install).  Also accept any
    # explicit ``requested`` entry which pip sets for top-level deps.
    roots: set[str] = set()
    for p in install_report.get("install", []):
        if p.get("is_direct") or p.get("requested"):
            roots.add(_normalize_name(p["metadata"]["name"]))
    if not roots:
        # Fallback: walk everything (audit still runs, just no
        # marker filtering benefit).
        return set(by_name)

    reachable: set[str] = set(roots)
    frontier = list(roots)
    soft_carve_norm = {_normalize_name(n) for n in SOFT_CARVE_OUT}
    while frontier:
        cur = frontier.pop()
        # SOFT_CARVE_OUT packages resolve to a DIFFERENT version on
        # Android (the fallback pure-Python version) which has
        # different transitives.  Don't walk into their current-
        # version requires_dist — that would propagate Android-
        # irrelevant transitives (e.g. libcst 1.8.6 demands
        # ``pyyaml-ft>=8.0.0; python_version == "3.13"`` but 0.3.23
        # only needs PyYAML and typing-extensions).
        if cur in soft_carve_norm:
            continue
        pkg = by_name.get(cur)
        if pkg is None:
            continue
        for req_str in pkg["metadata"].get("requires_dist") or []:
            try:
                req = Requirement(req_str)
            except Exception:
                continue
            if not _demand_active(
                req,
                demander_name=pkg["metadata"]["name"],
                env=env,
                active_extras=active_extras,
            ):
                continue
            child = _normalize_name(req.name)
            if child not in reachable:
                reachable.add(child)
                frontier.append(child)
    return reachable


# Packages we've manually verified install OK on Chaquopy via a
# pure-Python fallback version even though their LATEST version on
# PyPI is binary-only.  These are skipped by the missing-wheel check.
SOFT_CARVE_OUT: frozenset[str] = frozenset(
    {
        # libcst: pin ``libcst<2`` in pyproject lets pip fall back to
        # 0.3.23 which ships ``libcst-0.3.23-py3-none-any.whl`` on
        # PyPI.  1.x is Rust+PyO3 (binary) and not in Chaquopy.
        "libcst",
    }
)


def find_missing_wheels(install_report: dict) -> list[MissingWheel]:
    """For every Android-reachable resolved package, check it has SOME
    way to install on Android.  Returns the HARD blockers — packages
    pip will fail to resolve with ``no matching distributions``.

    Filtering logic (each True short-circuits to "fine"):
      1. dropped via ``_ANDROID_DROP_PACKAGES`` (postcreate strips it)
      2. URL-ref'd by us (own wheel in android-dep-collection)
      3. in Chaquopy's curated index (any version)
      4. in ``SOFT_CARVE_OUT`` (manually verified fallback)
      5. has a pure-Python wheel at this version OR any version on PyPI
         (pip on Chaquopy walks down to that version)

    Anything that survives all five is reported.

    The set we audit over is ANDROID-REACHABLE only: packages pulled
    by Windows-only markers (``sys_platform == 'win32'``) or
    free-threaded-only markers (``python_version >= '3.14'``) are
    excluded by walking the demand graph from the project root with
    the Android environment.
    """
    dropped_norm = {_normalize_name(n) for n in DROPPED_PACKAGES}
    url_ref_norm = {_normalize_name(n) for n in URL_REF_PACKAGES}
    chaquopy_norm = {_normalize_name(n) for n in CHAQUOPY_INDEX}
    soft_carve_norm = {_normalize_name(n) for n in SOFT_CARVE_OUT}

    env = _android_marker_env()
    android_reachable = _android_reachable(install_report, env, ANDROID_ACTIVE_EXTRAS)

    missing: list[MissingWheel] = []
    # Build a reverse-demand map so we can name "who pulled this in?"
    pulled_by: dict[str, str] = {}
    for pkg in install_report.get("install", []):
        meta = pkg["metadata"]
        parent = meta["name"]
        for req_str in meta.get("requires_dist") or []:
            try:
                req = Requirement(req_str)
            except Exception:
                continue
            child = _normalize_name(req.name)
            pulled_by.setdefault(child, parent)

    for pkg in install_report.get("install", []):
        meta = pkg["metadata"]
        name = meta["name"]
        norm = _normalize_name(name)
        if norm not in android_reachable:
            continue
        if (
            norm in dropped_norm
            or norm in url_ref_norm
            or norm in chaquopy_norm
            or norm in soft_carve_norm
        ):
            continue
        # Skip the project itself — pip's report marks editable
        # installs and our top-level project with ``is_direct`` and
        # a file:// download URL.
        is_local = pkg.get("is_direct", False) or pkg.get("download_info", {}).get(
            "url", ""
        ).startswith("file:")
        if is_local:
            continue
        # Pure-Python at current resolved version?  If yes, pip on
        # Chaquopy uses that wheel directly.
        if has_pure_python_wheel(name, meta["version"]):
            continue
        # Pure-Python at ANY version?  If yes, pip on Chaquopy walks
        # back through versions until it finds the pure-Python one.
        # (Slow but functional — see libcst 0.3.23 fallback.)
        if has_any_pure_python_version(name):
            continue
        missing.append(
            MissingWheel(
                package=name,
                version=meta["version"],
                pulled_by=pulled_by.get(norm, "(direct)"),
            )
        )
    missing.sort(key=lambda m: m.package)
    return missing


def _normalize_name(name: str) -> str:
    """PEP 503-ish normalisation: lowercase + dot→hyphen."""
    return name.lower().replace("_", "-").replace(".", "-")


def _android_marker_env() -> dict[str, str]:
    """PEP 508 environment dict for Chaquopy 13.1 (Python 3.13
    on Android, reported as Linux/aarch64).
    """
    env = dict(default_environment())
    env["python_version"] = "3.13"
    env["python_full_version"] = "3.13.7"
    env["platform_system"] = "Linux"
    env["platform_machine"] = "aarch64"
    env["sys_platform"] = "linux"
    env["implementation_name"] = "cpython"
    env["platform_python_implementation"] = "CPython"
    return env


def _demand_active(
    req: Requirement,
    *,
    demander_name: str,
    env: dict[str, str],
    active_extras: dict[str, frozenset[str]],
) -> bool:
    """True iff ``req`` is an active demand on Android for ``demander_name``.

    Handles two kinds of markers:

    * ``extra == 'X'`` — active only if the demander was pulled with
      extra ``X`` (per :data:`ANDROID_ACTIVE_EXTRAS`).
    * Non-extras markers (``python_version``, ``platform_machine``,
      etc.) — evaluated against the Android env.
    """
    if req.marker is None:
        return True
    extras_for_demander = active_extras.get(demander_name.lower(), frozenset())
    # We test each possible extra value (including the empty string
    # which represents "demander pulled without extras") and the
    # union of active extras.  A demand is active if ANY of these
    # marker evaluations returns True.
    candidates = [""] + list(extras_for_demander)
    for extra_val in candidates:
        env_with = dict(env)
        env_with["extra"] = extra_val
        try:
            if req.marker.evaluate(env_with):
                return True
        except Exception:  # pragma: no cover  (defensive)
            return True
    return False


def analyse(install_report: dict) -> list[Blocker]:
    """Run the ceiling check against a ``pip install --dry-run --report``
    JSON document.  Returns a list of :class:`Blocker` instances —
    empty list = all clear.
    """
    pkgs = install_report.get("install", [])
    env = _android_marker_env()
    chaquopy_norm = {
        _normalize_name(k): (k, Version(v)) for k, v in CHAQUOPY_MAX.items()
    }
    dropped_norm = {_normalize_name(n) for n in DROPPED_PACKAGES}

    # Map normalised target dep → (target display name, ceiling Version).
    # For each, collect the highest active floor + its source.
    worst: dict[str, tuple[Version, str, str]] = {}

    for pkg in pkgs:
        meta = pkg["metadata"]
        demander = meta["name"]
        demander_norm = _normalize_name(demander)
        # A dropped package isn't installed on Android, so its
        # outgoing demands don't count.
        if demander_norm in dropped_norm:
            continue
        # URL-ref'd packages ARE installed — but as our own Android
        # wheel.  Their metadata demands still apply, so don't skip.
        for req_str in meta.get("requires_dist") or []:
            try:
                req = Requirement(req_str)
            except Exception:
                continue
            target_norm = _normalize_name(req.name)
            if target_norm not in chaquopy_norm:
                continue
            # If the TARGET dep itself is dropped on Android (e.g.
            # bcrypt — we strip it from requirements.txt and
            # lazy-import on the consumer side), pip never tries to
            # install it, so an out-of-range floor against it is
            # not a real blocker.  This complements the demander-
            # side skip above: a drop neutralises both directions.
            if target_norm in dropped_norm:
                continue
            if not _demand_active(
                req,
                demander_name=demander,
                env=env,
                active_extras=ANDROID_ACTIVE_EXTRAS,
            ):
                continue
            # Find the floor in the specifier set.
            for spec in req.specifier:
                if spec.operator not in (">=", ">", "=="):
                    continue
                try:
                    v = Version(spec.version)
                except Exception:
                    continue
                cur = worst.get(target_norm)
                if cur is None or v > cur[0]:
                    worst[target_norm] = (v, demander, req_str)

    blockers: list[Blocker] = []
    for target_norm, (floor, demander, spec) in worst.items():
        display_name, ceiling = chaquopy_norm[target_norm]
        if floor > ceiling:
            blockers.append(
                Blocker(
                    dep=display_name,
                    ceiling=ceiling,
                    floor=floor,
                    demander=demander,
                    spec=spec,
                )
            )
    blockers.sort(key=lambda b: b.dep)
    return blockers


def _resolve_install_report(project_root: Path) -> dict:
    """Run ``pip install --dry-run --report`` and return parsed JSON."""
    report_path = project_root / ".chaquopy_audit_report.json"
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--ignore-installed",
                "--quiet",
                "--report",
                str(report_path),
                str(project_root),
            ],
            check=True,
        )
        with open(report_path, encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            report_path.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root with pyproject.toml (defaults to repo root)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Pre-built pip report JSON to analyse instead of resolving fresh",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help=(
            "Path to a JSON cache of PyPI pure-Python-wheel lookups "
            "(speeds up re-runs).  Default: no cache."
        ),
    )
    args = parser.parse_args(argv)

    global _PYPI_CACHE_PATH
    _PYPI_CACHE_PATH = args.cache

    if args.report:
        with open(args.report, encoding="utf-8") as f:
            report = json.load(f)
    else:
        try:
            report = _resolve_install_report(args.project)
        except subprocess.CalledProcessError as exc:
            print(f"error: pip dry-run failed: {exc}", file=sys.stderr)
            return 2

    total = len(report.get("install", []))
    ceiling_blockers = analyse(report)
    missing_wheels = find_missing_wheels(report)

    if not ceiling_blockers and not missing_wheels:
        print(
            f"ok: {total} packages resolved; no Chaquopy ceiling "
            f"violations; no missing-wheel issues."
        )
        return 0

    if ceiling_blockers:
        print(
            f"FOUND {len(ceiling_blockers)} Chaquopy CEILING violation(s):",
            file=sys.stderr,
        )
        for b in ceiling_blockers:
            print(
                f"  {b.dep}: floor {b.floor} > ceiling {b.ceiling}"
                f"  (demanded by {b.demander!r}: {b.spec!r})",
                file=sys.stderr,
            )

    if missing_wheels:
        print(
            f"FOUND {len(missing_wheels)} package(s) with NO Android-installable source:",
            file=sys.stderr,
        )
        for m in missing_wheels:
            print(
                f"  {m.package}=={m.version}"
                f"  (pulled by: {m.pulled_by})"
                f"  -- not in Chaquopy index, no pure-Python wheel on PyPI",
                file=sys.stderr,
            )

    print(
        "\nFix each blocker by one of:\n"
        "  (a) for ceiling violations: relax the demander's floor;\n"
        "  (b) add to URL_REF_PACKAGES + ship an Android wheel via "
        "dep/android-dep-collection;\n"
        "  (c) add to DROPPED_PACKAGES + lazy-import on the consumer side;\n"
        "  (d) pin to an older version that has a pure-Python wheel.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
