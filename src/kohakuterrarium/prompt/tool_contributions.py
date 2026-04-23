"""Tool prompt-contribution assembly.

Implements Cluster 5 / E.1 of the extension-point decisions: tools may
return a short ``prompt_contribution()`` string that gets inserted into
the system prompt once per session, between the auto-generated tool
list and the framework hints. Contributions are partitioned into three
ordered buckets (``first`` / ``normal`` / ``last``) and sorted
alphabetically by tool name *within* a bucket so the prefix is stable
across runs (important for prompt caching).

The assembler is transport-agnostic — it only needs a ``Registry`` to
look up every registered tool's live instance. If no tool contributes
anything the function returns an empty string and the aggregator drops
the section entirely.
"""

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Canonical bucket order. Unknown bucket values fall back to "normal"
# with a WARN log so typos surface without breaking aggregation.
_BUCKETS = ("first", "normal", "last")


def _resolve_bucket(tool: BaseTool) -> str:
    """Return the tool's bucket or ``"normal"`` with a warning on typos."""
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
    """Collect ``(bucket, tool_name, contribution)`` triples from a registry.

    Skips tools that don't derive from :class:`BaseTool` (pure
    ``Tool`` protocol implementations without the method) and tools
    whose ``prompt_contribution()`` returns ``None`` / empty.
    """
    if registry is None:
        return []

    triples: list[tuple[str, str, str]] = []
    for name in registry.list_tools():
        tool = registry.get_tool(name) if hasattr(registry, "get_tool") else None
        if tool is None or not isinstance(tool, BaseTool):
            continue

        try:
            contribution = tool.prompt_contribution()
        except Exception as exc:  # pragma: no cover — defensive
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
    """Assemble the ``## Tool guidance`` section from a registry.

    Returns an empty string when no tool contributes prose. The section
    is emitted as one markdown block; each contribution becomes a
    paragraph prefixed with the tool name so the model can tell which
    tool a hint came from.

    Ordering is deterministic — first/normal/last buckets in that
    order, and alphabetical-by-tool-name *within* a bucket — so the
    aggregated prompt prefix stays cache-stable across runs.
    """
    triples = collect_tool_contributions(registry)
    if not triples:
        return ""

    # Partition by bucket, preserve alphabetical sort per bucket.
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
