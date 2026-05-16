"""Unit tests for :mod:`kohakuterrarium.prompt.skill_loader`.

The skill loader parses markdown with optional YAML frontmatter into a
:class:`SkillDoc`. Contract:

- Frontmatter keys bucket into three places: native (``name`` /
  ``description`` / ``category`` / ``tags``), agentskills.io standard
  (``license``, ``allowed-tools``, …) into ``standard``, everything
  else into ``extra``. ``raw_frontmatter`` keeps the full dict.
- A leading BOM and CR/CRLF endings are normalised before the ``---``
  frontmatter probe.
- Bad encoding / malformed YAML / missing file degrade to ``None`` or
  ``({}, text)`` — a single broken skill never crashes the loader.
- ``SkillDoc.metadata`` is a deprecated alias of ``.extra`` and warns.
"""

import warnings

from kohakuterrarium.prompt.skill_loader import (
    SkillDoc,
    _normalize_skill_text,
    load_skill_doc,
    load_skill_docs_from_dir,
    parse_frontmatter,
    read_skill_text,
)


class TestParseFrontmatter:
    def test_no_frontmatter_returns_empty_meta_and_full_text(self):
        meta, content = parse_frontmatter("# Just a heading\n\nbody")
        assert meta == {}
        assert content == "# Just a heading\n\nbody"

    def test_parses_yaml_frontmatter_and_strips_it(self):
        text = "---\nname: bash\ndescription: run shell\n---\n\n# Bash\n\nbody here"
        meta, content = parse_frontmatter(text)
        assert meta == {"name": "bash", "description": "run shell"}
        assert content == "# Bash\n\nbody here"

    def test_bom_prefixed_frontmatter_is_detected(self):
        text = "﻿---\nname: x\n---\nbody"
        meta, content = parse_frontmatter(text)
        assert meta == {"name": "x"}
        assert content == "body"

    def test_crlf_endings_normalised(self):
        text = "---\r\nname: y\r\n---\r\nbody\r\nmore"
        meta, content = parse_frontmatter(text)
        assert meta == {"name": "y"}
        assert content == "body\nmore"

    def test_unterminated_frontmatter_treated_as_no_frontmatter(self):
        text = "---\nname: z\nno closing delimiter"
        meta, content = parse_frontmatter(text)
        assert meta == {}
        assert content == text.strip()

    def test_malformed_yaml_degrades_to_empty_meta(self):
        text = "---\nname: : : broken\n  bad indent\n---\nbody"
        meta, content = parse_frontmatter(text)
        assert meta == {}
        assert content == "body"

    def test_yaml_that_parses_to_non_dict_is_treated_as_absent(self):
        # A bare scalar between the fences is valid YAML but useless.
        text = "---\njust a string\n---\nbody"
        meta, content = parse_frontmatter(text)
        assert meta == {}
        assert content == "body"

    def test_non_string_input_returns_empty(self):
        assert parse_frontmatter(None) == ({}, "")


class TestNormalizeSkillText:
    def test_non_string_input_returns_empty_string(self):
        assert _normalize_skill_text(None) == ""
        assert _normalize_skill_text(123) == ""

    def test_strips_leading_bom(self):
        assert _normalize_skill_text("﻿hello") == "hello"

    def test_folds_crlf_and_cr_to_lf(self):
        assert _normalize_skill_text("a\r\nb\rc") == "a\nb\nc"

    def test_clean_text_passthrough(self):
        assert _normalize_skill_text("already clean") == "already clean"


class TestReadSkillText:
    def test_reads_utf8_file(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text("clean utf8 — 日本語", encoding="utf-8")
        assert read_skill_text(f) == "clean utf8 — 日本語"

    def test_strips_bom_and_normalises_crlf(self, tmp_path):
        f = tmp_path / "b.md"
        f.write_bytes("﻿line1\r\nline2".encode("utf-8"))
        assert read_skill_text(f) == "line1\nline2"

    def test_missing_file_returns_none(self, tmp_path):
        assert read_skill_text(tmp_path / "absent.md") is None

    def test_non_utf8_bytes_recovered_via_fallback(self, tmp_path):
        f = tmp_path / "latin.md"
        # 0xff is invalid UTF-8; latin-1 fallback decodes it as ÿ.
        f.write_bytes(b"caf\xe9")
        out = read_skill_text(f)
        assert out is not None
        assert "caf" in out

    def test_unreadable_path_returns_none(self, tmp_path):
        # A directory exists but read_bytes() raises OSError -> None.
        d = tmp_path / "a_directory"
        d.mkdir()
        assert read_skill_text(d) is None


class TestLoadSkillDoc:
    def test_missing_file_returns_none(self, tmp_path):
        assert load_skill_doc(tmp_path / "no.md") is None

    def test_unreadable_file_returns_none(self, tmp_path):
        # Path exists but cannot be read as bytes -> degrade to None.
        d = tmp_path / "dir_skill"
        d.mkdir()
        assert load_skill_doc(d) is None

    def test_native_fields_promoted_to_attributes(self, tmp_path):
        f = tmp_path / "read.md"
        f.write_text(
            "---\n"
            "name: read\n"
            "description: Read file contents\n"
            "category: builtin\n"
            "tags: [file, io]\n"
            "---\n"
            "# Read\n\nFull docs.",
            encoding="utf-8",
        )
        doc = load_skill_doc(f)
        assert doc.name == "read"
        assert doc.description == "Read file contents"
        assert doc.category == "builtin"
        assert doc.tags == ["file", "io"]
        assert doc.content == "# Read\n\nFull docs."

    def test_standard_keys_land_in_standard_bucket(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text(
            "---\nname: s\nlicense: MIT\nallowed-tools: [bash]\n---\nbody",
            encoding="utf-8",
        )
        doc = load_skill_doc(f)
        assert doc.standard == {"license": "MIT", "allowed-tools": ["bash"]}
        assert doc.extra == {}

    def test_unknown_keys_land_in_extra_bucket(self, tmp_path):
        f = tmp_path / "u.md"
        f.write_text(
            "---\nname: u\nsome_custom_key: value123\n---\nbody",
            encoding="utf-8",
        )
        doc = load_skill_doc(f)
        assert doc.extra == {"some_custom_key": "value123"}
        assert doc.standard == {}

    def test_native_keys_excluded_from_standard_and_extra(self, tmp_path):
        f = tmp_path / "n.md"
        f.write_text(
            "---\nname: n\ndescription: d\ncategory: c\ntags: [t]\n---\nbody",
            encoding="utf-8",
        )
        doc = load_skill_doc(f)
        assert doc.standard == {}
        assert doc.extra == {}

    def test_raw_frontmatter_keeps_full_dict(self, tmp_path):
        f = tmp_path / "r.md"
        f.write_text(
            "---\nname: r\nlicense: MIT\ncustom: x\n---\nbody",
            encoding="utf-8",
        )
        doc = load_skill_doc(f)
        assert doc.raw_frontmatter == {"name": "r", "license": "MIT", "custom": "x"}

    def test_name_defaults_to_file_stem_when_absent(self, tmp_path):
        f = tmp_path / "fallback_name.md"
        f.write_text("# No frontmatter here", encoding="utf-8")
        doc = load_skill_doc(f)
        assert doc.name == "fallback_name"
        assert doc.description == ""
        assert doc.category == "custom"

    def test_scalar_tags_coerced_to_list(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("---\nname: t\ntags: single\n---\nbody", encoding="utf-8")
        doc = load_skill_doc(f)
        assert doc.tags == ["single"]

    def test_full_doc_property_returns_content(self, tmp_path):
        f = tmp_path / "fd.md"
        f.write_text("---\nname: fd\n---\nthe body", encoding="utf-8")
        doc = load_skill_doc(f)
        assert doc.full_doc == "the body"

    def test_unexpected_parse_failure_degrades_to_none(self, tmp_path, monkeypatch):
        # If frontmatter parsing blows up unexpectedly, load_skill_doc must
        # catch it and return None — one broken skill never crashes the loader.
        import kohakuterrarium.prompt.skill_loader as sl

        f = tmp_path / "boom.md"
        f.write_text("---\nname: boom\n---\nbody", encoding="utf-8")

        def _explode(text):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(sl, "parse_frontmatter", _explode)
        assert load_skill_doc(f) is None


class TestParseFrontmatterDefensive:
    def test_unexpected_yaml_exception_degrades_to_empty_meta(self, monkeypatch):
        # A non-YAMLError raised by yaml.safe_load (e.g. corrupt unicode)
        # must degrade to "no frontmatter", not bubble up.
        import kohakuterrarium.prompt.skill_loader as sl

        def _explode(text):
            raise RuntimeError("unexpected yaml internal failure")

        monkeypatch.setattr(sl.yaml, "safe_load", _explode)
        meta, content = parse_frontmatter("---\nname: x\n---\nthe body")
        assert meta == {}
        assert content == "the body"


class TestSkillDocMetadataAlias:
    def test_metadata_returns_extra_and_warns_deprecation(self):
        doc = SkillDoc(name="x", description="d", content="c", extra={"k": "v"})
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = doc.metadata
        assert result == {"k": "v"}
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)


class TestLoadSkillDocsFromDir:
    def test_missing_dir_returns_empty_dict(self, tmp_path):
        assert load_skill_docs_from_dir(tmp_path / "absent") == {}

    def test_loads_all_md_keyed_by_skill_name(self, tmp_path):
        (tmp_path / "a.md").write_text("---\nname: alpha\n---\nA", encoding="utf-8")
        (tmp_path / "b.md").write_text("---\nname: beta\n---\nB", encoding="utf-8")
        docs = load_skill_docs_from_dir(tmp_path)
        assert set(docs) == {"alpha", "beta"}
        assert docs["alpha"].content == "A"

    def test_ignores_non_md_files(self, tmp_path):
        (tmp_path / "keep.md").write_text("---\nname: keep\n---\nK", encoding="utf-8")
        (tmp_path / "skip.txt").write_text("ignored", encoding="utf-8")
        docs = load_skill_docs_from_dir(tmp_path)
        assert set(docs) == {"keep"}
