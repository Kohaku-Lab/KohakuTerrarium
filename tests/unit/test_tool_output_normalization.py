"""Tests for central tool-output normalization."""

import base64
from pathlib import Path

from kohakuterrarium.core.tool_output import normalize_tool_output, render_content_text
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.session.store import SessionStore


def test_byte_safe_text_truncation():
    result = normalize_tool_output("é" * 10, max_output=5)
    text = result.output
    assert isinstance(text, str)
    assert text.startswith("éé")
    assert "truncated" in text
    assert result.metadata["truncated"] is True
    assert result.metadata["original_text_bytes"] == 20


def test_multimodal_text_part_truncation_preserves_image_placeholder():
    parts = [TextPart(text="abcdef"), ImagePart(url="https://example.com/cat.png")]
    result = normalize_tool_output(parts, max_output=3)
    assert isinstance(result.output, list)
    rendered = render_content_text(result.output)
    assert "abc" in rendered
    assert "truncated" in rendered
    assert "https://example.com/cat.png" in rendered


def test_image_data_url_materialized_to_artifact(tmp_path: Path):
    store = SessionStore(tmp_path / "s.kohakutr")
    payload = base64.b64encode(b"PNGDATA").decode("ascii")
    src = ImagePart(
        url=f"data:image/png;base64,{payload}",
        source_type="test",
        source_name="img1",
    )

    result = normalize_tool_output(
        [TextPart(text="image"), src],
        max_output=0,
        job_id="job_1",
        tool_name="mock",
        artifact_store=store,
    )

    assert isinstance(result.output, list)
    images = [p for p in result.output if isinstance(p, ImagePart)]
    assert len(images) == 1
    assert images[0].url.startswith("/api/sessions/s/artifacts/tool_outputs/")
    assert (store.artifacts_dir / "tool_outputs").is_dir()
    assert "data:image" not in render_content_text(result.output)


def test_image_data_url_elided_without_artifact_store():
    payload = base64.b64encode(b"raw").decode("ascii")
    result = normalize_tool_output(
        [ImagePart(url=f"data:image/png;base64,{payload}", source_name="raw")],
        max_output=0,
        artifact_store=None,
    )

    text = render_content_text(result.output)
    assert "base64 elided" in text
    assert payload not in text
