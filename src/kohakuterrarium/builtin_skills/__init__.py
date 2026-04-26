"""
Builtin skills - default documentation for builtin tools and subagents.

These files are packaged with the library and serve as default documentation.
Users can override them by placing files in their agent's prompts/tools/ folder.
"""

from pathlib import Path

from kohakuterrarium.skill_docs import load_skill_doc
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Path to builtin skills directory
BUILTIN_SKILLS_DIR = Path(__file__).parent


def read_skill_body(path: Path) -> str | None:
    """
    Read a skill markdown file and return only the body (frontmatter stripped).

    If the file has no frontmatter, the whole file is treated as content.
    If the file is malformed or fails to parse, falls back to the raw text
    so info output never breaks on a bad YAML block.

    Args:
        path: Path to a markdown skill file.

    Returns:
        The post-frontmatter body, or ``None`` if the file does not exist.
    """
    if not path.exists():
        return None

    doc = load_skill_doc(path)
    if doc is not None:
        if not doc.content:
            logger.debug("Skill file has empty body", path=str(path))
        return doc.content

    # load_skill_doc failed (logged inside). Degrade gracefully by
    # returning the raw text so the controller still gets something.
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to read skill file", path=str(path), error=str(exc))
        return None
    logger.debug("Falling back to raw skill content", path=str(path))
    return raw


def get_builtin_tool_doc(name: str) -> str | None:
    """
    Get builtin tool documentation by name.

    The returned string is the documentation body only - any YAML frontmatter
    is stripped. Callers that need the frontmatter metadata should use
    ``skill_docs.load_skill_doc`` directly.

    Args:
        name: Tool name (e.g., "bash", "read")

    Returns:
        Documentation body or None if not found
    """
    doc_path = BUILTIN_SKILLS_DIR / "tools" / f"{name}.md"
    return read_skill_body(doc_path)


def get_builtin_subagent_doc(name: str) -> str | None:
    """
    Get builtin subagent documentation by name.

    The returned string is the documentation body only - any YAML frontmatter
    is stripped. Callers that need the frontmatter metadata should use
    ``skill_docs.load_skill_doc`` directly.

    Args:
        name: Subagent name

    Returns:
        Documentation body or None if not found
    """
    doc_path = BUILTIN_SKILLS_DIR / "subagents" / f"{name}.md"
    return read_skill_body(doc_path)


def list_builtin_tool_docs() -> list[str]:
    """List all builtin tool names that have documentation."""
    tools_dir = BUILTIN_SKILLS_DIR / "tools"
    if not tools_dir.exists():
        return []
    return [p.stem for p in tools_dir.glob("*.md")]


def list_builtin_subagent_docs() -> list[str]:
    """List all builtin subagent names that have documentation."""
    subagents_dir = BUILTIN_SKILLS_DIR / "subagents"
    if not subagents_dir.exists():
        return []
    return [p.stem for p in subagents_dir.glob("*.md")]


def get_all_tool_docs(tool_names: list[str] | None = None) -> dict[str, str]:
    """
    Get documentation for multiple tools.

    Args:
        tool_names: List of tool names, or None for all builtin tools

    Returns:
        Dict of tool_name -> documentation
    """
    if tool_names is None:
        tool_names = list_builtin_tool_docs()

    docs = {}
    for name in tool_names:
        doc = get_builtin_tool_doc(name)
        if doc:
            docs[name] = doc
    return docs


def get_all_subagent_docs(subagent_names: list[str] | None = None) -> dict[str, str]:
    """
    Get documentation for multiple subagents.

    Args:
        subagent_names: List of subagent names, or None for all builtin

    Returns:
        Dict of subagent_name -> documentation
    """
    if subagent_names is None:
        subagent_names = list_builtin_subagent_docs()

    docs = {}
    for name in subagent_names:
        doc = get_builtin_subagent_doc(name)
        if doc:
            docs[name] = doc
    return docs
