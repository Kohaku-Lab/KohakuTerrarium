"""Unit tests for :mod:`kohakuterrarium.api.schemas`."""

import pytest
from pydantic import ValidationError

from kohakuterrarium.api.schemas import (
    AgentChat,
    AgentCreate,
    ChannelAdd,
    ChannelSend,
    ContentMetaPayload,
    CreatureAdd,
    FileDelete,
    FileMkdir,
    FilePartPayload,
    FilePayload,
    FileRename,
    FileWrite,
    ForkMutationPayload,
    ForkRequest,
    ForkResponse,
    ImagePartPayload,
    ImageUrlPayload,
    MessageEdit,
    ModelSwitch,
    RegenerateRequest,
    RenameRequest,
    SlashCommand,
    TerrariumCreate,
    TerrariumStatus,
    TextPartPayload,
    WireChannel,
)

# ── plain models accept valid payloads ──────────────────────────


class TestSimpleModels:
    def test_terrarium_create(self):
        m = TerrariumCreate(config_path="/path")
        assert m.config_path == "/path"
        assert m.llm is None
        assert m.on_node is None

    def test_terrarium_status(self):
        m = TerrariumStatus(
            terrarium_id="id", name="n", running=True, creatures={}, channels=[]
        )
        assert m.running is True

    def test_creature_add(self):
        m = CreatureAdd(name="alice", config_path="/p")
        assert m.listen_channels == []
        assert m.send_channels == []

    def test_channel_add_defaults(self):
        m = ChannelAdd(name="ch")
        assert m.channel_type == "queue"
        assert m.description == ""

    def test_wire_channel(self):
        m = WireChannel(channel="ch", direction="listen")
        assert m.enabled is True

    def test_agent_create(self):
        m = AgentCreate(config_path="/p")
        assert m.llm is None

    def test_rename_request(self):
        m = RenameRequest(name="new")
        assert m.name == "new"

    def test_model_switch(self):
        m = ModelSwitch(model="gpt-4")
        assert m.model == "gpt-4"

    def test_slash_command(self):
        m = SlashCommand(command="status")
        assert m.args == ""

    def test_file_models(self):
        assert FileWrite(path="p", content="c").content == "c"
        assert FileRename(old_path="a", new_path="b").new_path == "b"
        assert FileDelete(path="p").path == "p"
        assert FileMkdir(path="d").path == "d"


# ── ContentPart discriminated union ─────────────────────────────


class TestContentParts:
    def test_text_part(self):
        p = TextPartPayload(type="text", text="hi")
        assert p.text == "hi"

    def test_image_part(self):
        p = ImagePartPayload(
            type="image_url",
            image_url=ImageUrlPayload(url="u"),
            meta=ContentMetaPayload(source_type="upload"),
        )
        assert p.image_url.url == "u"
        assert p.image_url.detail == "low"
        assert p.meta.source_type == "upload"

    def test_image_url_detail_validation(self):
        with pytest.raises(ValidationError):
            ImageUrlPayload(url="u", detail="ULTRA")  # type: ignore

    def test_file_part(self):
        p = FilePartPayload(
            type="file",
            file=FilePayload(name="x.txt", content="hello"),
        )
        assert p.file.content == "hello"

    def test_file_payload_minimal(self):
        f = FilePayload()
        assert f.path is None
        assert f.is_inline is False


# ── Channel / chat / regen / edit ──────────────────────────────


class TestChannelSend:
    def test_str_content(self):
        m = ChannelSend(content="hi")
        assert m.content == "hi"
        assert m.sender == "human"

    def test_list_content(self):
        m = ChannelSend(content=[TextPartPayload(type="text", text="hi")])
        assert isinstance(m.content, list)


class TestAgentChat:
    def test_message_only(self):
        m = AgentChat(message="hi")
        assert m.message == "hi"
        assert m.content is None


class TestRegenerateRequest:
    def test_defaults(self):
        m = RegenerateRequest()
        assert m.turn_index is None
        assert m.branch_view is None

    def test_with_branch_view(self):
        m = RegenerateRequest(turn_index=2, branch_view={1: 2})
        assert m.branch_view == {1: 2}


class TestMessageEdit:
    def test_string_content(self):
        m = MessageEdit(content="hello")
        assert m.content == "hello"

    def test_list_content(self):
        m = MessageEdit(content=[TextPartPayload(type="text", text="hi")])
        assert isinstance(m.content, list)


# ── ForkRequest / ForkResponse ──────────────────────────────────


class TestForkRequest:
    def test_minimal(self):
        m = ForkRequest(at_event_id=5)
        assert m.at_event_id == 5
        assert m.mutate is None

    def test_with_mutation(self):
        m = ForkRequest(
            at_event_id=5,
            mutate=ForkMutationPayload(kind="drop_trailing"),
        )
        assert m.mutate.kind == "drop_trailing"

    def test_bad_mutation_kind(self):
        with pytest.raises(ValidationError):
            ForkMutationPayload(kind="bogus")  # type: ignore


class TestForkResponse:
    def test_basic(self):
        m = ForkResponse(session_id="s", fork_point=5, path="/p")
        assert m.fork_point == 5
