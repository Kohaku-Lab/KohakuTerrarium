"""Prebuild the offline wheel cache the Briefcase wrapper bundles.

The thin-wrapper Briefcase artifact (topic 06) installs the framework
into its managed venv on first launch.  When the user's machine is
offline at first launch, the bundled wheel cache under
``wheels-bundle/`` is the only thing standing between them and a
broken app.

This script is invoked by the ``build-desktop`` CI job and by
operators building local Briefcase artifacts:

    python scripts/build_wrapper_wheels.py [--version 1.5.0] [--out wheels-bundle/]

It runs ``pip download`` with ``--no-binary=:none:`` disabled (we want
binary wheels for speed) into the target directory.  All transitive
deps of ``kohakuterrarium`` are pulled — first launch is then a pure
``pip install --no-index --find-links=wheels-bundle/ kohakuterrarium``.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prebuild wheels-bundle/ for the Briefcase thin wrapper."
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Pin a specific kohakuterrarium version (e.g. '1.5.0'). "
        "Default = the currently-installed version's exact pin.",
    )
    parser.add_argument(
        "--out",
        default="wheels-bundle",
        help="Target directory (created/cleared).  Default: wheels-bundle/",
    )
    parser.add_argument(
        "--from-source",
        action="store_true",
        help="Build the kohakuterrarium wheel from THIS checkout (instead of pulling from PyPI). "
        "Use during local builds when the version isn't published yet.",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    if args.from_source:
        print(f"[wheels] building kohakuterrarium wheel from source -> {out}")
        try:
            subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[wheels] build failed: {e}", file=sys.stderr)
            return 1
        # Then pip-download the transitive deps from the bundled wheel.
        wheels = list(out.glob("kohakuterrarium-*.whl"))
        if not wheels:
            print("[wheels] no kohakuterrarium wheel produced", file=sys.stderr)
            return 1
        target_spec = str(wheels[0])
    else:
        if args.version:
            target_spec = f"kohakuterrarium=={args.version}"
        else:
            import importlib.metadata

            try:
                installed = importlib.metadata.version("kohakuterrarium")
            except importlib.metadata.PackageNotFoundError:
                print(
                    "[wheels] cannot infer version — pass --version or "
                    "install the package first",
                    file=sys.stderr,
                )
                return 2
            target_spec = f"kohakuterrarium=={installed}"

    print(f"[wheels] downloading deps for {target_spec} -> {out}")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--dest",
                str(out),
                "--prefer-binary",
                target_spec,
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[wheels] pip download failed: {e}", file=sys.stderr)
        return 1

    count = len(list(out.glob("*.whl"))) + len(list(out.glob("*.tar.gz")))
    print(f"[wheels] done — {count} artifacts in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
