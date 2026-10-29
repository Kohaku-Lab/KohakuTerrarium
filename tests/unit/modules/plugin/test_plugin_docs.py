"""Unit tests for :mod:`kohakuterrarium.modules.plugin.docs`.

``PluginManager.lookup_doc`` resolves ``<prefix>:<topic>`` through the plugin
that owns the prefix.
"""

import pytest

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.plugin.docs import PluginDocNotFound, split_doc_name
from kohakuterrarium.modules.plugin.manager import PluginManager


class _Docs(BasePlugin):
    def __init__(self, name, prefix, topics, priority=50):
        super().__init__()
        self.name = name
        self.doc_prefix = prefix
        self.priority = priority
        self.topics = topics
        self.seen_context = "unset"

    def doc_topics(self, context):
        return list(self.topics)

    def doc_topic(self, topic, context):
        self.seen_context = context
        return self.topics.get(topic)


class _Broken(BasePlugin):
    name = "broken"
    doc_prefix = "broken"

    def doc_topics(self, context):
        raise RuntimeError("boom")

    def doc_topic(self, topic, context):
        raise RuntimeError("boom")


def _manager(*plugins):
    manager = PluginManager()
    for plugin in plugins:
        manager.register(plugin)
    return manager


class TestSplitDocName:
    def test_prefix_and_topic(self):
        assert split_doc_name("ui:quiz") == ("ui", "quiz")
        assert split_doc_name(" my.pkg-x_1: a b ") == ("my.pkg-x_1", "a b")
        assert split_doc_name("ui:") == ("ui", "")

    def test_names_without_a_valid_prefix(self):
        for name in ("bash", ":quiz", "UI:quiz", "1ui:quiz", "a b:c", "x" * 33 + ":t"):
            assert split_doc_name(name) is None, name


class TestLookupDoc:
    def test_topic_resolves_through_its_owner(self):
        plugin = _Docs("glossary", "gloss", {"creature": "An agent."})
        manager = _manager(plugin)
        context = PluginContext(agent_name="a")
        manager._load_context = context
        assert manager.lookup_doc("gloss:creature") == "An agent."
        assert plugin.seen_context is context

    def test_empty_topic_lists_topics(self):
        manager = _manager(_Docs("glossary", "gloss", {"a": "1", "b": "2"}))
        assert manager.lookup_doc("gloss:") == "# gloss: topics\n\n`gloss:a`, `gloss:b`"
        assert (
            manager.lookup_doc("gloss: ") == "# gloss: topics\n\n`gloss:a`, `gloss:b`"
        )

    def test_unknown_topic_raises_with_the_topics(self):
        manager = _manager(_Docs("glossary", "gloss", {"a": "1"}))
        with pytest.raises(PluginDocNotFound) as caught:
            manager.lookup_doc("gloss:zzz")
        assert (
            str(caught.value) == "No documentation for 'gloss:zzz'. Topics: `gloss:a`"
        )
        assert isinstance(caught.value, NotFoundError)

    def test_names_no_plugin_owns_are_left_to_other_sources(self):
        manager = _manager(_Docs("glossary", "gloss", {"a": "1"}))
        assert manager.lookup_doc("other:a") is None
        assert manager.lookup_doc("bash") is None
        assert PluginManager().lookup_doc("gloss:a") is None

    def test_disabled_plugin_does_not_own_its_prefix(self):
        manager = _manager(_Docs("glossary", "gloss", {"a": "1"}))
        manager.disable("glossary")
        assert manager.lookup_doc("gloss:a") is None
        manager.enable("glossary")
        assert manager.lookup_doc("gloss:a") == "1"

    def test_first_plugin_by_priority_owns_a_shared_prefix(self):
        late = _Docs("late", "gloss", {"a": "late"}, priority=90)
        early = _Docs("early", "gloss", {"a": "early"}, priority=10)
        manager = _manager(late, early)
        assert manager.lookup_doc("gloss:a") == "early"
        assert list(manager.doc_owners()) == ["gloss"]

    def test_invalid_and_empty_prefixes_are_ignored(self):
        manager = _manager(
            _Docs("bad", "Bad Prefix", {"a": "1"}), _Docs("none", "", {"a": "1"})
        )
        assert manager.doc_owners() == {}
        assert manager.lookup_doc("bad:a") is None

    def test_a_raising_plugin_reports_not_found(self):
        manager = _manager(_Broken())
        with pytest.raises(PluginDocNotFound) as caught:
            manager.lookup_doc("broken:x")
        assert str(caught.value) == "No documentation for 'broken:x'. Topics: (none)"
        assert manager.lookup_doc("broken:") == "# broken: topics\n\n(none)"

    def test_base_plugin_has_no_topics(self):
        plugin = BasePlugin()
        assert plugin.doc_prefix == ""
        assert plugin.doc_topics(None) == [] and plugin.doc_topic("x", None) is None
