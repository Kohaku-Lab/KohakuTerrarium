"""Tests for the ``image_gen`` tool and its input-image helpers."""

import base64
import io

import pytest
from PIL import Image

from kohakuterrarium.builtins.tools.media.image_gen import ImageGenTool, _client_for
from kohakuterrarium.builtins.tools.media.image_inputs import (
    ImageInputError,
    file_data_url,
    recent_image_urls,
)
from kohakuterrarium.llm.message import ImagePart, Message
from kohakuterrarium.modules.tool.base import ToolContext

PNG = "data:image/png;base64,iVBORw0KGgo="


class _Client:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, **options):
        self.calls.append(("generate", prompt, options))
        return [ImagePart(url=PNG)]

    async def edit(self, prompt, images, **options):
        self.calls.append(("edit", prompt, images, options))
        return [ImagePart(url=PNG)]


class _Conversation:
    def __init__(self, messages):
        self._messages = messages

    def get_messages(self):
        return list(self._messages)


def _context(tmp_path, messages=()):
    controller = type("C", (), {"conversation": _Conversation(messages)})()
    agent = type("A", (), {"controller": controller})()
    return ToolContext(agent_name="a", session=None, working_dir=tmp_path, agent=agent)


def _png(path, size=(8, 8)):
    Image.new("RGB", size, (200, 30, 30)).save(path)
    return path


class TestSchema:
    def test_arguments_follow_the_codex_tool(self):
        schema = ImageGenTool().get_parameters_schema()
        assert list(schema["properties"]) == [
            "prompt",
            "transparent_background",
            "referenced_image_paths",
            "num_last_images_to_include",
        ]
        assert schema["required"] == ["prompt"]

    def test_gif_is_offered_only_when_the_option_is_on(self):
        tool = ImageGenTool()
        tool.refresh_runtime_options({"gif": True})
        assert "gif" in tool.get_parameters_schema()["properties"]

    def test_the_tool_runs_locally_and_carries_guidance(self):
        tool = ImageGenTool()
        assert not tool.is_provider_native
        assert "referenced_image_paths" in tool.prompt_contribution()


class TestExecute:
    @pytest.mark.asyncio
    async def test_a_prompt_alone_generates(self, tmp_path):
        client = _Client()
        result = await ImageGenTool(client=client).execute(
            {"prompt": "frog", "transparent_background": True}, _context(tmp_path)
        )
        assert result.success and result.has_images()
        assert client.calls == [
            ("generate", "frog", {"transparent_background": True, "gif": None})
        ]
        assert result.metadata["_image_artifact_subdir"] == "generated_images"

    @pytest.mark.asyncio
    async def test_referenced_paths_become_inline_images_for_an_edit(self, tmp_path):
        _png(tmp_path / "a.png")
        client = _Client()
        result = await ImageGenTool(client=client).execute(
            {"prompt": "hat", "referenced_image_paths": ["a.png"]}, _context(tmp_path)
        )
        assert result.success
        kind, _, images, _ = client.calls[0]
        assert kind == "edit" and images[0].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_recent_conversation_images_feed_an_edit(self, tmp_path):
        messages = [
            Message(role="tool", content=[ImagePart(url=PNG)], tool_call_id="t"),
            Message(role="user", content="more"),
        ]
        client = _Client()
        result = await ImageGenTool(client=client).execute(
            {"prompt": "blue", "num_last_images_to_include": 1},
            _context(tmp_path, messages),
        )
        assert result.success and client.calls[0][2] == [PNG]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (
                {"referenced_image_paths": ["a.png"], "num_last_images_to_include": 1},
                "only one of",
            ),
            ({"num_last_images_to_include": 6}, "from 1 to 5"),
            ({"referenced_image_paths": ["a.png"] * 6}, "at most 5"),
            ({"referenced_image_paths": ["missing.png"]}, "not found"),
            ({"num_last_images_to_include": 2}, "holds only 0"),
            ({"gif": {}}, "not enabled"),
        ],
    )
    async def test_bad_arguments_return_an_error_and_send_nothing(
        self, tmp_path, args, message
    ):
        client = _Client()
        result = await ImageGenTool(client=client).execute(
            {"prompt": "x", **args}, _context(tmp_path)
        )
        assert not result.success and message in result.error
        assert client.calls == []


class TestInputs:
    def test_a_large_local_image_is_downscaled(self, tmp_path):
        path = _png(tmp_path / "big.png", size=(4096, 1024))
        url = file_data_url(path)
        data = base64.b64decode(url.split(",", 1)[1])
        assert max(Image.open(io.BytesIO(data)).size) == 2048

    def test_a_file_that_is_not_an_image_is_refused(self, tmp_path):
        path = tmp_path / "x.png"
        path.write_text("nope")
        with pytest.raises(ImageInputError, match="not a readable image"):
            file_data_url(path)

    def test_recent_images_come_newest_first_across_messages(self):
        old, new = "data:image/png;base64,AA", "data:image/png;base64,BB"
        messages = [
            Message(role="user", content=[ImagePart(url=old)]),
            Message(role="tool", content=[ImagePart(url=new)], tool_call_id="t"),
        ]
        assert recent_image_urls(messages, 2) == [new, old]


class TestBackendSelection:
    def test_a_custom_codex_backend_supplies_the_endpoint_and_key(self, tmp_path):
        llm = type(
            "L", (), {"image_endpoint": ("https://corp.example/codex", "sk-1")}
        )()
        context = _context(tmp_path)
        context.agent.llm = llm
        client = _client_for(context)
        assert (client.base_url, client.api_key) == (
            "https://corp.example/codex",
            "sk-1",
        )

    def test_other_providers_use_the_chatgpt_login(self, tmp_path):
        context = _context(tmp_path)
        context.agent.llm = object()
        client = _client_for(context)
        assert client.api_key is None and client.base_url.endswith("/backend-api/codex")
