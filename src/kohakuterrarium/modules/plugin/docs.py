"""Plugin documentation topics that ``info`` resolves as ``<prefix>:<topic>``."""

import re

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DOC_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")
MAX_LISTED_TOPICS = 80


class PluginDocNotFound(NotFoundError):
    """A name uses a plugin's doc prefix but names no topic of that plugin."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def split_doc_name(name: str) -> tuple[str, str] | None:
    """``(prefix, topic)`` when ``name`` has a valid plugin doc prefix."""
    prefix, sep, topic = name.strip().partition(":")
    if not sep or not DOC_PREFIX_RE.match(prefix):
        return None
    return prefix, topic.strip()


class PluginDocsMixin:
    """``PluginManager`` lookup of plugin documentation by doc prefix."""

    def doc_owners(self) -> dict[str, BasePlugin]:
        """Doc prefix -> the applicable plugin that owns it (first by priority)."""
        owners: dict[str, BasePlugin] = {}
        for plugin in self._applicable_plugins():
            prefix = getattr(plugin, "doc_prefix", "") or ""
            if not prefix:
                continue
            if not DOC_PREFIX_RE.match(prefix):
                logger.warning(
                    "Plugin doc_prefix is not valid; ignored",
                    plugin_name=getattr(plugin, "name", "?"),
                    doc_prefix=prefix,
                )
                continue
            if prefix in owners:
                logger.warning(
                    "Plugin doc_prefix already owned; ignored",
                    plugin_name=getattr(plugin, "name", "?"),
                    owner=getattr(owners[prefix], "name", "?"),
                    doc_prefix=prefix,
                )
                continue
            owners[prefix] = plugin
        return owners

    def lookup_doc(self, name: str) -> str | None:
        """Documentation for ``<prefix>:<topic>``; ``None`` if no plugin owns the prefix.

        Raises :class:`PluginDocNotFound` when the prefix is owned but the
        topic is not; its message lists the topics. An empty topic returns
        the topic list.
        """
        parts = split_doc_name(name)
        if parts is None:
            return None
        prefix, topic = parts
        plugin = self.doc_owners().get(prefix)
        if plugin is None:
            return None
        context = self._load_context
        doc = None
        if topic:
            try:
                doc = plugin.doc_topic(topic, context)
            except Exception as e:
                logger.warning(
                    "Plugin doc_topic raised",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(e),
                    exc_info=True,
                )
        if doc:
            return doc
        topics = self._topics_of(plugin)
        listing = (
            ", ".join(f"`{prefix}:{t}`" for t in topics[:MAX_LISTED_TOPICS]) or "(none)"
        )
        if not topic:
            return f"# {prefix}: topics\n\n{listing}"
        raise PluginDocNotFound(
            f"No documentation for '{prefix}:{topic}'. Topics: {listing}"
        )

    def _topics_of(self, plugin: BasePlugin) -> list[str]:
        try:
            return [str(t) for t in plugin.doc_topics(self._load_context) or []]
        except Exception as e:
            logger.warning(
                "Plugin doc_topics raised",
                plugin_name=getattr(plugin, "name", "?"),
                error=str(e),
                exc_info=True,
            )
            return []
