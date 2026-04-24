"""Hygiene tests for ``prompt/skill_loader.py`` (items H.7 + H.8).

Covers:

* Unknown YAML keys survive parse round-trips verbatim (H.7).
* Recognized agentskills.io frontmatter fields land in ``SkillDoc.standard``
  rather than silently collapsing into a single ``metadata`` sink (H.7).
* ``SkillDoc.raw_frontmatter`` preserves every parsed key for potential
  re-serialisation (H.7).
* ``SkillDoc.tags`` round-trips YAML lists and is surfaced in the
  ``##info##`` command output so the field is actually consumed (H.8).
* ``SkillDoc.metadata`` is a deprecated alias that still returns the
  catch-all bucket but emits ``DeprecationWarning``.
* Backward-compat constructor signature still accepts only the required
  positional fields and supplies sensible defaults.
"""

import tempfile
import warnings
from pathlib import Path
from unittest.mock import MagicMock

from kohakuterrarium.commands.read import InfoCommand
from kohakuterrarium.prompt.skill_loader import (
    SkillDoc,
    load_skill_doc,
    parse_frontmatter,
)

# ---------------------------------------------------------------------------
# parse_frontmatter returns the raw YAML dict verbatim
# ---------------------------------------------------------------------------


def test_parse_frontmatter_preserves_all_keys():
    text = """---
name: my-skill
description: Demo
license: internal
compatibility: kt>=0.1
allowed-tools: [read, grep]
custom-field: something-unknown
metadata:
  author: tester
  version: "1.0"
---

body content here.
"""
    raw, content = parse_frontmatter(text)
    assert raw["name"] == "my-skill"
    assert raw["description"] == "Demo"
    assert raw["license"] == "internal"
    assert raw["compatibility"] == "kt>=0.1"
    assert raw["allowed-tools"] == ["read", "grep"]
    assert raw["custom-field"] == "something-unknown"
    assert raw["metadata"] == {"author": "tester", "version": "1.0"}
    assert content == "body content here."


# ---------------------------------------------------------------------------
# SkillDoc field routing: standard vs extra vs raw_frontmatter
# ---------------------------------------------------------------------------


def _write_temp_skill(text: str) -> Path:
    """Write ``text`` to a temporary .md file and return its path."""
    fh = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    fh.write(text)
    fh.close()
    return Path(fh.name)


def test_agentskills_fields_land_in_standard():
    path = _write_temp_skill("""---
name: spec-aware
description: Uses agentskills.io fields
license: CC-BY-4.0
compatibility: kt>=0.2
allowed-tools: read grep
disable-model-invocation: true
when_to_use: when the planets align
paths: ["*.py", "src/**"]
---

Body.
""")
    try:
        doc = load_skill_doc(path)
    finally:
        path.unlink()

    assert doc is not None
    assert doc.name == "spec-aware"
    assert doc.standard["license"] == "CC-BY-4.0"
    assert doc.standard["compatibility"] == "kt>=0.2"
    assert doc.standard["allowed-tools"] == "read grep"
    assert doc.standard["disable-model-invocation"] is True
    assert doc.standard["when_to_use"] == "when the planets align"
    assert doc.standard["paths"] == ["*.py", "src/**"]
    # None of the agentskills fields should leak into ``extra``.
    assert doc.extra == {}


def test_unknown_keys_land_in_extra():
    path = _write_temp_skill("""---
name: speculative
description: Uses fields KT has never heard of
x-future-knob: "keep-as-string"
weird-key: {nested: 1}
totally-made-up: 42
---

Body.
""")
    try:
        doc = load_skill_doc(path)
    finally:
        path.unlink()

    assert doc is not None
    assert doc.extra["x-future-knob"] == "keep-as-string"
    assert doc.extra["weird-key"] == {"nested": 1}
    assert doc.extra["totally-made-up"] == 42
    # Standard bucket stays empty because none of the keys are recognized.
    assert doc.standard == {}


def test_raw_frontmatter_contains_every_parsed_key():
    path = _write_temp_skill("""---
name: kitchen-sink
description: Lots of fields
category: builtin
tags: [a, b]
license: internal
compatibility: kt>=0.3
x-unknown: keep-me
---

Body.
""")
    try:
        doc = load_skill_doc(path)
    finally:
        path.unlink()

    assert doc is not None
    raw = doc.raw_frontmatter
    expected_keys = {
        "name",
        "description",
        "category",
        "tags",
        "license",
        "compatibility",
        "x-unknown",
    }
    assert expected_keys <= set(raw.keys())
    # Values round-trip verbatim.
    assert raw["tags"] == ["a", "b"]
    assert raw["x-unknown"] == "keep-me"


def test_metadata_yaml_key_goes_to_standard_not_extra():
    """The agentskills.io ``metadata`` YAML key is a first-class spec field.

    KT's historical in-memory ``SkillDoc.metadata`` catch-all collided with
    that keyspace; we now keep the spec key in ``standard``.
    """
    path = _write_temp_skill("""---
name: with-metadata
description: Has a YAML metadata block
metadata:
  owner: kohaku
  license-ref: LICENSE.txt
---

Body.
""")
    try:
        doc = load_skill_doc(path)
    finally:
        path.unlink()

    assert doc is not None
    assert doc.standard["metadata"] == {
        "owner": "kohaku",
        "license-ref": "LICENSE.txt",
    }
    assert "metadata" not in doc.extra


# ---------------------------------------------------------------------------
# Tags round-trip
# ---------------------------------------------------------------------------


def test_tags_round_trip_from_yaml_list():
    path = _write_temp_skill("""---
name: tagged
description: Has tags
tags:
  - alpha
  - beta
  - gamma
---

Body.
""")
    try:
        doc = load_skill_doc(path)
    finally:
        path.unlink()

    assert doc is not None
    assert doc.tags == ["alpha", "beta", "gamma"]


def test_tags_scalar_coerced_to_list():
    """A bare ``tags: foo`` value should still produce ``['foo']``."""
    path = _write_temp_skill("""---
name: scalar-tag
description: Scalar tag instead of list
tags: solo
---

Body.
""")
    try:
        doc = load_skill_doc(path)
    finally:
        path.unlink()

    assert doc is not None
    assert doc.tags == ["solo"]


# ---------------------------------------------------------------------------
# Deprecated metadata alias
# ---------------------------------------------------------------------------


def test_metadata_alias_returns_extra_with_deprecation_warning():
    doc = SkillDoc(
        name="x",
        description="y",
        content="z",
        extra={"leftover": 1},
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = doc.metadata
    assert result == {"leftover": 1}
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert any("SkillDoc.metadata is deprecated" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# Backward-compat constructor
# ---------------------------------------------------------------------------


def test_minimal_constructor_has_sensible_defaults():
    doc = SkillDoc(name="x", description="y", content="z")
    assert doc.category == "custom"
    assert doc.tags == []
    assert doc.standard == {}
    assert doc.extra == {}
    assert doc.raw_frontmatter == {}


# ---------------------------------------------------------------------------
# InfoCommand surfaces tags
# ---------------------------------------------------------------------------


class TestInfoCommandSurfacesTags:
    """``##info##`` should render ``Tags: ...`` when a skill declares any."""

    async def test_info_renders_tags_for_builtin_tool(self):
        cmd = InfoCommand()
        # ``bash.md`` ships with tags: [shell, command, system].
        context = MagicMock(spec=[])
        result = await cmd.execute("bash", context)
        assert result.error is None
        assert result.content is not None
        # First line should surface the tags.
        assert result.content.startswith("Tags: ")
        assert "shell" in result.content
        assert "command" in result.content
        assert "system" in result.content

    async def test_info_omits_tag_line_when_skill_has_no_tags(self, tmp_path: Path):
        cmd = InfoCommand()

        tagless = tmp_path / "prompts" / "tools" / "silent.md"
        tagless.parent.mkdir(parents=True, exist_ok=True)
        tagless.write_text(
            """---
name: silent
description: No tags here
---

Some documentation body.
""",
            encoding="utf-8",
        )

        context = MagicMock()
        context.agent_path = tmp_path
        result = await cmd.execute("silent", context)
        assert result.error is None
        assert result.content is not None
        assert "Tags:" not in result.content
        assert "Some documentation body." in result.content

    async def test_info_renders_tags_for_agent_override(self, tmp_path: Path):
        cmd = InfoCommand()

        override = tmp_path / "prompts" / "tools" / "custom.md"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(
            """---
name: custom
description: A user override
tags: [alpha, beta]
---

Overridden body.
""",
            encoding="utf-8",
        )

        context = MagicMock()
        context.agent_path = tmp_path
        result = await cmd.execute("custom", context)
        assert result.error is None
        assert "Tags: alpha, beta" in (result.content or "")
        assert "Overridden body." in (result.content or "")
