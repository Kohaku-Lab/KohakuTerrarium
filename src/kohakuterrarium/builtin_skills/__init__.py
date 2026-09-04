"""Load packaged reference documentation for built-in tools and sub-agents.

Every reference file is split at the ``## Reference`` heading into two tiers:

- **usage** — the body above it. Argument table, behavior, limits. Small enough
  to inline into a system prompt when a tool asks for ``doc_mode: full``.
- **reference** — the body from that heading on. Output formats, edge cases,
  worked failures. Reachable only through the ``info`` tool.

Files without the heading are all usage; the split is opt-in per file.
"""

from pathlib import Path

from kohakuterrarium.skill_docs import load_skill_doc
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent

REFERENCE_HEADING = "## Reference"

_TIERS = ("all", "usage", "reference")


def split_doc_tiers(body: str) -> tuple[str, str]:
    """Split a reference body into ``(usage, reference)`` at the tier heading."""
    if not body:
        return "", ""
    marker = f"\n{REFERENCE_HEADING}"
    if body.startswith(REFERENCE_HEADING):
        return "", body.strip()
    idx = body.find(marker)
    if idx < 0:
        return body.strip(), ""
    return body[:idx].strip(), body[idx + 1 :].strip()


def _select_tier(body: str | None, tier: str) -> str | None:
    """Return the requested tier of a reference body."""
    if body is None:
        return None
    if tier not in _TIERS:
        logger.warning("Unknown documentation tier requested", tier=tier)
        tier = "all"
    if tier == "all":
        return body
    usage, reference = split_doc_tiers(body)
    return usage if tier == "usage" else reference


def read_skill_body(path: Path) -> str | None:
    """Return a reference file's body, falling back to raw text on parse failure."""
    if not path.exists():
        return None

    doc = load_skill_doc(path)
    if doc is not None:
        if not doc.content:
            logger.debug("Skill file has empty body", path=str(path))
        return doc.content

    # Reference lookup should remain usable when frontmatter is malformed.
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to read skill file", path=str(path), error=str(exc))
        return None
    logger.debug("Falling back to raw skill content", path=str(path))
    return raw


def get_builtin_tool_doc(name: str, *, tier: str = "all") -> str | None:
    """Return a built-in tool's documentation body by name."""
    doc_path = BUILTIN_SKILLS_DIR / "tools" / f"{name}.md"
    return _select_tier(read_skill_body(doc_path), tier)


def get_builtin_subagent_doc(name: str, *, tier: str = "all") -> str | None:
    """Return a built-in sub-agent's documentation body by name."""
    doc_path = BUILTIN_SKILLS_DIR / "subagents" / f"{name}.md"
    return _select_tier(read_skill_body(doc_path), tier)


def list_builtin_tool_docs() -> list[str]:
    """List built-in tool names with packaged documentation."""
    tools_dir = BUILTIN_SKILLS_DIR / "tools"
    if not tools_dir.exists():
        return []
    return [p.stem for p in tools_dir.glob("*.md")]


def list_builtin_subagent_docs() -> list[str]:
    """List built-in sub-agent names with packaged documentation."""
    subagents_dir = BUILTIN_SKILLS_DIR / "subagents"
    if not subagents_dir.exists():
        return []
    return [p.stem for p in subagents_dir.glob("*.md")]


def get_all_tool_docs(
    tool_names: list[str] | None = None, *, tier: str = "all"
) -> dict[str, str]:
    """Return documentation bodies for selected or all built-in tools."""
    if tool_names is None:
        tool_names = list_builtin_tool_docs()

    docs = {}
    for name in tool_names:
        doc = get_builtin_tool_doc(name, tier=tier)
        if doc:
            docs[name] = doc
    return docs


def get_all_subagent_docs(
    subagent_names: list[str] | None = None, *, tier: str = "all"
) -> dict[str, str]:
    """Return documentation bodies for selected or all built-in sub-agents."""
    if subagent_names is None:
        subagent_names = list_builtin_subagent_docs()

    docs = {}
    for name in subagent_names:
        doc = get_builtin_subagent_doc(name, tier=tier)
        if doc:
            docs[name] = doc
    return docs
