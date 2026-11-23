"""Unit tests for kohakuterrarium.browser.options."""

import dataclasses
from pathlib import Path

import pytest

from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.policy import NetworkPolicy
from kohakuterrarium.errors import ConfigError


def test_defaults_from_empty_and_none():
    for data in ({}, None):
        opts = BrowserOptions.from_mapping(data)
        assert opts == BrowserOptions()
    opts = BrowserOptions()
    assert opts.headless is True
    assert opts.channel is None and opts.executable_path is None
    assert opts.viewport == (1280, 800)
    assert opts.profile_dir is None
    assert opts.allow_hosts == () and opts.block_hosts == ()
    assert opts.allow_loopback is False and opts.allow_private is False
    assert dict(opts.secrets) == {}
    assert opts.nav_timeout_s == 30.0
    assert opts.action_timeout_s == 5.0
    assert opts.snapshot_max_chars == 20000
    assert opts.idle_close_s == 600.0


def test_full_mapping_is_normalised():
    opts = BrowserOptions.from_mapping(
        {
            "headless": False,
            "channel": "chrome",
            "executable_path": Path("/opt/chrome"),
            "viewport": " 1920 X 1080 ",
            "profile_dir": "~/profile",
            "allow_hosts": ["Example.COM.", "*.Bücher.example:443", "example.com"],
            "block_hosts": ("ads.example.com",),
            "allow_loopback": True,
            "allow_private": True,
            "secrets": {"bank_pw": "env:BANK_PW"},
            "nav_timeout_s": 10,
            "action_timeout_s": 2.5,
            "snapshot_max_chars": 5000,
            "idle_close_s": 60,
        }
    )
    assert opts.headless is False
    assert opts.channel == "chrome"
    assert opts.executable_path == str(Path("/opt/chrome"))
    assert opts.viewport == (1920, 1080)
    assert opts.profile_dir == "~/profile"
    assert opts.allow_hosts == ("example.com", "*.xn--bcher-kva.example:443")
    assert opts.block_hosts == ("ads.example.com",)
    assert opts.allow_loopback is True and opts.allow_private is True
    assert dict(opts.secrets) == {"bank_pw": "env:BANK_PW"}
    assert opts.nav_timeout_s == 10.0 and isinstance(opts.nav_timeout_s, float)
    assert opts.action_timeout_s == 2.5
    assert opts.snapshot_max_chars == 5000
    assert opts.idle_close_s == 60.0


def test_explicit_none_keeps_optional_text_unset():
    opts = BrowserOptions.from_mapping(
        {"channel": None, "executable_path": None, "profile_dir": None}
    )
    assert (opts.channel, opts.executable_path, opts.profile_dir) == (None, None, None)


def test_viewport_list_form():
    assert BrowserOptions.from_mapping({"viewport": [800, 600]}).viewport == (800, 600)
    assert BrowserOptions.from_mapping({"viewport": (800, 600)}).viewport == (800, 600)


def test_options_are_frozen_and_secrets_read_only():
    source = {"a": "env:A"}
    opts = BrowserOptions.from_mapping({"secrets": source})
    source["b"] = "env:B"
    assert dict(opts.secrets) == {"a": "env:A"}
    with pytest.raises(TypeError):
        opts.secrets["c"] = "env:C"
    with pytest.raises(dataclasses.FrozenInstanceError):
        opts.headless = False


BAD = [
    ({"bogus": 1}, "bogus"),
    ({"headless": "yes"}, "headless"),
    ({"headless": 1}, "headless"),
    ({"allow_loopback": None}, "allow_loopback"),
    ({"channel": ""}, "channel"),
    ({"channel": 5}, "channel"),
    ({"executable_path": "  "}, "executable_path"),
    ({"profile_dir": 3}, "profile_dir"),
    ({"viewport": "1280"}, "viewport"),
    ({"viewport": "axb"}, "viewport"),
    ({"viewport": [1280]}, "viewport"),
    ({"viewport": [1280, 800, 1]}, "viewport"),
    ({"viewport": [True, 800]}, "viewport"),
    ({"viewport": [1280.5, 800]}, "viewport"),
    ({"viewport": [100, 800]}, "viewport"),
    ({"viewport": [1280, 10]}, "viewport"),
    ({"viewport": [100000, 800]}, "viewport"),
    ({"viewport": 1280}, "viewport"),
    ({"allow_hosts": "example.com"}, "allow_hosts"),
    ({"allow_hosts": ["bad host"]}, "allow_hosts"),
    ({"allow_hosts": [3]}, "allow_hosts"),
    ({"block_hosts": ["*"]}, "block_hosts"),
    ({"secrets": ["env:A"]}, "secrets"),
    ({"secrets": {"a": "B"}}, "secrets"),
    ({"secrets": {"a": "file:/x"}}, "secrets"),
    ({"secrets": {"a": 3}}, "secrets"),
    ({"secrets": {"": "env:A"}}, "secrets"),
    ({"secrets": {"bad name": "env:A"}}, "secrets"),
    ({"nav_timeout_s": 0}, "nav_timeout_s"),
    ({"nav_timeout_s": -1}, "nav_timeout_s"),
    ({"nav_timeout_s": float("nan")}, "nav_timeout_s"),
    ({"nav_timeout_s": float("inf")}, "nav_timeout_s"),
    ({"nav_timeout_s": "30"}, "nav_timeout_s"),
    ({"nav_timeout_s": True}, "nav_timeout_s"),
    ({"nav_timeout_s": 100000}, "nav_timeout_s"),
    ({"action_timeout_s": 0}, "action_timeout_s"),
    ({"idle_close_s": 0}, "idle_close_s"),
    ({"snapshot_max_chars": 0}, "snapshot_max_chars"),
    ({"snapshot_max_chars": 100}, "snapshot_max_chars"),
    ({"snapshot_max_chars": 5000.0}, "snapshot_max_chars"),
    ({"snapshot_max_chars": 10**9}, "snapshot_max_chars"),
]


@pytest.mark.parametrize(("data", "key"), BAD)
def test_bad_values_raise_config_error_naming_the_key(data, key):
    with pytest.raises(ConfigError) as info:
        BrowserOptions.from_mapping(data)
    assert key in str(info.value)


def test_non_mapping_is_rejected():
    with pytest.raises(ConfigError):
        BrowserOptions.from_mapping(["headless"])


def test_network_policy_reflects_options():
    opts = BrowserOptions.from_mapping(
        {
            "allow_hosts": ["*.example.com"],
            "block_hosts": ["bad.example.com"],
            "allow_loopback": True,
        }
    )
    policy = opts.network_policy()
    assert isinstance(policy, NetworkPolicy)
    assert policy.check_host("www.example.com", 443).allowed
    assert policy.check_host("bad.example.com", 443).code == "blocked_host"
    assert policy.check_host("other.org", 443).code == "not_allowed_host"
    assert policy.check_address("127.0.0.1").allowed
    assert policy.check_address("10.0.0.1").code == "private"

    gated = opts.network_policy(external_check=lambda host, port: "sandbox")
    assert gated.check_host("www.example.com", 443).code == "external"
