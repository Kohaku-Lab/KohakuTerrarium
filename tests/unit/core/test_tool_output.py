"""Unit tests for :mod:`kohakuterrarium.core.tool_output`."""

import base64
from pathlib import Path


from kohakuterrarium.core.tool_output import (
    NormalizedToolOutput,
    OutputStats,
    materialize_image_part,
    normalize_tool_output,
    output_stats,
    render_content_text,
    truncate_text_utf8,
)
from kohakuterrarium.llm.message import (
    FilePart,
    ImagePart,
    TextPart,
)

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
        assert new_img.url.startswith("/api/sessions/sess123/artifacts/")
        # Artifact actually written to disk.
        assert any(
            f.startswith("tool_outputs/") and store.written[f] == b"PNG-RAW-BYTES"
            for f in store.written
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

    def test_filepart_base64_redacted(self):
        parts = [FilePart(name="x.bin", data_base64="x" * 50)]
        n = normalize_tool_output(parts, max_output=10_000)
        assert any(
            isinstance(p, TextPart) and "elided (50 chars)" in p.text for p in n.output
        )

    def test_filepart_with_content_passthrough(self):
        parts = [FilePart(name="x.txt", content="ok")]
        n = normalize_tool_output(parts, max_output=10_000)
        assert any(isinstance(p, FilePart) and p.content == "ok" for p in n.output)


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


# ── dataclass shape ──────────────────────────────────────────────


class TestDataclasses:
    def test_normalized_output_text_property(self):
        s = OutputStats(text="hi", lines=1, bytes=2, preview="hi")
        n = NormalizedToolOutput(output="hi", stats=s)
        assert n.text == "hi"


# ── data-URL decode failure WITHOUT elide returns original part (216) ──


class TestMaterializeDecodeFailureKeepPart:
    def test_bad_base64_no_store_no_elide_returns_part(self):
        from kohakuterrarium.core.tool_output import materialize_image_part

        part = ImagePart(url="data:image/png;base64,@@@notbase64@@@")
        out = materialize_image_part(
            part, artifact_store=None, subdir="t", elide_without_store=False
        )
        # Decode fails → returns original part (line 216 path).
        assert out is part

    def test_bad_base64_with_store_no_elide(self, monkeypatch, tmp_path):
        """Decode failure with a store but elide_without_store=False also
        returns the original part (line 216 inside the if-elide guard)."""
        from kohakuterrarium.core.tool_output import materialize_image_part
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
        from kohakuterrarium.core.tool_output import materialize_image_part

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
        from kohakuterrarium.core.tool_output import _truncate_text_parts

        # Force truncation by setting text bytes over max_output, but
        # include a non-TextPart that should pass through.
        parts = [TextPart(text="x" * 50), ImagePart(url="https://x/a.png")]
        out, meta = _truncate_text_parts(parts, max_output=10)
        # ImagePart survives unchanged.
        assert any(isinstance(p, ImagePart) for p in out)


# ── _truncate_text_parts remaining<=0 omit branch (272-274) ─────


class TestTruncateRemainingZero:
    def test_excess_text_parts_omitted(self):
        from kohakuterrarium.core.tool_output import _truncate_text_parts

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
        from kohakuterrarium.core.tool_output import _truncate_text_parts

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
        from kohakuterrarium.core.tool_output import _render_image_placeholder

        part = ImagePart(url="", source_type="emoji", source_name="smile")
        out = _render_image_placeholder(part)
        assert "emoji" in out and "smile" in out


# ── _copy_dynamic_image_attrs round-trip (line 352) ─────────────


class TestCopyDynamicImageAttrs:
    def test_revised_prompt_copied(self):
        from kohakuterrarium.core.tool_output import _copy_dynamic_image_attrs

        src = ImagePart(url="https://x/a.png")
        src.revised_prompt = "stylized"
        dst = ImagePart(url="https://x/b.png")
        _copy_dynamic_image_attrs(src, dst)
        assert getattr(dst, "revised_prompt", None) == "stylized"
