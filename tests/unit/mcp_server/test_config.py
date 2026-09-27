"""Dedicated configuration rejects fields that cannot take effect."""

import json

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


def test_registered_delegation_targets_are_file_relative(tmp_path):
    path = tmp_path / "mcp.yaml"
    path.write_text(
        "workspace: .\ndelegation:\n"
        "  coder:\n    kind: creature\n    config: ./coder\n"
        "  reviewer:\n    kind: subagent\n    config: '@pack/reviewer.yaml'\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.delegation["coder"].config == str(tmp_path / "coder")
    assert config.delegation["reviewer"].config == "@pack/reviewer.yaml"


@pytest.mark.parametrize("kind", ["terrarium", "inline", ""])
def test_delegation_rejects_unsupported_target_kind(tmp_path, kind):
    with pytest.raises(ValueError):
        MCPToolsConfig.model_validate(
            {
                "workspace": tmp_path,
                "delegation": {"target": {"kind": kind, "config": "."}},
            }
        )


@pytest.mark.parametrize("value", ["wrong", [], ["target"]])
def test_malformed_catalog_reports_validation_error(tmp_path, value):
    config = tmp_path / "bad.json"
    config.write_text(
        json.dumps({"workspace": ".", "delegation": value}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_config(config)
