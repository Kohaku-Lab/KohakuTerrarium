"""Unit tests for :mod:`kohakuterrarium.cli.identity.mcp`."""

from kohakuterrarium.cli.identity.mcp import list_cli, list_for_agent_cli
from kohakuterrarium.studio.identity.mcp.servers import save_servers

SECRET = "Bearer s3cret-token"


class TestListing:
    def test_global_listing_names_headers_without_their_values(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        save_servers(
            [
                {
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": "http://h/mcp",
                    "headers": {"Authorization": SECRET},
                }
            ]
        )
        assert list_cli() == 0
        out = capsys.readouterr().out
        assert "headers:   ['Authorization']" in out
        assert SECRET not in out

    def test_agent_listing_names_headers_without_their_values(self, tmp_path, capsys):
        (tmp_path / "config.yaml").write_text(
            "name: a\n"
            "mcp_servers:\n"
            "  - name: remote\n"
            "    transport: streamable_http\n"
            "    url: http://h/mcp\n"
            f"    headers: {{Authorization: '{SECRET}'}}\n",
            encoding="utf-8",
        )
        assert list_for_agent_cli(str(tmp_path)) == 0
        out = capsys.readouterr().out
        assert "Headers: Authorization" in out
        assert SECRET not in out
