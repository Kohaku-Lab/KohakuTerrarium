"""Unit tests for :mod:`kohakuterrarium.builtins.tools.app.hosts`."""

import pytest

from kohakuterrarium.builtins.tools.app.hosts import PRESETS, parse_allowed_hosts


def test_default_is_the_common_preset_with_fonts_and_tailwind():
    hosts = parse_allowed_hosts(None)
    assert hosts == tuple(sorted(PRESETS["common"]))
    assert {"cdn.jsdelivr.net", "esm.sh", "fonts.gstatic.com"} <= set(hosts)
    assert "cdn.tailwindcss.com" in hosts


def test_empty_list_means_no_network():
    assert parse_allowed_hosts([]) == ()


def test_presets_and_hosts_mix_dedupe_and_lowercase():
    hosts = parse_allowed_hosts(["fonts", "CDN.Example.org", "fonts.gstatic.com"])
    assert hosts == ("cdn.example.org", "fonts.googleapis.com", "fonts.gstatic.com")
    assert parse_allowed_hosts("tailwind") == ("cdn.tailwindcss.com",)


@pytest.mark.parametrize(
    "bad",
    [
        "https://cdn.example.org",
        "cdn.example.org/path",
        "cdn.example.org:8443",
        "*.example.org",
        "localhost",
        "nopreset",
        "",
        "evil.com; script-src *",
        3,
    ],
)
def test_anything_but_a_bare_host_or_preset_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_allowed_hosts([bad])


def test_non_list_value_is_rejected():
    with pytest.raises(ValueError):
        parse_allowed_hosts({"common": True})
