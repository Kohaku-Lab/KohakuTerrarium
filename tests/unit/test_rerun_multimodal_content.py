"""Pin: rerun trigger normalises multimodal content before push.

Frontend ``editMessage`` builds a ``[{type:"text", text:"..."}]`` list
via ``buildMessageParts`` and POSTs it to the backend. The route
forwards the list to ``agent.edit_and_rerun(...)``, which delegates to
``_rerun_from_last``. If the helper stuffs the raw dict-list straight
into the TriggerEvent, ``_format_events_for_context`` ignores every
entry (it only matches typed ``ContentPart`` instances) and the
controller's ``combined_text`` collapses to ``""``. In native mode
that triggers ``skip_empty=True`` and the LLM ends up running with
the truncated conversation but no new user message — the symptom
users reported as "edit+rerun runs without the edit".
"""

import pytest

from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.events import EventType
from kohakuterrarium.llm.message import ImagePart, TextPart


class _CapturingAgent(AgentMessagesMixin):
    """Strip-down agent that captures the TriggerEvent ``_rerun_from_last``
    would have pushed, instead of routing it through the real lock /
    controller pipeline."""

    def __init__(self):
        self.captured = []

    async def _process_event(self, event):
        self.captured.append(event)


@pytest.mark.asyncio
async def test_rerun_normalises_dict_list_into_typed_parts():
    agent = _CapturingAgent()
    payload = [{"type": "text", "text": "actually, hello"}]
    await agent._rerun_from_last(new_user_content=payload)

    assert len(agent.captured) == 1
    event = agent.captured[0]
    assert event.type == EventType.USER_INPUT
    assert event.context == {"rerun": True, "edited": True}
    assert isinstance(event.content, list)
    assert len(event.content) == 1
    assert isinstance(event.content[0], TextPart)
    assert event.content[0].text == "actually, hello"


@pytest.mark.asyncio
async def test_rerun_normalises_multimodal_dict_list():
    agent = _CapturingAgent()
    payload = [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abcd"}},
    ]
    await agent._rerun_from_last(new_user_content=payload)

    event = agent.captured[0]
    assert isinstance(event.content, list)
    assert len(event.content) == 2
    assert isinstance(event.content[0], TextPart)
    assert isinstance(event.content[1], ImagePart)


@pytest.mark.asyncio
async def test_rerun_passes_string_through_unchanged():
    agent = _CapturingAgent()
    await agent._rerun_from_last(new_user_content="plain edit")

    event = agent.captured[0]
    assert event.content == "plain edit"
    assert event.context == {"rerun": True, "edited": True}


@pytest.mark.asyncio
async def test_rerun_pure_regen_uses_empty_string_no_edited_flag():
    agent = _CapturingAgent()
    await agent._rerun_from_last()

    event = agent.captured[0]
    assert event.content == ""
    assert event.context == {"rerun": True, "edited": False}
