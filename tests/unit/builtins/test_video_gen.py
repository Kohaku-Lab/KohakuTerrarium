"""Tests for video artifact output from the background built-in tool."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tools.video_gen import VideoGenTool
from kohakuterrarium.llm.grok_video import GrokVideoResult
from kohakuterrarium.modules.tool.base import ToolContext


class _Client:
    async def generate(self, args):
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
