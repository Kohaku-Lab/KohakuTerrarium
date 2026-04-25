"""Skill loader robustness — never crash on malformed SKILL.md.

The framework has had multiple reports of ``kt run`` / ``kt serve``
crashing because a single skill file in the user's workspace had a
BOM, mojibake from a Windows editor, malformed YAML frontmatter, or
other oddities. The contract is: log a warning, skip the file, keep
going. These tests pin that behaviour down so it doesn't regress.
"""

from pathlib import Path

from kohakuterrarium.prompt.skill_loader import (
    _normalize_skill_text,
    load_skill_doc,
    parse_frontmatter,
    read_skill_text,
)
from kohakuterrarium.skills.discovery import (
    _scan_root,
    discover_skills,
    load_skill_from_path,
)

# ───────────────────────── Encoding & BOM hygiene ─────────────────────────


def test_normalize_strips_leading_bom():
    text = "﻿---\nname: foo\n---\nbody"
    out = _normalize_skill_text(text)
    assert out.startswith("---")


def test_normalize_collapses_crlf_to_lf():
    text = "---\r\nname: foo\r\n---\r\nbody"
    out = _normalize_skill_text(text)
    assert "\r" not in out


def test_parse_frontmatter_handles_bom_prefix():
    text = "﻿---\nname: foo\ndescription: bar\n---\nbody"
    fm, body = parse_frontmatter(text)
    assert fm == {"name": "foo", "description": "bar"}
    assert body == "body"


def test_parse_frontmatter_returns_empty_on_yaml_error():
    # Deliberately invalid YAML — unterminated quote
    text = '---\nname: "unterminated\n---\nbody'
    fm, body = parse_frontmatter(text)
    assert fm == {}
    # Body still recovers (the bytes after the closing ---) so the
    # markdown content isn't lost. The regression we're guarding
    # against is parse_frontmatter raising on broken YAML.
    assert body == "body"


def test_parse_frontmatter_returns_empty_when_yaml_is_a_string():
    # Frontmatter that parses as a YAML scalar, not a dict.
    text = "---\nplain string\n---\nbody"
    fm, body = parse_frontmatter(text)
    assert fm == {}
    assert body == "body"


def test_parse_frontmatter_handles_non_string_input():
    fm, body = parse_frontmatter(None)  # type: ignore[arg-type]
    assert fm == {}
    assert body == ""


# ───────────────────────────── read_skill_text ────────────────────────────


def test_read_skill_text_decodes_utf8_with_bom(tmp_path: Path):
    p = tmp_path / "SKILL.md"
    p.write_bytes(b"\xef\xbb\xbf---\nname: foo\n---\nbody\n")
    text = read_skill_text(p)
    assert text is not None
    assert text.startswith("---")


def test_read_skill_text_falls_back_to_replacement_on_invalid_utf8(tmp_path: Path):
    # Lone byte 0x80 isn't valid UTF-8; loader should still recover.
    p = tmp_path / "SKILL.md"
    p.write_bytes(b"---\nname: \x80broken\n---\nbody")
    text = read_skill_text(p)
    assert text is not None
    # Decoded with replacement → the \x80 lands as a replacement char
    # but the rest of the structure survives so the parser can proceed.
    assert "name:" in text


def test_read_skill_text_returns_none_for_missing_file(tmp_path: Path):
    text = read_skill_text(tmp_path / "no-such-file.md")
    assert text is None


# ─────────────────────────── load_skill_doc / from_path ───────────────────


def test_load_skill_doc_returns_none_for_missing(tmp_path: Path):
    assert load_skill_doc(tmp_path / "ghost.md") is None


def test_load_skill_doc_recovers_from_bom_and_returns_doc(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_bytes(b"\xef\xbb\xbf---\nname: hello\ndescription: hi\n---\nbody\n")
    doc = load_skill_doc(p)
    assert doc is not None
    assert doc.name == "hello"
    assert doc.description == "hi"
    assert doc.content == "body"


def test_load_skill_doc_recovers_from_malformed_yaml(tmp_path: Path):
    """A skill with bad frontmatter should still produce a SkillDoc — name
    falls back to the file stem and content is whatever's after the
    delimiter."""
    p = tmp_path / "bad.md"
    p.write_text(
        "---\nname: 'unterminated\n---\nbody after broken fm",
        encoding="utf-8",
    )
    doc = load_skill_doc(p)
    # Loader degrades to "no frontmatter" — that's still a usable doc
    # with the file stem as name.
    assert doc is not None
    assert doc.name == "bad"


def test_load_skill_from_path_returns_none_on_undecodable_garbage(tmp_path: Path):
    """A binary blob masquerading as SKILL.md must not crash the loader.

    With permissive decoding (replacement-mode) we still get a string
    back, but parsing a blob as markdown produces a degenerate skill —
    we accept that as long as the loader doesn't raise.
    """
    p = tmp_path / "SKILL.md"
    p.write_bytes(b"\x00\x01\x02\x03\x04binary blob")
    skill = load_skill_from_path(p, origin="user", default_name="weird")
    # Either None or a fallback skill is acceptable; the only thing we
    # absolutely refuse is raising.
    if skill is not None:
        assert skill.name == "weird"


def test_load_skill_from_path_handles_yaml_with_dict_paths(tmp_path: Path):
    """If a user writes ``paths: {a: b}`` (dict), _as_string_list should
    return [] without raising."""
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: x\npaths:\n  a: b\n---\nbody",
        encoding="utf-8",
    )
    skill = load_skill_from_path(p, origin="user", default_name="x")
    assert skill is not None
    assert skill.paths == []  # dict frontmatter for paths is ignored


# ─────────────────────────────── _scan_root ──────────────────────────────


def test_scan_root_skips_one_bad_file_among_many(tmp_path: Path):
    """A single corrupt SKILL.md must not block the rest of the scan."""
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "SKILL.md").write_text(
        "---\nname: good\ndescription: ok\n---\nbody",
        encoding="utf-8",
    )
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_bytes(b"\x00\x01\x02 not really markdown")

    skills = _scan_root(tmp_path, origin="user")
    names = {s.name for s in skills}
    assert "good" in names
    # The bad one might still appear (with replacement decoding) under
    # the directory name — but the scan must not have raised.


def test_discover_skills_does_not_crash_on_unicode_in_path(tmp_path: Path):
    """End-to-end: discover_skills survives nonsensical / unreadable
    project skill roots without raising."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".kt").mkdir()
    (cwd / ".kt" / "skills").mkdir()
    skill_dir = cwd / ".kt" / "skills" / "demo"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_bytes(b"\xef\xbb\xbf---\nname: demo\n---\nbody")

    home = tmp_path / "home"
    home.mkdir()

    skills = discover_skills(cwd=cwd, home=home)
    names = {s.name for s in skills}
    assert "demo" in names
