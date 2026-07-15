"""Shared markdown skill/documentation parsing helpers.

Skill documentation loader with optional YAML frontmatter.

The module lives at the package root so prompt, command, and skill-discovery
code can share it without package import cycles.

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

Native fields become :class:`SkillDoc` attributes, recognized external fields
remain in :attr:`SkillDoc.standard`, and unknown fields remain in
:attr:`SkillDoc.extra`. :attr:`SkillDoc.raw_frontmatter` retains the complete
mapping for re-serialization.
"""

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Native fields are exposed directly on SkillDoc.
_NATIVE_KEYS: frozenset[str] = frozenset({"name", "description", "category", "tags"})

# Recognized external fields remain verbatim; ``metadata`` is itself standard.
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
    """Parsed skill documentation with native, standard, and unknown metadata."""

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
        """Return the markdown body."""
        return self.content

    @property
    def metadata(self) -> dict[str, Any]:
        """Return unknown fields through the deprecated pre-standard alias."""
        warnings.warn(
            "SkillDoc.metadata is deprecated; use SkillDoc.extra for unknown "
            "frontmatter keys or SkillDoc.standard for recognized "
            "agentskills.io fields (including the standard 'metadata' key).",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.extra


def _normalize_skill_text(text: str) -> str:
    """Normalize text so BOMs and platform line endings cannot hide frontmatter."""
    if not isinstance(text, str):
        return ""
    if text.startswith("﻿"):
        text = text.lstrip("﻿")
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def read_skill_text(path: "Path | str") -> str | None:
    """Read markdown with fallback decoding and normalized text.

    Returns ``None`` for unreadable files so discovery can skip one bad skill.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read skill file", path=str(p), error=str(exc))
        return None
    # Prefer strict UTF-8; later decoders preserve as much readable text as possible.
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
    """Return raw YAML frontmatter and the remaining markdown body.

    Invalid or absent frontmatter yields an empty mapping.
    """
    if not isinstance(text, str):
        return {}, ""
    # Normalize before delimiter detection so a BOM cannot mask frontmatter.
    text = _normalize_skill_text(text).strip()

    if not text.startswith("---"):
        return {}, text

    end_idx = text.find("---", 3)
    if end_idx == -1:
        return {}, text

    frontmatter_text = text[3:end_idx].strip()
    content = text[end_idx + 3 :].strip()

    # Malformed metadata must not prevent the markdown body from loading.
    parsed: Any
    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as e:
        logger.warning("Failed to parse skill frontmatter", error=str(e))
        parsed = {}
    except Exception as e:  # noqa: BLE001 - parser failures degrade to no metadata
        logger.warning(
            "Unexpected error parsing skill frontmatter",
            error=str(e),
            error_type=type(e).__name__,
        )
        parsed = {}

    if not isinstance(parsed, dict):
        # Frontmatter must be a mapping even when another YAML type is valid.
        parsed = {}

    return parsed, content


def _split_frontmatter(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate recognized external fields from unknown fields.

    Native fields are excluded because the caller consumes them directly.
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
    """Load one skill document, returning ``None`` when it cannot be recovered."""
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
    except Exception as e:  # noqa: BLE001 - one malformed skill must not stop discovery
        logger.warning(
            "Failed to load skill doc; skipping",
            path=str(path),
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


def load_skill_docs_from_dir(directory: Path | str) -> dict[str, SkillDoc]:
    """Load top-level markdown files keyed by their declared skill names."""
    directory = Path(directory)
    docs = {}

    if not directory.exists():
        return docs

    for path in directory.glob("*.md"):
        doc = load_skill_doc(path)
        if doc:
            docs[doc.name] = doc

    return docs
