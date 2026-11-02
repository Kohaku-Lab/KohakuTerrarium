"""Synthetic Codex pet archives without third-party artwork."""

import io
import json
import zipfile

from PIL import Image, ImageDraw


def pet_archive(
    *,
    version=2,
    metadata=None,
    cells=None,
    prefix="",
    extra=None,
    opaque=False,
    size=None,
    image_format="PNG",
):
    rows = 11 if version == 2 else 9
    image = Image.new(
        "RGBA", size or (1536, rows * 208), (0, 0, 0, 255 if opaque else 0)
    )
    counts = cells or [6, 8, 8, 4, 5, 8, 6, 6, 6, 8, 8][:rows]
    draw = ImageDraw.Draw(image)
    for row, count in enumerate(counts):
        for col in range(count) if isinstance(count, int) else count:
            draw.rectangle(
                (col * 192 + 20, row * 208 + 20, col * 192 + 50, row * 208 + 50),
                fill=(30, 160, 160, 255),
            )
    content = io.BytesIO()
    image.save(
        content, image_format, **({"lossless": True} if image_format == "WEBP" else {})
    )
    image_name = "spritesheet.webp" if image_format == "WEBP" else "spritesheet.png"
    manifest = {
        "id": "synthetic",
        "displayName": "Synthetic pet",
        "spriteVersionNumber": version,
        "spritesheetPath": image_name,
        **(metadata or {}),
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(prefix + "pet.json", json.dumps(manifest))
        z.writestr(prefix + image_name, content.getvalue())
        for key, value in (extra or {}).items():
            z.writestr(key, value)
    return archive.getvalue()
