"""Shared markdown skill/documentation parsing helpers.

This module deliberately lives at the package root instead of under
``prompt/`` so builtin-skill readers, prompt aggregation, commands, and
procedural-skill discovery can all depend on it without creating any
package-level import cycles.

Skill/Documentation loader with frontmatter YAML support.

Loads markdown files with optional YAML frontmatter for metadata.

Format:
```
---
name: tool_name
description: One-line description
category: builtin
---

# Full Documentation

...markdown content...
```

Recognized frontmatter keys are split across three buckets:

1. **Native KT fields** (`name`, `description`, `category`, `tags`) live as
   first-class attributes on :class:`SkillDoc`.
2. **agentskills.io / Claude Code standard fields** — ``license``,
   ``compatibility``, ``allowed-tools``, ``disable-model-invocation``,
   ``when_to_use``, ``paths``, ``agent``, ``arguments``, ``user-invocable``,
   ``model``, ``effort``, ``hooks``, plus the standard's user-extension
   ``metadata`` map — are preserved verbatim in :attr:`SkillDoc.standard`.
3. **Anything else** — completely unknown keys — lands in
   :attr:`SkillDoc.extra` so future tooling can still round-trip them.

The full parsed YAML dict is also stashed under
:attr:`SkillDoc.raw_frontmatter` for re-serialization use cases.
"""

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Keys promoted to first-class attributes on SkillDoc.
_NATIVE_KEYS: frozenset[str] = frozenset({"name", "description", "category", "tags"})

# Keys defined by the agentskills.io spec and/or Claude Code frontmatter
# extensions. Kept verbatim in SkillDoc.standard. Unknown keys go into
# SkillDoc.extra instead.
#
# Note: the agentskills.io spec itself defines ``metadata`` as a user
# extension dict. We keep that key in ``standard`` rather than ``extra``.
_STANDARD_KEYS: frozenset[str] = frozenset(
    {
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
        "disable-model-invocation",
        "when_to_use",
        "paths",
        "agent",
        "arguments",
        "argument-hint",
        "user-invocable",
        "model",
        "effort",
        "hooks",
        "context",
        "shell",
    }
)


@dataclass
class SkillDoc:
    """Parsed skill/tool documentation.

    Field layout:

    - ``name`` / ``description`` / ``content`` — required-ish identifiers plus
      the markdown body.
    - ``category`` — KT-specific bucket (``builtin`` / ``custom`` / …).
    - ``tags`` — free-form tag list (rendered by info output).
    - ``standard`` — recognized agentskills.io / Claude Code frontmatter
      fields (see ``_STANDARD_KEYS``). Kept as parsed YAML values
      (strings, lists, dicts) without normalization.
    - ``extra`` — YAML keys that are neither native nor standard. Preserved
      so future tooling can re-emit SKILL.md round-trippable.
    - ``raw_frontmatter`` — the full parsed YAML dict (useful when a caller
      wants to re-serialise without losing any ordering or provenance).
    """

    name: str
    description: str
    content: str
    category: str = "custom"
    tags: list[str] = field(default_factory=list)
    standard: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)

    @property
    def full_doc(self) -> str:
        """Get full documentation (frontmatter + content)."""
        return self.content

    @property
    def metadata(self) -> dict[str, Any]:
        """Deprecated alias for :attr:`extra`.

        Historically ``SkillDoc`` used a single ``metadata`` dict as the
        catch-all for every non-native frontmatter key. That name collides
        with the agentskills.io spec's own ``metadata`` user-extension
        field, so the catch-all moved to :attr:`extra` and recognized spec
        fields moved to :attr:`standard`. This alias preserves source
        compatibility for one minor version (per cluster 7.2) and will be
        removed afterwards.
        """
        warnings.warn(
            "SkillDoc.metadata is deprecated; use SkillDoc.extra for unknown "
            "frontmatter keys or SkillDoc.standard for recognized "
            "agentskills.io fields (including the standard 'metadata' key).",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.extra


def _normalize_skill_text(text: str) -> str:
    """Strip a leading BOM and normalise line endings.

    Markdown editors on Windows / various export pipelines like to
    prepend a UTF-8 BOM (``\\ufeff``); the YAML frontmatter detection
    further down checks for a literal ``---`` prefix and would silently
    skip every BOM'd file. CR / CRLF line endings are likewise folded
    to ``\\n`` so ``yaml.safe_load`` doesn't choke on stray CRs.
    """
    if not isinstance(text, str):
        return ""
    if text.startswith("﻿"):
        text = text.lstrip("﻿")
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def read_skill_text(path: "Path | str") -> str | None:
    """Read a SKILL.md / *.md file with permissive decoding.

    Tries UTF-8 first (the documented format), then falls back to UTF-8
    with replacement, then latin-1. Returns ``None`` when the file
    can't be read at all (missing, permission denied, …) — callers warn
    and skip rather than crash. BOM and CR characters are stripped on
    the way out so downstream YAML / markdown parsing stays robust.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read skill file", path=str(p), error=str(exc))
        return None
    # Try strict UTF-8 first. Fall back through utf-8-sig (eats BOM
    # too), then replacement-mode UTF-8, then latin-1 as a last
    # resort. None of these can raise — the loader will log a warning
    # and the markdown body will be best-effort recovered.
    for encoding, errors in (
        ("utf-8", "strict"),
        ("utf-8-sig", "strict"),
        ("utf-8", "replace"),
        ("latin-1", "replace"),
    ):
        try:
            text = raw.decode(encoding, errors)
        except UnicodeDecodeError:
            continue
        if encoding != "utf-8" or errors != "strict":
            logger.warning(
                "Skill file is not clean UTF-8 — recovered with fallback decoding",
                path=str(p),
                encoding=encoding,
                errors=errors,
            )
        return _normalize_skill_text(text)
    logger.warning("Failed to decode skill file", path=str(p))
    return None


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    Parse YAML frontmatter from markdown text.

    Args:
        text: Markdown text potentially with frontmatter

    Returns:
        (metadata_dict, content_without_frontmatter). The dict is the raw
        YAML mapping — every parsed key is included verbatim so callers
        can decide how to bucket them. Returns ``({}, text)`` when the
        text has no (valid) frontmatter.
    """
    if not isinstance(text, str):
        return {}, ""
    # Strip BOM + normalise newlines BEFORE the ``---`` prefix check
    # so a BOM'd file doesn't fall straight through to the no-fm path.
    text = _normalize_skill_text(text).strip()

    # Check for frontmatter delimiter
    if not text.startswith("---"):
        return {}, text

    # Find end of frontmatter
    end_idx = text.find("---", 3)
    if end_idx == -1:
        return {}, text

    # Extract frontmatter YAML
    frontmatter_text = text[3:end_idx].strip()
    content = text[end_idx + 3 :].strip()

    # Parse YAML — both YAMLError (malformed) and any unexpected
    # exception (e.g. corrupt unicode that survived decoding) must
    # degrade to "no frontmatter" rather than bubbling up.
    parsed: Any
    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as e:
        logger.warning("Failed to parse skill frontmatter", error=str(e))
        parsed = {}
    except Exception as e:  # noqa: BLE001 — defensive
        logger.warning(
            "Unexpected error parsing skill frontmatter",
            error=str(e),
            error_type=type(e).__name__,
        )
        parsed = {}

    if not isinstance(parsed, dict):
        # YAML "front matter" that parses to e.g. a string or list is
        # technically valid YAML but useless to the loader — treat as
        # absent rather than blowing up downstream.
        parsed = {}

    return parsed, content


def _split_frontmatter(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Partition a raw frontmatter dict into (standard, extra) buckets.

    Native keys (``name`` / ``description`` / ``category`` / ``tags``) are
    already consumed by the caller and intentionally excluded from both
    buckets.
    """
    standard: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _NATIVE_KEYS:
            continue
        if key in _STANDARD_KEYS:
            standard[key] = value
        else:
            extra[key] = value
    return standard, extra


def load_skill_doc(path: Path | str) -> SkillDoc | None:
    """
    Load a skill/tool documentation file.

    Returns ``None`` (with a warning) for any failure — bad encoding,
    BOM-only file, malformed frontmatter, OS error — so a single
    broken skill never crashes the agent loader.

    Args:
        path: Path to markdown file

    Returns:
        SkillDoc or None if the file is missing or unreadable.
    """
    path = Path(path)

    if not path.exists():
        return None

    text = read_skill_text(path)
    if text is None:
        return None

    try:
        raw, content = parse_frontmatter(text)
        standard, extra = _split_frontmatter(raw)

        tags_value = raw.get("tags", [])
        if not isinstance(tags_value, list):
            tags_value = [tags_value] if tags_value else []

        return SkillDoc(
            name=str(raw.get("name") or path.stem),
            description=str(raw.get("description") or ""),
            content=content,
            category=str(raw.get("category") or "custom"),
            tags=[str(t) for t in tags_value if t is not None],
            standard=standard,
            extra=extra,
            raw_frontmatter=dict(raw),
        )
    except Exception as e:  # noqa: BLE001 — last line of defense
        logger.warning(
            "Failed to load skill doc; skipping",
            path=str(path),
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


def load_skill_docs_from_dir(directory: Path | str) -> dict[str, SkillDoc]:
    """
    Load all skill docs from a directory.

    Args:
        directory: Path to directory containing .md files

    Returns:
        Dict mapping skill name to SkillDoc
    """
    directory = Path(directory)
    docs = {}

    if not directory.exists():
        return docs

    for path in directory.glob("*.md"):
        doc = load_skill_doc(path)
        if doc:
            docs[doc.name] = doc

    return docs
