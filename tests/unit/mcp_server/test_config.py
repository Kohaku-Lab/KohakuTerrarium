"""Dedicated configuration rejects fields that cannot take effect."""

import pytest
from pydantic import ValidationError

from kohakuterrarium.mcp_server.config import MCPToolsConfig, load_config


@pytest.mark.parametrize(
    "field", ["llm", "triggers", "system_prompt", "base_config", "compact"]
)
def test_rejects_agent_fields(tmp_path, field):
    with pytest.raises(ValidationError):
        MCPToolsConfig.model_validate({"workspace": tmp_path, field: "ignored"})


def test_rejects_unknown_duplicate_tools_and_missing_workspace(tmp_path):
    for tools in (
        [{"name": "subagent"}],
        [{"name": "read"}, {"name": "read"}],
        [{"name": "read", "doc_mode": "brief"}],
    ):
        with pytest.raises(ValidationError):
            MCPToolsConfig.model_validate({"workspace": tmp_path, "tools": tools})
    with pytest.raises(ValidationError):
        MCPToolsConfig(workspace=tmp_path / "missing")


def test_load_file_relative_workspace(tmp_path):
    config = tmp_path / "tools.yaml"
    config.write_text("workspace: .\ntools:\n  - name: read\n", encoding="utf-8")
    loaded = load_config(config)
    assert loaded.workspace == tmp_path and [t.name for t in loaded.tools] == ["read"]
    config.write_text("- invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        load_config(config)
