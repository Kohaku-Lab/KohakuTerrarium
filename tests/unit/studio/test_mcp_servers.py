"""Unit tests for :mod:`kohakuterrarium.studio.identity.mcp_servers`."""

import pytest
import yaml

from kohakuterrarium.studio.identity.mcp_servers import (
    delete_server,
    find_server,
    load_agent_mcp_servers,
    load_servers,
    mcp_config_path,
    prompt_server_dict,
    save_servers,
    upsert_server,
)


@pytest.fixture(autouse=True)
def _redirect_path(tmp_path, monkeypatch):
    """Isolate ``mcp_servers.yaml`` via ``KT_CONFIG_DIR``.

    ``mcp_servers.py`` resolves its path through ``config_dir()``; the
    env var is the documented isolation seam — keeps the suite off the
    operator's real ``~/.kohakuterrarium/``.
    """
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path / "mcp_servers.yaml"


# ── mcp_config_path ──────────────────────────────────────────────


class TestConfigPath:
    def test_returns_path(self, _redirect_path):
        assert mcp_config_path() == _redirect_path


# ── load / save / find ───────────────────────────────────────────


class TestLoadServers:
    def test_returns_empty_when_missing(self):
        assert load_servers() == []

    def test_round_trip(self, _redirect_path):
        servers = [{"name": "s1", "transport": "stdio"}]
        save_servers(servers)
        assert load_servers() == servers

    def test_non_list_returns_empty(self, _redirect_path):
        _redirect_path.write_text("not_a_list: true")
        assert load_servers() == []

    def test_malformed_returns_empty(self, _redirect_path):
        _redirect_path.write_text("not: : yaml: :")
        assert load_servers() == []


class TestUpsertServer:
    def test_add_new(self, _redirect_path):
        s = {"name": "alpha", "transport": "stdio"}
        out = upsert_server(s)
        assert out == s
        assert find_server("alpha") == s

    def test_replace_existing(self, _redirect_path):
        upsert_server({"name": "alpha", "transport": "stdio"})
        upsert_server({"name": "alpha", "transport": "http"})
        assert find_server("alpha")["transport"] == "http"
        # Still only one entry.
        assert len(load_servers()) == 1

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="Name is required"):
            upsert_server({})


class TestDeleteServer:
    def test_remove(self, _redirect_path):
        upsert_server({"name": "s1"})
        assert delete_server("s1") is True
        assert find_server("s1") is None

    def test_missing_returns_false(self):
        assert delete_server("ghost") is False


class TestFindServer:
    def test_known(self, _redirect_path):
        upsert_server({"name": "s1"})
        assert find_server("s1")["name"] == "s1"

    def test_unknown(self):
        assert find_server("ghost") is None


# ── prompt_server_dict ───────────────────────────────────────────


def _make_prompt(responses: dict[str, str]):
    def p(label, default):
        return responses.get(label, default)

    return p


class TestPromptServerDict:
    def test_basic(self):
        p = _make_prompt(
            {
                "Name": "alpha",
                "Transport": "stdio",
                "Command": "echo",
                "Args JSON array": '["a","b"]',
                "Env JSON object": '{"K":"V"}',
                "URL": "",
                "Connect timeout (seconds)": "5.5",
            }
        )
        out = prompt_server_dict(None, p)
        assert out["name"] == "alpha"
        assert out["args"] == ["a", "b"]
        assert out["env"] == {"K": "V"}
        assert out["connect_timeout"] == 5.5

    def test_invalid_args_json(self):
        p = _make_prompt({"Name": "x", "Args JSON array": "{not json"})
        with pytest.raises(ValueError, match="Invalid args JSON"):
            prompt_server_dict(None, p)

    def test_args_not_list(self):
        p = _make_prompt({"Name": "x", "Args JSON array": '{"a":1}'})
        with pytest.raises(ValueError, match="Args must be"):
            prompt_server_dict(None, p)

    def test_invalid_env_json(self):
        p = _make_prompt({"Name": "x", "Env JSON object": "{not json"})
        with pytest.raises(ValueError, match="Invalid env JSON"):
            prompt_server_dict(None, p)

    def test_env_not_dict(self):
        p = _make_prompt({"Name": "x", "Env JSON object": "[1,2]"})
        with pytest.raises(ValueError, match="Env must be"):
            prompt_server_dict(None, p)

    def test_missing_name_raises(self):
        p = _make_prompt({"Name": ""})
        with pytest.raises(ValueError, match="Name is required"):
            prompt_server_dict(None, p)

    def test_invalid_timeout(self):
        p = _make_prompt({"Name": "x", "Connect timeout (seconds)": "abc"})
        with pytest.raises(ValueError, match="Invalid connect timeout"):
            prompt_server_dict(None, p)

    def test_blank_timeout_becomes_none(self):
        p = _make_prompt({"Name": "x", "Connect timeout (seconds)": ""})
        out = prompt_server_dict(None, p)
        assert out["connect_timeout"] is None

    def test_existing_dict_seeds_defaults(self):
        existing = {
            "name": "alpha",
            "transport": "stdio",
            "args": ["a"],
            "env": {"K": "V"},
            "connect_timeout": 3,
        }

        # Return the default at each prompt.
        def p(label, default):
            return default

        out = prompt_server_dict(existing, p)
        assert out["name"] == "alpha"
        assert out["connect_timeout"] == 3


# ── load_agent_mcp_servers ───────────────────────────────────────


class TestLoadAgentMcpServers:
    def test_unknown_path(self):
        servers, cfg, err = load_agent_mcp_servers("/no/such/path")
        assert servers == []
        assert cfg is None
        assert "Agent path not found" in err

    def test_missing_config(self, tmp_path):
        d = tmp_path / "agent"
        d.mkdir()
        servers, cfg, err = load_agent_mcp_servers(str(d))
        assert servers == []
        assert cfg is None
        assert "No config.yaml" in err

    def test_with_config(self, tmp_path):
        d = tmp_path / "agent"
        d.mkdir()
        (d / "config.yaml").write_text(
            yaml.safe_dump({"mcp_servers": [{"name": "s1"}]})
        )
        servers, cfg, err = load_agent_mcp_servers(str(d))
        assert err is None
        assert servers == [{"name": "s1"}]

    def test_with_config_yml(self, tmp_path):
        d = tmp_path / "agent"
        d.mkdir()
        (d / "config.yml").write_text(yaml.safe_dump({"mcp_servers": []}))
        _, cfg, err = load_agent_mcp_servers(str(d))
        assert err is None
        assert cfg.name == "config.yml"

    def test_broken_yaml(self, tmp_path):
        d = tmp_path / "agent"
        d.mkdir()
        (d / "config.yaml").write_text(":\n:not yaml:")
        servers, cfg, err = load_agent_mcp_servers(str(d))
        assert servers == []
        assert err is not None
