"""Generate Briefcase-Android icon + splash variants from ``app.png``.

Run from the repo root::

    python packaging/android/gen_icons.py

Briefcase's icon resolver (see ``briefcase/commands/create.py::install_image``)
expects ``<icon-path>-<variant>-<size>.png``.  The Android cookiecutter
template (``briefcase-android-gradle-template``) declares 20 icon
targets in its ``briefcase.toml``:

    icon.round.{48,72,96,144,192}      ic_launcher_round.png  (mipmap-*)
    icon.square.{48,72,96,144,192}     ic_launcher.png        (mipmap-*)
    icon.square.{320,480,640,960,1280} splash.png             (mipmap-*)
    icon.adaptive.{108,162,216,324,432} ic_launcher_foreground.png

A previous attempt named the files ``app.round-icon-48.png`` (dot +
"icon" word) — Briefcase silently fell back to its default Beeware bee
because the source-filename pattern is ``-<variant>-<size>``, not
``.<variant>-icon-<size>``.  This script regenerates the full 20-file
set with the correct names.  Round variants apply a circular alpha
mask; adaptive variants inset the artwork to the central 66/108 safe
zone per the Material adaptive-icon spec; splash variants are plain
Lanczos resizes on a transparent canvas (the Android template
composites them over the configured launcher background colour).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ICON_DIR = Path(__file__).resolve().parents[2] / "src" / "kohakuterrarium" / "app_icons"
SOURCE_PNG = ICON_DIR / "app.png"

# (variant, sizes) tuples — match the Android template's icon-target table.
LAUNCHER_SIZES = (48, 72, 96, 144, 192)
SPLASH_SIZES = (320, 480, 640, 960, 1280)
ADAPTIVE_SIZES = (108, 162, 216, 324, 432)

# Adaptive-icon safe-zone ratio.  The Material spec specifies the
# inner 66dp of the 108dp canvas is "always visible"; the surrounding
# 21dp gutter on each side can be cropped by the system mask.  We
# inset the artwork so even circular system masks won't clip it.
ADAPTIVE_SAFE_RATIO = 66.0 / 108.0  # ≈0.611


def _resize(src: Image.Image, size: int) -> Image.Image:
    """Lanczos resize to a square of ``size``×``size`` pixels."""
    return src.resize((size, size), Image.LANCZOS)


def _round_mask(size: int) -> Image.Image:
    """Return a circular alpha mask sized ``size``×``size``."""
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def _make_round(src: Image.Image, size: int) -> Image.Image:
    """Square resize + circular alpha clip."""
    base = _resize(src, size).convert("RGBA")
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(base, (0, 0), _round_mask(size))
    return out


def _make_square(src: Image.Image, size: int) -> Image.Image:
    """Plain Lanczos resize."""
    return _resize(src, size).convert("RGBA")


def _make_adaptive(src: Image.Image, size: int) -> Image.Image:
    """Inset the artwork to the central safe zone on a transparent canvas.

    Without the inset, the system's adaptive-icon mask (especially the
    "circle" shape used by Pixel launchers) crops the outer corners of
    the artwork — which on a mascot logo with character ears / hair
    lopped means a recognisable icon turns unrecognisable.
    """
    inner = max(1, int(round(size * ADAPTIVE_SAFE_RATIO)))
    inset_art = _resize(src, inner).convert("RGBA")
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = (size - inner) // 2
    out.paste(inset_art, (offset, offset), inset_art)
    return out


def _make_splash(src: Image.Image, size: int) -> Image.Image:
    """Splash variant — artwork centred on a transparent canvas.

    The artwork is sized to ~60% of the canvas (the rest is bleed for
    the Android launch screen).  This matches the practical layout of
    Material Design splash screens: a centred logo on a solid
    background colour the template fills in at runtime.
    """
    inner = max(1, int(round(size * 0.6)))
    inset_art = _resize(src, inner).convert("RGBA")
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = (size - inner) // 2
    out.paste(inset_art, (offset, offset), inset_art)
    return out


def main() -> None:
    if not SOURCE_PNG.exists():
        raise SystemExit(f"Source not found: {SOURCE_PNG}")

    src = Image.open(SOURCE_PNG).convert("RGBA")
    print(f"Source: {SOURCE_PNG.name}  {src.size[0]}x{src.size[1]} {src.mode}")

    written: list[str] = []

    for size in LAUNCHER_SIZES:
        out_round = ICON_DIR / f"app-round-{size}.png"
        out_square = ICON_DIR / f"app-square-{size}.png"
        _make_round(src, size).save(out_round)
        _make_square(src, size).save(out_square)
        written.extend([out_round.name, out_square.name])

    for size in SPLASH_SIZES:
        out_splash = ICON_DIR / f"app-square-{size}.png"
        _make_splash(src, size).save(out_splash)
        written.append(out_splash.name)

    for size in ADAPTIVE_SIZES:
        out_adaptive = ICON_DIR / f"app-adaptive-{size}.png"
        _make_adaptive(src, size).save(out_adaptive)
        written.append(out_adaptive.name)

    print(f"Wrote {len(written)} files to {ICON_DIR}:")
    for name in written:
        print(f"  {name}")


if __name__ == "__main__":
    main()
