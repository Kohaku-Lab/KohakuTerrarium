"""Unit tests for :mod:`kohakuterrarium.core.tools.output`."""

import base64
import hashlib
import types
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote

import pytest

from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.core.tools.output import (
    NormalizedToolOutput,
    OutputStats,
    materialize_image_part,
    merge_tool_metadata,
    normalize_tool_output,
    normalize_tool_result,
    output_stats,
    render_content_text,
    truncate_text_utf8,
)
from kohakuterrarium.llm.message import (
    FilePart,
    ImagePart,
    TextPart,
    content_part_from_dict,
)
from kohakuterrarium.modules.tool.base import ToolResult
from kohakuterrarium.modules.tool.media_policy import MediaPolicy
from kohakuterrarium.session.store import SessionStore

# ── helpers ──────────────────────────────────────────────────────


class _FakeArtifactStore:
    def __init__(self, root: Path, session_id: str = "sess123"):
        self.root = root
        self.session_id = session_id
        self.written: dict[str, bytes] = {}
        self.fail = False

    def write_artifact(self, filename: str, raw: bytes) -> Path:
        if self.fail:
            raise RuntimeError("disk full")
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.written[filename] = raw
        return path


def _png_data_url(payload: bytes = b"PNG-DATA") -> str:
    return f"data:image/png;base64,{base64.b64encode(payload).decode()}"


# ── truncate_text_utf8 ───────────────────────────────────────────


class TestTruncateUtf8:
    def test_zero_max_means_unlimited(self):
        text = "hello" * 1000
        out, meta = truncate_text_utf8(text, 0)
        assert out == text
        assert meta["truncated"] is False
        assert meta["original_text_bytes"] == len(text.encode())

    def test_short_text_not_truncated(self):
        out, meta = truncate_text_utf8("hi", 100)
        assert out == "hi"
        assert meta["truncated"] is False

    def test_truncates_to_byte_limit(self):
        text = "x" * 1000
        out, meta = truncate_text_utf8(text, 10)
        assert "truncated" in out
        assert meta["truncated"] is True
        assert meta["omitted_text_bytes"] >= 990
        assert meta["max_output_bytes"] == 10

    def test_truncation_note_suggests_narrower_scope_for_read(self):
        text = "x" * 1000
        out, _ = truncate_text_utf8(text, 10, tool_name="read")
        assert "offset/limit" in out
        assert "omitted sections" in out

    def test_generic_truncation_note_has_no_read_specific_hint(self):
        text = "x" * 1000
        out, _ = truncate_text_utf8(text, 10)
        assert "truncated" in out
        assert "offset/limit" not in out

    def test_truncation_note_points_at_saved_output_file(self):
        text = "x" * 1000
        out, _ = truncate_text_utf8(
            text, 10, tool_name="bash", saved_to="/tmp/kohakuterrarium-bash/bash_1.log"
        )
        assert "Full output saved to /tmp/kohakuterrarium-bash/bash_1.log" in out
        assert "use read to view it" in out
        assert "offset/limit" not in out

    def test_multibyte_safe_decode(self):
        # 3-byte chars — split position cannot fall mid-character.
        text = "日本語" * 10
        out, meta = truncate_text_utf8(text, 7)
        assert meta["truncated"] is True
        # Prefix must be valid UTF-8 (errors=ignore drops partial bytes).
        assert isinstance(out, str)


# ── render_content_text ──────────────────────────────────────────


class TestRenderContentText:
    def test_none(self):
        assert render_content_text(None) == ""

    def test_str_passthrough(self):
        assert render_content_text("hello") == "hello"

    def test_text_parts_joined(self):
        parts = [TextPart(text="a"), TextPart(text="b")]
        assert render_content_text(parts) == "a\nb"

    def test_empty_text_part_skipped(self):
        parts = [TextPart(text="a"), TextPart(text=""), TextPart(text="b")]
        assert render_content_text(parts) == "a\nb"

    def test_image_part_renders_placeholder(self):
        parts = [
            ImagePart(
                url="https://x/a.png", source_type="attachment", source_name="a.png"
            )
        ]
        out = render_content_text(parts)
        assert "https://x/a.png" in out
        assert "[attachment: a.png]" in out

    def test_data_url_image_part_redacted(self):
        parts = [ImagePart(url=_png_data_url())]
        out = render_content_text(parts)
        # No raw base64 escapes.
        assert "PNG-DATA" not in out
        assert "elided" in out

    def test_long_url_truncated(self):
        parts = [ImagePart(url="https://x/" + ("a" * 1000))]
        out = render_content_text(parts)
        assert "..." in out
        assert len(out) < 600

    def test_file_part_with_content(self):
        out = render_content_text([FilePart(name="readme.md", content="# hi")])
        assert "[file: readme.md]" in out
        assert "# hi" in out

    def test_file_part_data_redacted(self):
        out = render_content_text([FilePart(name="a.bin", data_base64="x" * 50)])
        assert "elided (50 chars)" in out

    def test_file_part_label_falls_back_to_path(self):
        out = render_content_text([FilePart(path="/tmp/x.bin")])
        assert "[file: /tmp/x.bin]" in out

    def test_dict_normalised(self):
        # Dict form gets normalized into typed parts via
        # ``normalize_content_parts`` before rendering.
        out = render_content_text([{"type": "text", "text": "hello"}])
        assert out == "hello"


# ── output_stats ─────────────────────────────────────────────────


class TestOutputStats:
    def test_simple_text(self):
        s = output_stats("hello\nworld")
        assert s.text == "hello\nworld"
        assert s.lines == 2
        assert s.bytes == 11
        assert s.preview.startswith("hello")

    def test_empty(self):
        s = output_stats("")
        assert s.lines == 0
        assert s.bytes == 0
        assert s.preview == ""

    def test_preview_truncated(self):
        s = output_stats("a" * 10_000, preview_chars=50)
        assert len(s.preview) == 50


# ── normalize_tool_output: text path ─────────────────────────────


class TestNormalizeText:
    def test_str_under_limit(self):
        n = normalize_tool_output("hello", max_output=1000)
        assert n.output == "hello"
        assert n.metadata["truncated"] is False
        assert n.stats.bytes == 5

    def test_str_truncated(self):
        n = normalize_tool_output("x" * 1000, max_output=10)
        assert n.metadata["truncated"] is True
        assert "truncated" in n.output

    def test_none_becomes_empty(self):
        n = normalize_tool_output(None, max_output=100)
        assert n.output == ""

    def test_text_property(self):
        n = normalize_tool_output("hello", max_output=100)
        assert n.text == "hello"


# ── normalize_tool_output: multimodal ────────────────────────────


class TestNormalizeMultimodal:
    def test_text_parts_truncated_total(self):
        parts = [TextPart(text="x" * 100), TextPart(text="y" * 100)]
        n = normalize_tool_output(parts, max_output=50)
        assert n.metadata["truncated"] is True
        total_kept = sum(
            len(p.text.encode())
            for p in n.output
            if isinstance(p, TextPart) and "truncated" not in p.text
        )
        # Kept bytes never exceed cap.
        assert total_kept <= 50

    def test_under_cap_not_truncated(self):
        parts = [TextPart(text="hello")]
        n = normalize_tool_output(parts, max_output=1000)
        assert n.metadata["truncated"] is False

    def test_image_part_without_store_elided(self):
        parts = [ImagePart(url=_png_data_url(b"hello-world-payload"))]
        n = normalize_tool_output(parts, max_output=10_000, artifact_store=None)
        # ImagePart replaced by TextPart placeholder.
        assert not any(isinstance(p, ImagePart) for p in n.output)
        assert n.metadata.get("data_urls_elided") == 1

    @pytest.mark.parametrize("storage", ["absent", "failing", "available"])
    def test_read_snapshot_survives_normalization(self, tmp_path, storage):
        store = None if storage == "absent" else _FakeArtifactStore(tmp_path)
        if store is not None:
            store.fail = storage == "failing"
        url = _png_data_url(b"SNAPSHOT")
        result = ToolResult(output=[ImagePart(url=url, source_type="file")])
        normalized, metadata = normalize_tool_result(
            ReadTool(), result, max_output=100, artifact_store=store
        )
        image = normalized.output[0]
        if storage == "available":
            assert image.url.startswith("/api/sessions/sess123/artifacts/")
            assert list(store.written.values()) == [b"SNAPSHOT"]
        else:
            assert image.url == url
            assert "data_urls_elided" not in metadata
        assert metadata["session_metadata"]["media"]["pinned"] is False

    def test_image_part_with_store_persisted(self, tmp_path):
        store = _FakeArtifactStore(tmp_path)
        parts = [ImagePart(url=_png_data_url(b"PNG-RAW-BYTES"))]
        n = normalize_tool_output(
            parts,
            max_output=10_000,
            artifact_store=store,
            tool_name="image_gen",
            job_id="job1",
        )
        assert n.metadata.get("data_urls_materialized") == 1
        # New ImagePart kept; URL now points to served artifact.
        new_img = next(p for p in n.output if isinstance(p, ImagePart))
        digest = hashlib.sha256(b"PNG-RAW-BYTES").hexdigest()
        assert new_img.url.startswith("/api/sessions/sess123/artifacts/")
        # Artifact actually written to disk under the content-addressed layout.
        assert any(
            f.startswith(f"tool_outputs/{digest}/")
            and store.written[f] == b"PNG-RAW-BYTES"
            for f in store.written
        )
        assert n.metadata["artifacts"][0]["relative_path"].startswith(
            f"tool_outputs/{digest}/"
        )

    def test_reference_policy_keeps_inline_media_and_writes_nothing(self, tmp_path):
        store = _FakeArtifactStore(tmp_path)
        parts = [ImagePart(url=_png_data_url(b"RAW"), source_type="file")]
        n = normalize_tool_output(
            parts,
            max_output=10_000,
            artifact_store=store,
            tool_name="read",
            media_policy=MediaPolicy(persist=False, pinned=False),
        )
        kept = next(p for p in n.output if isinstance(p, ImagePart))
        assert kept.url.startswith("data:image/png;base64,")
        assert store.written == {}
        assert "artifacts" not in n.metadata
        assert "data_urls_materialized" not in n.metadata
        # The presentation half still travels with the result.
        assert n.metadata["media_policy"] == {
            "persist": False,
            "pinned": False,
            "inline_fallback": False,
        }

    def test_file_reference_passes_through_under_any_policy(self, tmp_path):
        store = _FakeArtifactStore(tmp_path)
        ref = ImagePart(
            url="file:///tmp/pic.png", source_type="file", source_name="pic.png"
        )
        n = normalize_tool_output(
            [ref], max_output=10_000, artifact_store=store, tool_name="read"
        )
        assert n.output[0] is ref
        assert store.written == {}
        assert n.metadata["media_policy"] == {
            "persist": True,
            "pinned": True,
            "inline_fallback": False,
        }

    def test_media_policy_is_only_reported_for_media_results(self):
        n = normalize_tool_output(
            [TextPart(text="plain")],
            max_output=10_000,
            media_policy=MediaPolicy(pinned=False),
        )
        assert "media_policy" not in n.metadata

    def test_merge_lifts_media_policy_into_session_metadata(self):
        merged = merge_tool_metadata(
            {"session_metadata": {"artifacts": [{"kind": "file"}]}},
            {"media_policy": {"persist": True, "pinned": False}},
        )
        assert merged["session_metadata"]["media"] == {"persist": True, "pinned": False}
        assert merged["session_metadata"]["artifacts"] == [{"kind": "file"}]
        # One canonical location: the raw key does not survive the merge.
        assert "media_policy" not in merged

    def test_normalize_tool_result_applies_tool_policy_and_result_override(
        self, tmp_path
    ):
        store = _FakeArtifactStore(tmp_path)
        tool = types.SimpleNamespace(
            tool_name="camera", media_policy=MediaPolicy(persist=False)
        )
        result = ToolResult(
            output=[ImagePart(url=_png_data_url(b"SHOT"))],
            metadata={"media_policy": {"pinned": False}, "_image_artifact_subdir": "x"},
        )
        normalized, metadata = normalize_tool_result(
            tool, result, max_output=10_000, job_id="j1", artifact_store=store
        )
        assert normalized.output[0].url.startswith("data:image/png;base64,")
        assert store.written == {}
        assert metadata["session_metadata"]["media"] == {
            "persist": False,
            "pinned": False,
            "inline_fallback": False,
        }
        assert "media_policy" not in metadata
        assert "_image_artifact_subdir" not in metadata

    def test_generated_image_can_use_a_dedicated_subdirectory(self, tmp_path):
        store = _FakeArtifactStore(tmp_path)
        n = normalize_tool_output(
            [ImagePart(url=_png_data_url(b"GENERATED"))],
            max_output=10_000,
            artifact_store=store,
            image_subdir="generated_images",
            tool_name="grok_image_gen",
            job_id="job1",
        )

        artifact = n.metadata["artifacts"][0]
        digest = hashlib.sha256(b"GENERATED").hexdigest()
        assert artifact["kind"] == "image"
        assert artifact["relative_path"].startswith(f"generated_images/{digest}/")
        assert artifact["url"].startswith(
            f"/api/sessions/sess123/artifacts/generated_images/{digest}/"
        )

    def test_artifact_url_uses_exact_retained_directory_namespace(self, tmp_path):
        store = _FakeArtifactStore(
            tmp_path / "graph_demo_deadbeef.artifacts", session_id="graph_demo"
        )
        normalized = normalize_tool_output(
            [ImagePart(url=_png_data_url(b"RETAINED"))],
            max_output=10_000,
            artifact_store=store,
            tool_name="grok_image_gen",
        )

        assert normalized.metadata["artifacts"][0]["url"].startswith(
            "/api/sessions/graph_demo_deadbeef/artifacts/"
        )

    def test_image_decode_failure_elided(self, tmp_path):
        store = _FakeArtifactStore(tmp_path)
        # Bogus base64 that won't decode.
        parts = [ImagePart(url="data:image/png;base64,@@@bogus@@@")]
        n = normalize_tool_output(parts, max_output=10_000, artifact_store=store)
        # base64 with `validate=False` may succeed; we just verify no
        # raw payload escapes — output is either a placeholder or
        # the materialized image. Either way, no crash.
        assert (
            n.metadata.get("data_urls_elided", 0)
            + n.metadata.get("data_urls_materialized", 0)
            == 1
        )

    def test_image_persist_failure_elided(self, tmp_path):
        store = _FakeArtifactStore(tmp_path)
        store.fail = True
        parts = [ImagePart(url=_png_data_url())]
        n = normalize_tool_output(parts, max_output=10_000, artifact_store=store)
        # Falls back to placeholder.
        assert n.metadata.get("data_urls_elided") == 1

    @pytest.mark.parametrize("binary", [False, True])
    def test_filepart_original_survives_normalization_with_display_only_elision(
        self, tmp_path, binary
    ):
        raw = b"\x00\xffBINARY_REPORT"
        file = {
            "name": "report.bin" if binary else "report.txt",
            "mime": "application/octet-stream" if binary else "text/plain",
            "encoding": "base64" if binary else "utf-8",
            "is_inline": True,
            "vendor": {"token": "keep"},
        }
        file.update(
            {"data_base64": base64.b64encode(raw).decode()}
            if binary
            else {"content": "report text"}
        )
        parts = [{"type": "file", "file": file, "extension": {"source": ["tool"]}}]
        original = deepcopy(parts)
        normalized = normalize_tool_output(parts, max_output=1)
        assert [part.to_dict() for part in normalized.output] == original
        store = SessionStore(tmp_path / "file.kohakutr")
        try:
            store.append_event(
                "agent",
                "tool_result",
                {"output": [part.to_dict() for part in normalized.output]},
            )
            store.flush()
            restored = store.get_events("agent")[0]["output"]
            assert restored == original
            if binary:
                assert base64.b64decode(restored[0]["file"]["data_base64"]) == raw
                assert "elided" in normalized.stats.preview
                assert file["data_base64"] not in normalized.stats.preview
            else:
                assert restored[0]["file"]["content"] == "report text"
            assert parts == original
        finally:
            store.close()


# ── materialize_image_part standalone ────────────────────────────


class TestMaterializeImagePart:
    def test_non_data_url_returned_unchanged(self, tmp_path):
        store = _FakeArtifactStore(tmp_path)
        part = ImagePart(url="https://example.com/x.png")
        out = materialize_image_part(part, store, subdir="t")
        assert out is part

    def test_data_url_no_store_no_elide(self):
        part = ImagePart(url=_png_data_url())
        out = materialize_image_part(part, None, subdir="t", elide_without_store=False)
        # Returned unchanged when elide flag is off.
        assert out is part

    def test_data_url_no_store_with_elide(self):
        part = ImagePart(url=_png_data_url())
        out = materialize_image_part(part, None, subdir="t", elide_without_store=True)
        assert isinstance(out, TextPart)

    def test_session_id_missing_falls_back_to_disk_uri(self, tmp_path):
        store = _FakeArtifactStore(tmp_path, session_id="")
        part = ImagePart(url=_png_data_url())
        out = materialize_image_part(part, store, subdir="t")
        assert isinstance(out, ImagePart)
        # No API URL → file:// URI from disk path.
        assert out.url.startswith("file:")


def _artifact_relpath(url: str) -> str:
    return unquote(url.split("/artifacts/", 1)[1])


class TestMaterializeImagePartImmutable:
    def test_same_name_different_bytes_keeps_prior_artifact(self, tmp_path):
        store = SessionStore(str(tmp_path / "run.kohakutr"))
        try:
            first_bytes = b"PNG-FIRST-BYTES"
            second_bytes = b"PNG-SECOND-BYTES"
            first = materialize_image_part(
                ImagePart(
                    url=_png_data_url(first_bytes),
                    detail="high",
                    source_type="generated",
                    source_name="scene.png",
                ),
                store,
                subdir="generated_images",
                stem_hint="scene",
            )
            second = materialize_image_part(
                ImagePart(
                    url=_png_data_url(second_bytes),
                    detail="low",
                    source_type="attachment",
                    source_name="scene.png",
                ),
                store,
                subdir="generated_images",
                stem_hint="scene",
            )
            assert isinstance(first, ImagePart)
            assert isinstance(second, ImagePart)
            first_rel = first.artifact_relpath
            second_rel = second.artifact_relpath
            assert first_rel != second_rel
            assert first.url != second.url
            assert _artifact_relpath(first.url) == first_rel
            assert _artifact_relpath(second.url) == second_rel
            first_digest = hashlib.sha256(first_bytes).hexdigest()
            second_digest = hashlib.sha256(second_bytes).hexdigest()
            assert first_rel == f"generated_images/{first_digest}/scene.png"
            assert second_rel == f"generated_images/{second_digest}/scene.png"
            assert (store.artifacts_dir / first_rel).read_bytes() == first_bytes
            assert (store.artifacts_dir / second_rel).read_bytes() == second_bytes
            assert first.source_name == "scene.png"
            assert first.detail == "high"
            assert first.source_type == "generated"
            assert second.source_name == "scene.png"
            assert second.detail == "low"
            assert second.source_type == "attachment"
        finally:
            store.close()

    def test_identical_bytes_reuse_stable_ref_and_metadata_does_not_leak(
        self, tmp_path
    ):
        store = SessionStore(str(tmp_path / "run.kohakutr"))
        try:
            payload = b"PNG-STABLE-BYTES"
            first = materialize_image_part(
                ImagePart(
                    url=_png_data_url(payload),
                    detail="auto",
                    source_type="generated",
                    source_name="out.png",
                ),
                store,
                subdir="generated_images",
                stem_hint="out",
            )
            first.revised_prompt = "first-prompt"
            second = materialize_image_part(
                ImagePart(
                    url=_png_data_url(payload),
                    detail="high",
                    source_type="file",
                    source_name="out.png",
                ),
                store,
                subdir="generated_images",
                stem_hint="out",
            )
            assert isinstance(first, ImagePart)
            assert isinstance(second, ImagePart)
            assert first.artifact_relpath == second.artifact_relpath
            assert first.url == second.url
            assert not hasattr(second, "revised_prompt")
            assert second.detail == "high"
            assert second.source_type == "file"
            digest = hashlib.sha256(payload).hexdigest()
            assert second.artifact_relpath == f"generated_images/{digest}/out.png"
            assert (
                store.artifacts_dir / first.artifact_relpath
            ).read_bytes() == payload
        finally:
            store.close()

    def test_materialization_preserves_wire_extensions_and_detaches_metadata(
        self, tmp_path
    ):
        raw = {
            "type": "image_url",
            "image_url": {
                "url": _png_data_url(),
                "detail": "high",
                "provider_extension": {"token": "keep"},
            },
            "meta": {"source_name": "same.png", "vendor": {"id": "original"}},
            "extension": {"items": ["original"]},
        }
        original = deepcopy(raw)
        part = content_part_from_dict(raw)
        part.revised_prompt = "revised"
        store = SessionStore(str(tmp_path / "extensions.kohakutr"))
        try:
            result = materialize_image_part(
                part, store, subdir="generated_images", stem_hint="same"
            )
            expected = deepcopy(original)
            expected["image_url"]["url"] = result.url
            assert result.to_dict() == expected
            assert result.revised_prompt == "revised"
            result._persisted_fields["meta"]["vendor"]["id"] = "mutated"
            assert part.to_dict() == original
            assert raw == original
        finally:
            store.close()

    def test_jpeg_extension_and_old_layout_remain_readable(self, tmp_path):
        store = SessionStore(str(tmp_path / "run.kohakutr"))
        try:
            legacy_rel = "generated_images/scene.jpeg"
            legacy_bytes = b"JPEG-LEGACY"
            store.write_artifact(legacy_rel, legacy_bytes)
            jpeg = materialize_image_part(
                ImagePart(
                    url=f"data:image/jpeg;base64,{base64.b64encode(b'JPEG-NEW').decode()}",
                    source_name="scene.jpeg",
                ),
                store,
                subdir="generated_images",
                stem_hint="scene",
            )
            assert isinstance(jpeg, ImagePart)
            digest = hashlib.sha256(b"JPEG-NEW").hexdigest()
            assert jpeg.artifact_relpath == f"generated_images/{digest}/scene.jpeg"
            assert (store.artifacts_dir / legacy_rel).read_bytes() == legacy_bytes
            assert (
                store.artifacts_dir / jpeg.artifact_relpath
            ).read_bytes() == b"JPEG-NEW"
        finally:
            store.close()


# ── dataclass shape ──────────────────────────────────────────────


class TestDataclasses:
    def test_normalized_output_text_property(self):
        s = OutputStats(text="hi", lines=1, bytes=2, preview="hi")
        n = NormalizedToolOutput(output="hi", stats=s)
        assert n.text == "hi"


# ── data-URL decode failure WITHOUT elide returns original part (216) ──


class TestMaterializeDecodeFailureKeepPart:
    def test_bad_base64_no_store_no_elide_returns_part(self):
        from kohakuterrarium.core.tools.output import materialize_image_part

        part = ImagePart(url="data:image/png;base64,@@@notbase64@@@")
        out = materialize_image_part(
            part, artifact_store=None, subdir="t", elide_without_store=False
        )
        # Decode fails → returns original part (line 216 path).
        assert out is part

    def test_bad_base64_with_store_no_elide(self, monkeypatch, tmp_path):
        """Decode failure with a store but elide_without_store=False also
        returns the original part (line 216 inside the if-elide guard)."""
        from kohakuterrarium.core.tools.output import materialize_image_part
        import base64 as _b64

        class _Store:
            session_id = "x"

            def write_artifact(self, name, raw):
                return tmp_path / name

        def fail_decode(*a, **kw):
            raise ValueError("bad base64")

        monkeypatch.setattr(_b64, "b64decode", fail_decode)
        part = ImagePart(url="data:image/png;base64,xyz")
        out = materialize_image_part(
            part, artifact_store=_Store(), subdir="t", elide_without_store=False
        )
        assert out is part


# ── persist failure WITHOUT elide (line 232) ─────────────────────


class TestMaterializePersistFailureKeepPart:
    def test_persist_failure_no_elide(self, tmp_path):
        from kohakuterrarium.core.tools.output import materialize_image_part

        class _FailStore:
            session_id = "x"

            def write_artifact(self, *a, **kw):
                raise RuntimeError("disk full")

        part = ImagePart(url=_png_data_url())
        out = materialize_image_part(
            part,
            artifact_store=_FailStore(),
            subdir="t",
            elide_without_store=False,
        )
        # write_artifact failed + elide off → returns original part.
        assert out is part


# ── _truncate_text_parts non-TextPart pass-through (264-266) ────


class TestTruncateNonTextPartPassthrough:
    def test_image_part_passes_through_in_truncation(self):
        from kohakuterrarium.core.tools.output import _truncate_text_parts

        # Force truncation by setting text bytes over max_output, but
        # include a non-TextPart that should pass through.
        parts = [TextPart(text="x" * 50), ImagePart(url="https://x/a.png")]
        out, meta = _truncate_text_parts(parts, max_output=10)
        # ImagePart survives unchanged.
        assert any(isinstance(p, ImagePart) for p in out)


# ── _truncate_text_parts remaining<=0 omit branch (272-274) ─────


class TestTruncateRemainingZero:
    def test_excess_text_parts_omitted(self):
        from kohakuterrarium.core.tools.output import _truncate_text_parts

        # First part exhausts the budget; second part should hit the
        # ``remaining <= 0`` branch and be fully omitted.
        parts = [TextPart(text="x" * 100), TextPart(text="y" * 100)]
        out, meta = _truncate_text_parts(parts, max_output=50)
        # The truncation note mentions omitted bytes.
        assert any("truncated" in p.text for p in out if isinstance(p, TextPart))


class TestTruncateRemainingFitsWithinBudget:
    def test_part_fits_in_remaining(self):
        """When a text part fits entirely within the remaining byte
        budget, ``out.append(part); remaining -= len; continue`` fires
        (lines 272-274)."""
        from kohakuterrarium.core.tools.output import _truncate_text_parts

        # Total = 3+2+5 = 10 bytes; max_output = 8. Part 1 (3) and part 2
        # (2) both fit; part 3 (5) overflows.
        parts = [
            TextPart(text="aaa"),
            TextPart(text="bb"),
            TextPart(text="ccccc"),
        ]
        out, meta = _truncate_text_parts(parts, max_output=8)
        assert meta["truncated"] is True
        # The first two parts survive intact via the fits-in-remaining
        # branch.
        texts = [p.text for p in out if isinstance(p, TextPart)]
        assert "aaa" in texts
        assert "bb" in texts


# ── _render_image_placeholder no-URL description-only (line 320) ──


class TestRenderImagePlaceholderNoURL:
    def test_no_url_returns_description(self):
        from kohakuterrarium.core.tools.output import _render_image_placeholder

        part = ImagePart(url="", source_type="emoji", source_name="smile")
        out = _render_image_placeholder(part)
        assert "emoji" in out and "smile" in out
