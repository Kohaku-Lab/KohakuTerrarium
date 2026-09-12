"""Unit tests for :class:`CanvasImageTool`.

The tool is the promote door: a local image path becomes a pinned
``ImagePart`` plus ``canvas_preview.kind == "image"``. ``read`` of the
same file must stay a look-up, so this test pins the metadata the
Studio canvas actually keys on.
"""

from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image

from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.builtins.tools.canvas_image import CanvasImageTool
from kohakuterrarium.modules.tool.base import ToolContext


class _Store:
    def __init__(self, tmp: Path) -> None:
        self.path = tmp / "run.kohakutr"
        self.session_id = "run"
        self.dir = tmp / "run.artifacts"
        self.dir.mkdir()
        self.written: list[tuple[str, bytes]] = []

    def write_artifact(self, filename: str, data: bytes) -> Path:
        self.written.append((filename, data))
        dest = self.dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest


class _Agent:
    def __init__(self, store: _Store | None = None) -> None:
        self.session_store = store


class _Guard:
    def __init__(self, message: str) -> None:
        self.message = message

    def check(self, path: str) -> str:
        return self.message


def _ctx(tmp_path: Path, *, agent=None, path_guard=None) -> ToolContext:
    return ToolContext(
        agent_name="test",
        session=None,
        working_dir=tmp_path,
        agent=agent,
        path_guard=path_guard,
    )


def _png(path: Path) -> Path:
    Image.new("RGB", (2, 2), (220, 40, 40)).save(path, "PNG")
    return path


def _image_part(result) -> ImagePart:
    parts = result.output
    assert isinstance(parts, list)
    images = [p for p in parts if isinstance(p, ImagePart)]
    assert images, f"expected an ImagePart, got {parts!r}"
    return images[0]


class TestCanvasImagePromotes:
    @pytest.mark.parametrize("names", [("out.png", "out.png"), ("a b.png", "a_b.png")])
    async def test_publications_preserve_prior_bytes(self, tmp_path, names):
        store = SessionStore(str(tmp_path / "run.kohakutr"))
        try:
            paths = []
            for folder, name, color in zip(("left", "right"), names, ("red", "blue")):
                directory = tmp_path / folder
                directory.mkdir()
                path = directory / name
                Image.new("RGB", (2, 2), color).save(path)
                paths.append(path)
            ctx = _ctx(tmp_path, agent=_Agent(store))
            first = await CanvasImageTool()._execute({"path": str(paths[0])}, ctx)
            second = await CanvasImageTool()._execute({"path": str(paths[1])}, ctx)
            first_url = _image_part(first).url
            second_url = _image_part(second).url
            assert first_url != second_url
            for url, source in ((first_url, paths[0]), (second_url, paths[1])):
                relative = unquote(url.split("/artifacts/", 1)[1])
                assert (
                    store.artifacts_dir / relative
                ).read_bytes() == source.read_bytes()

            original = paths[0].read_bytes()
            Image.new("RGB", (2, 2), "green").save(paths[0])
            updated = await CanvasImageTool()._execute({"path": str(paths[0])}, ctx)
            repeated = await CanvasImageTool()._execute({"path": str(paths[0])}, ctx)
            assert _image_part(updated).url != first_url
            assert _image_part(repeated).url == _image_part(updated).url
            relative = unquote(first_url.split("/artifacts/", 1)[1])
            assert (store.artifacts_dir / relative).read_bytes() == original
        finally:
            store.close()

    async def test_rejects_unsupported_decoded_format(self, tmp_path):
        path = tmp_path / "disguised.png"
        Image.new("RGB", (2, 2), "red").save(path, "TIFF")
        result = await CanvasImageTool()._execute({"path": str(path)}, _ctx(tmp_path))
        assert result.error
        assert "unsupported" in result.error
        assert result.metadata.get("canvas_preview") is None

    async def test_missing_path_errors(self, tmp_path):
        result = await CanvasImageTool()._execute({}, _ctx(tmp_path))
        assert result.error
        assert result.metadata.get("canvas_preview") is None

    async def test_missing_file_errors(self, tmp_path):
        result = await CanvasImageTool()._execute(
            {"path": str(tmp_path / "gone.png")}, _ctx(tmp_path)
        )
        assert result.error
        assert "not found" in result.error.lower()
        assert result.metadata.get("canvas_preview") is None

    async def test_directory_errors(self, tmp_path):
        result = await CanvasImageTool()._execute(
            {"path": str(tmp_path)}, _ctx(tmp_path)
        )
        assert result.error
        assert result.metadata.get("canvas_preview") is None

    async def test_non_image_errors(self, tmp_path):
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        result = await CanvasImageTool()._execute({"path": str(txt)}, _ctx(tmp_path))
        assert result.error
        assert result.metadata.get("canvas_preview") is None

    async def test_corrupt_png_errors(self, tmp_path):
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not an image")
        result = await CanvasImageTool()._execute({"path": str(bad)}, _ctx(tmp_path))
        assert result.error
        assert result.metadata.get("canvas_preview") is None

    async def test_path_guard_blocks(self, tmp_path):
        png = _png(tmp_path / "secret.png")
        result = await CanvasImageTool()._execute(
            {"path": str(png)},
            _ctx(tmp_path, path_guard=_Guard("blocked by policy")),
        )
        assert result.error == "blocked by policy"
        assert result.metadata.get("canvas_preview") is None

    async def test_promotes_without_session_store(self, tmp_path):
        png = _png(tmp_path / "out.png")
        result = await CanvasImageTool()._execute({"path": str(png)}, _ctx(tmp_path))
        assert result.success
        preview = result.metadata["canvas_preview"]
        resolved = png.resolve()
        assert preview["kind"] == "image"
        assert preview["file_path"] == str(resolved)
        assert preview["lang"] in {"png", "jpg"}
        assert preview["content"] == resolved.as_uri()
        assert preview["truncated"] is False
        image = _image_part(result)
        assert image.url == resolved.as_uri()
        assert image.source_type == "file"
        assert image.source_name == "out.png"

    async def test_copies_into_session_artifacts(self, tmp_path):
        png = _png(tmp_path / "shot.png")
        store = _Store(tmp_path)
        result = await CanvasImageTool()._execute(
            {"path": str(png)},
            _ctx(tmp_path, agent=_Agent(store)),
        )
        assert result.success
        assert store.written
        rel, data = store.written[0]
        assert rel.startswith("canvas_images/")
        assert data == png.read_bytes()
        preview = result.metadata["canvas_preview"]
        assert preview["kind"] == "image"
        assert preview["file_path"] == str(png.resolve())
        assert preview["content"].startswith("/api/sessions/")
        assert "artifacts/" in preview["content"]
        image = _image_part(result)
        assert image.url == preview["content"]

    async def test_artifact_write_failure_falls_back_to_file_uri(self, tmp_path):
        class _BadStore:
            def write_artifact(self, filename: str, data: bytes) -> Path:
                raise RuntimeError("disk full")

        png = _png(tmp_path / "x.png")
        result = await CanvasImageTool()._execute(
            {"path": str(png)},
            _ctx(tmp_path, agent=_Agent(_BadStore())),
        )
        assert result.success
        assert result.metadata["canvas_preview"]["content"] == png.resolve().as_uri()

    async def test_too_large_errors(self, tmp_path, monkeypatch):
        from kohakuterrarium.builtins.tools import canvas_image as mod

        png = _png(tmp_path / "big.png")
        monkeypatch.setattr(mod, "MAX_IMAGE_BYTES", 1)
        result = await CanvasImageTool()._execute({"path": str(png)}, _ctx(tmp_path))
        assert result.error
        assert "large" in result.error.lower()
        assert result.metadata.get("canvas_preview") is None


def test_registered_as_builtin():
    from kohakuterrarium.builtins.tool_catalog import get_builtin_tool

    assert get_builtin_tool("canvas_image") is not None
