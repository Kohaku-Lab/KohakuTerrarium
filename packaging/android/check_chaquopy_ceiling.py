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
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version

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
    {"kohakuvault", "pydantic-core", "safetensors", "tokenizers", "primp"}
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
    args = parser.parse_args(argv)

    if args.report:
        with open(args.report, encoding="utf-8") as f:
            report = json.load(f)
    else:
        try:
            report = _resolve_install_report(args.project)
        except subprocess.CalledProcessError as exc:
            print(f"error: pip dry-run failed: {exc}", file=sys.stderr)
            return 2

    blockers = analyse(report)
    if not blockers:
        print(
            f"ok: {len(report.get('install', []))} packages resolved; "
            f"no Chaquopy ceiling violations."
        )
        return 0
    print(f"FOUND {len(blockers)} Chaquopy ceiling violation(s):", file=sys.stderr)
    for b in blockers:
        print(
            f"  {b.dep}: floor {b.floor} > Chaquopy ceiling {b.ceiling}"
            f"  (demanded by {b.demander!r}: {b.spec!r})",
            file=sys.stderr,
        )
    print(
        "Fix one of: (a) relax the demander's floor; "
        "(b) add the package to URL_REF_PACKAGES + ship an Android wheel "
        "via dep/android-dep-collection; "
        "(c) add to DROPPED_PACKAGES + lazy-import on the consumer side.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
