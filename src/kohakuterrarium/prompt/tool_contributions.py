"""Assemble deterministic prompt guidance from registered tool contributions.

Contributions use ``first``, ``normal``, and ``last`` buckets, then sort by tool
name within each bucket to keep prompt prefixes cache-stable.
"""

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Invalid buckets degrade to normal without breaking prompt aggregation.
_BUCKETS = ("first", "normal", "last")


def _resolve_bucket(tool: BaseTool) -> str:
    """Return a valid contribution bucket, warning on invalid values."""
    raw = getattr(tool, "prompt_contribution_bucket", "normal") or "normal"
    if raw not in _BUCKETS:
        logger.warning(
            "Unknown prompt_contribution_bucket, falling back to 'normal'",
            tool_name=getattr(tool, "tool_name", "?"),
            bucket=raw,
        )
        return "normal"
    return raw


def collect_tool_contributions(registry: Registry | None) -> list[tuple[str, str, str]]:
    """Collect non-empty contributions from registered :class:`BaseTool` instances."""
    if registry is None:
        return []

    triples: list[tuple[str, str, str]] = []
    for name in registry.list_tools():
        tool = registry.get_tool(name) if hasattr(registry, "get_tool") else None
        if tool is None or not isinstance(tool, BaseTool):
            continue

        try:
            contribution = tool.prompt_contribution()
        except Exception as exc:  # pragma: no cover - isolate tool extensions
            logger.warning(
                "prompt_contribution raised — skipping",
                tool_name=name,
                error=str(exc),
            )
            continue

        if not contribution:
            continue

        bucket = _resolve_bucket(tool)
        triples.append((bucket, name, contribution.strip()))

    return triples


def build_tool_guidance_section(registry: Registry | None) -> str:
    """Build a cache-stable Markdown guidance section from tool contributions."""
    triples = collect_tool_contributions(registry)
    if not triples:
        return ""

    by_bucket: dict[str, list[tuple[str, str]]] = {b: [] for b in _BUCKETS}
    for bucket, name, contribution in triples:
        by_bucket[bucket].append((name, contribution))
    for entries in by_bucket.values():
        entries.sort(key=lambda pair: pair[0])

    lines = ["## Tool guidance", ""]
    for bucket in _BUCKETS:
        for name, contribution in by_bucket[bucket]:
            lines.append(f"- **{name}**: {contribution}")
    return "\n".join(lines)
