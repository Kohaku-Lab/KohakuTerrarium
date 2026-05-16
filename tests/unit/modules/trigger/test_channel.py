"""Unit tests for :mod:`kohakuterrarium.modules.trigger.channel`.

Behavior-first: the channel trigger wakes on a delivered message,
applies sender filtering / self-ignore, renders prompt templates with
message metadata, and cleans up its subscription on stop.
"""

import asyncio


from kohakuterrarium.core.channel import ChannelMessage, ChannelRegistry
from kohakuterrarium.core.events import EventType
from kohakuterrarium.modules.trigger.channel import ChannelTrigger


def _broadcast_registry():
    """A registry whose channels are broadcast (AgentChannel)."""
    return ChannelRegistry()


async def _deliver(registry, name, *messages):
    """Send each ChannelMessage kwargs dict to a broadcast channel.

    Yields control first so a concurrently-awaited ChannelTrigger has a
    chance to create its subscription before the broadcast lands.
    """
    await asyncio.sleep(0.05)
    channel = registry.get_or_create(name, channel_type="broadcast")
    for msg_kwargs in messages:
        await channel.send(ChannelMessage(**msg_kwargs))


async def _wait_with_delivery(trigger, registry, name, *messages):
    """Await trigger.wait_for_trigger while delivering messages alongside."""
    deliverer = asyncio.create_task(_deliver(registry, name, *messages))
    try:
        return await asyncio.wait_for(trigger.wait_for_trigger(), timeout=3)
    finally:
        await deliverer


class TestFireOnMessage:
    async def test_wakes_on_delivered_message(self):
        reg = _broadcast_registry()
        t = ChannelTrigger(channel_name="inbox", registry=reg)
        await t.start()
        ev = await _wait_with_delivery(
            t, reg, "inbox", {"sender": "alice", "content": "hi there"}
        )
        assert ev is not None
        assert ev.type == EventType.CHANNEL_MESSAGE
        assert ev.context["sender"] == "alice"
        assert ev.context["channel"] == "inbox"
        # No prompt template → event content is the raw message content.
        assert ev.content == "hi there"

    async def test_wait_returns_none_when_not_running(self):
        reg = _broadcast_registry()
        t = ChannelTrigger(channel_name="inbox", registry=reg)
        assert await t.wait_for_trigger() is None


class TestFiltering:
    async def test_filter_sender_whitelists(self):
        reg = _broadcast_registry()
        t = ChannelTrigger(channel_name="inbox", registry=reg, filter_sender="bob")
        await t.start()
        # alice is filtered out; bob passes.
        ev = await _wait_with_delivery(
            t,
            reg,
            "inbox",
            {"sender": "alice", "content": "ignored"},
            {"sender": "bob", "content": "kept"},
        )
        assert ev.context["sender"] == "bob"
        assert ev.content == "kept"

    async def test_ignore_sender_skips_self_messages(self):
        reg = _broadcast_registry()
        t = ChannelTrigger(channel_name="inbox", registry=reg, ignore_sender="me")
        await t.start()
        ev = await _wait_with_delivery(
            t,
            reg,
            "inbox",
            {"sender": "me", "content": "self-talk"},
            {"sender": "other", "content": "real"},
        )
        assert ev.context["sender"] == "other"

    async def test_ignore_sender_id_skips_by_stable_identity(self):
        reg = _broadcast_registry()
        t = ChannelTrigger(channel_name="inbox", registry=reg, ignore_sender_id="cid-1")
        await t.start()
        # Same display name, but sender_id distinguishes them.
        ev = await _wait_with_delivery(
            t,
            reg,
            "inbox",
            {"sender": "dup", "content": "from self", "sender_id": "cid-1"},
            {"sender": "dup", "content": "from peer", "sender_id": "cid-2"},
        )
        assert ev.content == "from peer"


class TestPromptRendering:
    async def test_template_placeholders_substituted(self):
        reg = _broadcast_registry()
        t = ChannelTrigger(
            channel_name="inbox",
            registry=reg,
            prompt="[{channel}] {sender}: {content}",
        )
        await t.start()
        ev = await _wait_with_delivery(
            t, reg, "inbox", {"sender": "carol", "content": "ping"}
        )
        assert ev.content == "[inbox] carol: ping"
        # prompt_override must be the *rendered* string, not the raw template
        # — otherwise the controller shows the unfilled {placeholders}.
        assert ev.prompt_override == "[inbox] carol: ping"

    async def test_metadata_placeholders_available(self):
        reg = _broadcast_registry()
        t = ChannelTrigger(
            channel_name="inbox",
            registry=reg,
            prompt="urgency={urgency}",
        )
        await t.start()
        ev = await _wait_with_delivery(
            t,
            reg,
            "inbox",
            {"sender": "x", "content": "c", "metadata": {"urgency": "high"}},
        )
        assert ev.content == "urgency=high"


class TestLifecycle:
    async def test_stop_unsubscribes_broadcast_subscription(self):
        reg = _broadcast_registry()
        # Pre-create the channel as broadcast so the trigger goes through
        # the AgentChannel subscription path.
        reg.get_or_create("inbox", channel_type="broadcast")
        t = ChannelTrigger(channel_name="inbox", registry=reg)
        await t.start()
        # Force subscription creation by waiting once.
        await _wait_with_delivery(t, reg, "inbox", {"sender": "a", "content": "msg"})
        assert t._subscription is not None
        await t.stop()
        # Subscription cleared on stop.
        assert t._subscription is None

    async def test_on_start_resolves_registry_from_session(self):
        reg = _broadcast_registry()

        class _Session:
            channels = reg

        # No explicit registry — _on_start resolves it from the session.
        t = ChannelTrigger(channel_name="inbox", session=_Session())
        await t.start()
        assert t._registry is reg

    async def test_on_start_falls_back_to_global_registry(self):
        # No registry, no session → _on_start uses the global singleton.
        from kohakuterrarium.core.session import get_channel_registry

        t = ChannelTrigger(channel_name="inbox")
        await t.start()
        assert t._registry is get_channel_registry()

    def test_resume_dict_round_trip(self):
        original = ChannelTrigger(
            channel_name="inbox",
            prompt="p",
            filter_sender="bob",
            ignore_sender_id="cid",
        )
        clone = ChannelTrigger.from_resume_dict(original.to_resume_dict())
        assert clone.channel_name == "inbox"
        assert clone.filter_sender == "bob"
        assert clone.ignore_sender_id == "cid"

    def test_class_metadata(self):
        assert ChannelTrigger.universal is True
        assert ChannelTrigger.resumable is True
        assert ChannelTrigger.setup_tool_name == "watch_channel"


class TestPostSetup:
    """post_setup wires registry + self-ignore from the invoking context."""

    def test_registry_resolved_from_environment_shared_channels(self):
        reg = _broadcast_registry()

        class _Env:
            shared_channels = reg

        class _Agent:
            environment = _Env()

        class _Ctx:
            agent = _Agent()
            agent_name = "watcher"

        trigger = ChannelTrigger(channel_name="inbox")
        ChannelTrigger.post_setup(trigger, _Ctx())
        assert trigger._registry is reg
        # The invoking agent's name becomes the self-ignore sender.
        assert trigger.ignore_sender == "watcher"

    def test_registry_resolved_from_session_channels(self):
        reg = _broadcast_registry()

        class _Session:
            channels = reg

        class _Agent:
            environment = None
            session = _Session()

        class _Ctx:
            agent = _Agent()
            agent_name = "watcher"

        trigger = ChannelTrigger(channel_name="inbox")
        ChannelTrigger.post_setup(trigger, _Ctx())
        assert trigger._registry is reg

    def test_ignore_sender_id_set_from_creature_id(self):
        class _Agent:
            environment = None
            session = None
            _creature_id = "cid-99"

        class _Ctx:
            agent = _Agent()
            agent_name = "watcher"

        trigger = ChannelTrigger(channel_name="inbox")
        ChannelTrigger.post_setup(trigger, _Ctx())
        # The stable creature id is wired as the self-ignore id.
        assert trigger.ignore_sender_id == "cid-99"

    def test_post_setup_with_no_agent_is_a_noop(self):
        class _Ctx:
            agent = None
            agent_name = None

        trigger = ChannelTrigger(channel_name="inbox")
        ChannelTrigger.post_setup(trigger, _Ctx())
        # Nothing to wire — registry stays None, no crash.
        assert trigger._registry is None
        assert trigger.ignore_sender is None

    def test_post_setup_preserves_explicit_ignore_sender(self):
        class _Agent:
            environment = None
            session = None

        class _Ctx:
            agent = _Agent()
            agent_name = "watcher"

        # An explicitly-set ignore_sender must NOT be overwritten.
        trigger = ChannelTrigger(channel_name="inbox", ignore_sender="explicit")
        ChannelTrigger.post_setup(trigger, _Ctx())
        assert trigger.ignore_sender == "explicit"
