"""Small Live2D resource exports shared by store and HTTP workflows."""

import io
import json
import zipfile

import pytest
from PIL import Image

from examples.packages.live2d_pet.kt_live2d.model_adapters import (
    DEEPSEEK_EXPRESSIONS,
    DEEPSEEK_WORK,
)


@pytest.fixture
def model_zip(tmp_path):
    """Small exported asset files; real Cubism validation has separate coverage."""
    image = io.BytesIO()
    Image.new("RGBA", (16, 32), (180, 100, 30, 120)).save(image, format="PNG")
    entries = {
        "fish.model3.json": json.dumps(
            {
                "Version": 3,
                "FileReferences": {
                    "Moc": "fish.moc3",
                    "Physics": "fish.physics3.json",
                    "Textures": ["texture.png"],
                },
            }
        ).encode(),
        "fish.moc3": b"MOC3" + bytes(100),
        "fish.physics3.json": b'{"Version":3}',
        "texture.png": image.getvalue(),
        "NOTICE.txt": b"Keep the supplied notice.",
        "unrelated.html": b"<script>bad()</script>",
    }
    entries.update(
        {
            f"Exp3/{name}.exp3.json": b'{"Parameters":[]}'
            for name in DEEPSEEK_EXPRESSIONS
        }
    )
    entries.update(
        {
            name: b'{"Curves":[],"Meta":{"Duration":1}}'
            for name in (*DEEPSEEK_WORK, "Animation/IDLE.motion3.json")
        }
    )

    def write(changes=None, **metadata):
        source = tmp_path / "model.zip"
        values = {**entries, **(changes or {})}
        if metadata:
            manifest = json.loads(values["fish.model3.json"])
            values["fish.model3.json"] = json.dumps({**manifest, **metadata}).encode()
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in values.items():
                if data is not None:
                    archive.writestr(f"export/{name}", data)
        return source

    return write
