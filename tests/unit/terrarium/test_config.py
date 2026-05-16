"""Unit tests for :mod:`kohakuterrarium.terrarium.config`."""

from pathlib import Path

import pytest
import yaml

from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    TerrariumConfig,
    _find_terrarium_config,
    _format_channel_block,
    _parse_channels,
    _parse_creature,
    build_channel_topology_prompt,
    load_terrarium_config,
)

# ── dataclasses ──────────────────────────────────────────────────


class TestChannelConfig:
    def test_defaults(self):
        c = ChannelConfig(name="ch")
        assert c.channel_type == "queue"
        assert c.description == ""


class TestCreatureConfig:
    def test_defaults(self):
        c = CreatureConfig(name="x", config_data={}, base_dir=Path("."))
        assert c.listen_channels == []
        assert c.send_channels == []
        assert c.output_log is False
        assert c.output_log_size == 100


# ── _format_channel_block ────────────────────────────────────────


class TestFormatChannelBlock:
    def test_unknown_channel_returns_empty(self):
        out = _format_channel_block("nope", {}, set(), set())
        assert out == ""

    def test_listen_only(self):
        ch_by_name = {"a": ChannelConfig(name="a", channel_type="queue")}
        out = _format_channel_block("a", ch_by_name, {"a"}, set())
        assert "listen" in out
        assert "send" not in out

    def test_send_only(self):
        ch_by_name = {"a": ChannelConfig(name="a", channel_type="broadcast")}
        out = _format_channel_block("a", ch_by_name, set(), {"a"})
        assert "send" in out
        assert "broadcast" in out

    def test_listen_and_send_with_description(self):
        ch_by_name = {
            "a": ChannelConfig(name="a", channel_type="queue", description="docs")
        }
        out = _format_channel_block("a", ch_by_name, {"a"}, {"a"})
        assert "listen" in out
        assert "send" in out
        assert "docs" in out


# ── build_channel_topology_prompt ────────────────────────────────


class TestBuildChannelTopologyPrompt:
    def test_no_relevant_channels_returns_empty(self):
        cr = CreatureConfig(name="solo", config_data={}, base_dir=Path("."))
        cfg = TerrariumConfig(name="t", creatures=[cr], channels=[])
        assert build_channel_topology_prompt(cfg, cr) == ""

    def test_listen_and_send_workflow(self):
        cr = CreatureConfig(
            name="alice",
            config_data={},
            base_dir=Path("."),
            listen_channels=["inbox"],
            send_channels=["outbox"],
        )
        cfg = TerrariumConfig(
            name="t",
            creatures=[cr],
            channels=[
                ChannelConfig(name="inbox"),
                ChannelConfig(name="outbox"),
            ],
        )
        out = build_channel_topology_prompt(cfg, cr)
        assert "inbox" in out
        assert "outbox" in out
        assert "Your Workflow" in out

    def test_listen_only(self):
        cr = CreatureConfig(
            name="alice",
            config_data={},
            base_dir=Path("."),
            listen_channels=["inbox"],
        )
        cfg = TerrariumConfig(
            name="t",
            creatures=[cr],
            channels=[ChannelConfig(name="inbox")],
        )
        out = build_channel_topology_prompt(cfg, cr)
        assert "no outgoing channels configured" in out

    def test_send_only(self):
        cr = CreatureConfig(
            name="alice",
            config_data={},
            base_dir=Path("."),
            send_channels=["outbox"],
        )
        cfg = TerrariumConfig(
            name="t",
            creatures=[cr],
            channels=[ChannelConfig(name="outbox")],
        )
        out = build_channel_topology_prompt(cfg, cr)
        assert "Send your output to" in out

    def test_team_members_section(self):
        a = CreatureConfig(
            name="alice", config_data={}, base_dir=Path("."), listen_channels=["x"]
        )
        b = CreatureConfig(name="bob", config_data={}, base_dir=Path("."))
        cfg = TerrariumConfig(
            name="t",
            creatures=[a, b],
            channels=[ChannelConfig(name="x")],
        )
        out = build_channel_topology_prompt(cfg, a)
        assert "Team Members" in out
        assert "bob" in out


# ── _parse_creature ──────────────────────────────────────────────


class TestParseCreature:
    def test_basic(self):
        cr = _parse_creature(
            {"name": "alice", "channels": {"listen": ["a"], "can_send": ["b"]}},
            Path("/base"),
        )
        assert cr.name == "alice"
        assert cr.listen_channels == ["a"]
        assert cr.send_channels == ["b"]

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="missing 'name'"):
            _parse_creature({}, Path("/base"))

    def test_backward_compat_config_to_base_config(self):
        cr = _parse_creature(
            {"name": "x", "config": "./other"},
            Path("/base"),
        )
        # Legacy "config" key migrated to base_config.
        assert "base_config" in cr.config_data
        assert "config" not in cr.config_data

    def test_output_log_options(self):
        cr = _parse_creature(
            {"name": "x", "output_log": True, "output_log_size": 50},
            Path("/base"),
        )
        assert cr.output_log is True
        assert cr.output_log_size == 50


# ── _parse_channels ──────────────────────────────────────────────


class TestParseChannels:
    def test_dict_form(self):
        chans = _parse_channels(
            {
                "a": {"type": "broadcast", "description": "shared"},
                "b": {},
            }
        )
        assert chans[0].name == "a"
        assert chans[0].channel_type == "broadcast"
        assert chans[0].description == "shared"
        assert chans[1].name == "b"
        assert chans[1].channel_type == "queue"

    def test_bare_name(self):
        chans = _parse_channels({"a": None})
        assert chans[0].name == "a"
        assert chans[0].channel_type == "queue"


# ── _find_terrarium_config ───────────────────────────────────────


class TestFindTerrariumConfig:
    def test_file_returned_as_is(self, tmp_path):
        p = tmp_path / "terrarium.yaml"
        p.write_text("")
        assert _find_terrarium_config(p) == p

    def test_finds_yaml_in_dir(self, tmp_path):
        p = tmp_path / "terrarium.yaml"
        p.write_text("")
        assert _find_terrarium_config(tmp_path) == p

    def test_finds_yml_in_dir(self, tmp_path):
        p = tmp_path / "terrarium.yml"
        p.write_text("")
        assert _find_terrarium_config(tmp_path) == p

    def test_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _find_terrarium_config(tmp_path)


# ── load_terrarium_config ────────────────────────────────────────


class TestLoadTerrariumConfig:
    def test_minimal(self, tmp_path):
        path = tmp_path / "terrarium.yaml"
        path.write_text(yaml.safe_dump({"name": "t"}))
        cfg = load_terrarium_config(path)
        assert cfg.name == "t"
        assert cfg.creatures == []
        assert cfg.channels == []
        assert cfg.root is None

    def test_full(self, tmp_path):
        path = tmp_path / "terrarium.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "terrarium": {
                        "name": "team",
                        "channels": {
                            "team_chat": {"type": "broadcast", "description": "x"}
                        },
                        "creatures": [
                            {
                                "name": "alice",
                                "channels": {"listen": ["team_chat"]},
                                "model": "gpt-4o",
                            },
                            {
                                "name": "bob",
                                "channels": {"can_send": ["team_chat"]},
                            },
                        ],
                        "root": {"model": "gpt-4o"},
                    }
                }
            )
        )
        cfg = load_terrarium_config(path)
        assert cfg.name == "team"
        assert len(cfg.creatures) == 2
        assert cfg.channels[0].channel_type == "broadcast"
        assert cfg.root is not None

    def test_load_from_directory(self, tmp_path):
        path = tmp_path / "terrarium.yaml"
        path.write_text(yaml.safe_dump({"name": "t"}))
        cfg = load_terrarium_config(tmp_path)
        assert cfg.name == "t"

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_terrarium_config(tmp_path / "nope")

    def test_top_level_without_terrarium_wrapper(self, tmp_path):
        # YAML without the ``terrarium:`` key still works.
        path = tmp_path / "terrarium.yaml"
        path.write_text(yaml.safe_dump({"name": "direct"}))
        cfg = load_terrarium_config(path)
        assert cfg.name == "direct"

    def test_empty_yaml_returns_defaults(self, tmp_path):
        path = tmp_path / "terrarium.yaml"
        path.write_text("")
        cfg = load_terrarium_config(path)
        # default name is "terrarium".
        assert cfg.name == "terrarium"
