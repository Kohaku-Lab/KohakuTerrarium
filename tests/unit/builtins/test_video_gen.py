"""Tests for video artifact output from the background built-in tool."""

import base64
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from kohakuterrarium.builtins.tools.read import MAX_IMAGE_BYTES
from kohakuterrarium.builtins.tools.video_gen import VideoGenTool
from kohakuterrarium.llm.grok_video import GrokVideoResult
from kohakuterrarium.modules.tool.base import ToolContext


class _Client:
    def __init__(self):
        self.calls = []

    async def generate(self, args):
        self.calls.append(args)
        return GrokVideoResult(
            content=b"mp4",
            mime="video/mp4",
            model=args["model"],
            request_id="req-1",
            duration=3,
        )


class _Store:
    session_id = "session-1"

    def __init__(self, root):
        self.root = root

    def write_artifact(self, filename, data):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class _Conversation:
    def __init__(self, messages):
        self.messages = messages

    def to_messages(self):
        return self.messages


def _png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, format="PNG")
    return output.getvalue()


class TestVideoGenTool:
    @pytest.mark.asyncio
    async def test_completed_video_is_a_stable_file_part(self, tmp_path):
        artifact_root = tmp_path / "session-1_deadbeef.artifacts"
        context = ToolContext(
            agent_name="a",
            session=None,
            working_dir=tmp_path,
            agent=SimpleNamespace(session_store=_Store(artifact_root)),
        )
        tool = VideoGenTool(client=_Client())

        result = await tool.execute({"prompt": "cat"}, context=context)

        assert result.success
        location = result.output[0]
        part = result.output[1]
        written = list((artifact_root / "generated_videos").glob("*.mp4"))
        assert len(written) == 1
        assert location.text == f"Video saved to: {written[0]}"
        assert part.mime == "video/mp4"
        assert part.path.startswith(
            "/api/sessions/session-1_deadbeef/artifacts/generated_videos/"
        )
        assert result.metadata["request_id"] == "req-1"
        artifact = result.metadata["session_metadata"]["artifacts"][0]
        assert artifact["kind"] == "video"
        assert artifact["relative_path"].startswith("generated_videos/")
        assert artifact["url"] == part.path
        assert written[0].read_bytes() == b"mp4"

    @pytest.mark.asyncio
    async def test_context_is_required(self):
        result = await VideoGenTool(client=_Client()).execute({"prompt": "cat"})
        assert result.error == "video_gen requires an agent execution context"

    @pytest.mark.asyncio
    async def test_local_image_is_encoded_inside_the_tool(self, tmp_path):
        image_path = tmp_path / "reference.png"
        image_path.write_bytes(_png_bytes())
        client = _Client()
        context = ToolContext(
            agent_name="a",
            session=None,
            working_dir=tmp_path,
            agent=SimpleNamespace(session_store=None),
        )

        args = {"prompt": "dance", "input_image": str(image_path)}
        result = await VideoGenTool(client=client).execute(args, context=context)

        assert result.success
        reference = client.calls[0]["input_image"]
        assert reference.startswith("data:image/png;base64,")
        assert str(image_path) not in reference
        assert "base64" not in result.get_text_output()
        assert args["input_image"] == str(image_path)

    @pytest.mark.asyncio
    async def test_latest_uses_inline_user_attachment_without_model_output(
        self, tmp_path
    ):
        encoded = base64.b64encode(_png_bytes()).decode("ascii")
        conversation = _Conversation(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "animate this"},
                        {
                            "type": "file",
                            "file": {
                                "mime": "image/png",
                                "data_base64": encoded,
                                "is_inline": True,
                            },
                        },
                    ],
                }
            ]
        )
        client = _Client()
        context = ToolContext(
            agent_name="a",
            session=None,
            working_dir=tmp_path,
            agent=SimpleNamespace(session_store=None, conversation=conversation),
        )

        args = {"prompt": "dance", "input_image": "latest"}
        result = await VideoGenTool(client=client).execute(args, context=context)

        assert result.success
        assert client.calls[0]["input_image"] == f"data:image/png;base64,{encoded}"
        assert encoded not in result.get_text_output()
        assert args["input_image"] == "latest"

    @pytest.mark.asyncio
    async def test_deleted_upload_temp_path_falls_back_to_inline_attachment(
        self, tmp_path
    ):
        encoded = base64.b64encode(_png_bytes()).decode("ascii")
        conversation = _Conversation(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "file",
                            "file": {
                                "mime": "image/png",
                                "data_base64": encoded,
                            },
                        }
                    ],
                }
            ]
        )
        client = _Client()
        context = ToolContext(
            agent_name="a",
            session=None,
            working_dir=tmp_path,
            agent=SimpleNamespace(session_store=None, conversation=conversation),
        )
        missing = str(Path(tempfile.gettempdir()) / "deleted-upload.png")

        result = await VideoGenTool(client=client).execute(
            {"prompt": "dance", "input_image": missing}, context=context
        )

        assert result.success
        assert client.calls[0]["input_image"] == f"data:image/png;base64,{encoded}"

    @pytest.mark.asyncio
    async def test_oversized_local_image_is_rejected_before_reading(self, tmp_path):
        image_path = tmp_path / "too-large.png"
        with image_path.open("wb") as handle:
            handle.seek(MAX_IMAGE_BYTES)
            handle.write(b"x")
        client = _Client()
        context = ToolContext(
            agent_name="a",
            session=None,
            working_dir=tmp_path,
            agent=SimpleNamespace(session_store=None),
        )

        result = await VideoGenTool(client=client).execute(
            {"prompt": "dance", "input_image": str(image_path)}, context=context
        )

        assert "Max: 20MB" in result.error
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_latest_without_attachment_is_rejected_before_submission(
        self, tmp_path
    ):
        client = _Client()
        context = ToolContext(
            agent_name="a",
            session=None,
            working_dir=tmp_path,
            agent=SimpleNamespace(session_store=None),
        )

        result = await VideoGenTool(client=client).execute(
            {"prompt": "dance", "input_image": "latest"}, context=context
        )

        assert "No attached image" in result.error
        assert client.calls == []
