"""Workspace manifest sync — ``POST /api/studio/workspace/manifest/sync``."""

from pathlib import Path

TOOL_SRC = '''\
"""Sample workspace tool."""

from kohakuterrarium.modules.tool.base import BaseTool, ToolResult


class MyTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "x"

    async def _execute(self, args):
        return ToolResult(output="ok")
'''

SUBAGENT_SRC = '''\
"""Sample sub-agent."""

from kohakuterrarium.modules.subagent.config import SubAgentConfig

MY_SUBAGENT_CONFIG = SubAgentConfig(
    name="my_subagent",
    description="x",
    tools=[],
    system_prompt="test",
)
'''

INPUT_SRC = '''\
"""Sample input."""

from kohakuterrarium.modules.input.base import BaseInputModule


class MyInput(BaseInputModule):
    async def get_input(self):
        return None
'''


def _write_tool(tmp: Path) -> None:
    (tmp / "modules" / "tools" / "my_tool.py").write_text(TOOL_SRC, encoding="utf-8")


def test_sync_creates_manifest_when_missing(client, tmp_workspace: Path):
    _write_tool(tmp_workspace)

    resp = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "tools", "name": "my_tool"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] is True
    assert body["path"] == "kohaku.yaml"
    assert body["entry"]["name"] == "my_tool"
    assert body["entry"]["module"] == "modules.tools.my_tool"
    assert body["entry"]["class"] == "MyTool"

    text = (tmp_workspace / "kohaku.yaml").read_text(encoding="utf-8")
    assert "name: my_tool" in text
    assert "module: modules.tools.my_tool" in text


def test_sync_is_idempotent(client, tmp_workspace: Path):
    _write_tool(tmp_workspace)
    client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "tools", "name": "my_tool"},
    )
    resp = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "tools", "name": "my_tool"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] is False


def test_sync_preserves_existing_comments(client, tmp_workspace: Path):
    (tmp_workspace / "kohaku.yaml").write_text(
        "# top comment\n"
        "name: demo\n"
        'version: "1.0"\n'
        "tools:\n"
        "  # existing tool\n"
        "  - name: other_tool\n"
        "    module: modules.tools.other_tool\n"
        "    class: OtherTool\n",
        encoding="utf-8",
    )
    _write_tool(tmp_workspace)

    resp = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "tools", "name": "my_tool"},
    )
    assert resp.status_code == 200

    text = (tmp_workspace / "kohaku.yaml").read_text(encoding="utf-8")
    assert "# top comment" in text
    assert "# existing tool" in text
    assert "other_tool" in text
    assert "my_tool" in text


def test_sync_subagent_omits_class_field(client, tmp_workspace: Path):
    (tmp_workspace / "modules" / "subagents" / "my_subagent.py").write_text(
        SUBAGENT_SRC, encoding="utf-8"
    )
    resp = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "subagents", "name": "my_subagent"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "class" not in body["entry"]
    text = (tmp_workspace / "kohaku.yaml").read_text(encoding="utf-8")
    assert "class:" not in text.split("subagents:")[1].split("\n\n")[0]


def test_sync_io_routes_under_io_key(client, tmp_workspace: Path):
    (tmp_workspace / "modules" / "inputs" / "my_input.py").write_text(
        INPUT_SRC, encoding="utf-8"
    )
    resp = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "inputs", "name": "my_input"},
    )
    assert resp.status_code == 200
    text = (tmp_workspace / "kohaku.yaml").read_text(encoding="utf-8")
    assert "io:" in text
    assert "my_input" in text


def test_sync_404_when_module_missing(client, tmp_workspace: Path):
    resp = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "tools", "name": "ghost"},
    )
    assert resp.status_code == 404


def test_sync_400_on_unknown_kind(client, tmp_workspace: Path):
    resp = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "nonsense", "name": "x"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "unknown_kind"
