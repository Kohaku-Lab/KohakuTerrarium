"""Unit tests for :mod:`kohakuterrarium.core.mcp_registry`."""

import pytest

from kohakuterrarium.core.mcp_registry import (
    load_global_mcp_servers,
    mcp_config_path,
)


@pytest.fixture
def _redirect_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestMcpConfigPath:
    def test_points_at_config_dir(self, _redirect_path, tmp_path):
        assert mcp_config_path() == tmp_path / "mcp_servers.yaml"


class TestLoadGlobalMcpServers:
    def test_missing_file_returns_empty(self, _redirect_path):
        assert load_global_mcp_servers() == []

    def test_returns_servers(self, _redirect_path, tmp_path):
        (tmp_path / "mcp_servers.yaml").write_text(
            "- name: g1\n  url: http://x\n", encoding="utf-8"
        )
        servers = load_global_mcp_servers()
        assert servers == [{"name": "g1", "url": "http://x"}]

    def test_non_list_returns_empty(self, _redirect_path, tmp_path):
        (tmp_path / "mcp_servers.yaml").write_text(
            "name: not-a-list\n", encoding="utf-8"
        )
        assert load_global_mcp_servers() == []

    def test_malformed_returns_empty(self, _redirect_path, tmp_path):
        (tmp_path / "mcp_servers.yaml").write_text(
            "- name: [unclosed\n", encoding="utf-8"
        )
        assert load_global_mcp_servers() == []
