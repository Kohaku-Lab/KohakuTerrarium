"""Build the terminal runtime archive the one-line installers download.

One archive per platform, holding what the desktop app ships beside its window:

* ``python/``     the pinned relocatable CPython (``packaging/desktop/python-runtime.json``)
* ``bootstrap/``  the launcher package plus its two pure-Python dependencies

The installers unpack it into ``~/.kohakuterrarium/runtime/terminal/``; the launcher in it
then installs the release tree from the channel feed (``--install``), the same trees the
desktop app uses. Output: ``kt-runtime-<platform>.tar.gz`` (``.zip`` on Windows, which
PowerShell unpacks without tar) and ``<archive>.sha256``.

Usage::

    python packaging/terminal/build_runtime.py --platform win-x64 --out dist/terminal/

The bootstrap's dependencies are installed with the staged interpreter, so build each
platform on that platform (as CI does).
"""

import argparse
import hashlib
import json
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parent / "desktop"
sys.path.insert(0, str(DESKTOP))

import stage_runtime  # noqa: E402  (the desktop staging helpers, reused as they are)

ARCHIVE_ROOT = "kt-runtime"


def archive_name(platform: str) -> str:
    suffix = "zip" if platform.startswith("win") else "tar.gz"
    return f"kt-runtime-{platform}.{suffix}"


def stage(platform: str, root: Path, python_archive: Path | None = None) -> Path:
    """``root/python`` and ``root/bootstrap`` for ``platform``; ``python_archive`` is a
    local copy of the pinned interpreter archive (else it is downloaded and checked)."""
    pin = json.loads(stage_runtime.RUNTIME_PIN.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as scratch:
        archive = python_archive or stage_runtime.download_python(
            pin, platform, Path(scratch)
        )
        stage_runtime.extract_python(archive, root / "python")
    python = stage_runtime.interpreter_path(root / "python", platform)
    stage_runtime.stage_bootstrap(
        stage_runtime.PACKAGE, root / "bootstrap", python=python
    )
    return root


def pack(root: Path, out: Path, platform: str) -> Path:
    """The staged ``root`` as the platform's archive (entries under ``kt-runtime/``)."""
    out.mkdir(parents=True, exist_ok=True)
    target = out / archive_name(platform)
    if platform.startswith("win"):
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zf.write(path, f"{ARCHIVE_ROOT}/{path.relative_to(root).as_posix()}")
    else:
        with tarfile.open(target, "w:gz") as tar:
            tar.add(root, arcname=ARCHIVE_ROOT)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name(target.name + ".sha256").write_text(
        f"{digest}  {target.name}\n", encoding="utf-8"
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--platform", required=True, choices=sorted(stage_runtime._triples())
    )
    parser.add_argument("--out", type=Path, default=Path("dist/terminal"))
    parser.add_argument(
        "--python-archive",
        type=Path,
        default=None,
        help="A local copy of the pinned interpreter archive (skips the download).",
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as scratch:
        root = stage(args.platform, Path(scratch) / "stage", args.python_archive)
        target = pack(root, args.out, args.platform)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
