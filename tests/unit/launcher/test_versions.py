"""versions: ordering of release version strings."""

import pytest

from kohakuterrarium.launcher.versions import is_newer, version_key


@pytest.mark.parametrize(
    "newer, older",
    [
        ("2.2.1.dev20261031120000+abc", "2.0.0.dev20260520092822+45856af"),
        ("2.2.1", "2.2.1rc1"),
        ("2.2.1rc1", "2.2.1b2"),
        ("2.2.1b1", "2.2.1a9"),
        ("2.2.1a1", "2.2.1.dev99999999999999"),
        ("2.2.1.dev20261101", "2.2.1.dev20261031"),
        ("2.10.0", "2.9.9"),
        ("2.2.1", "2.2"),
    ],
)
def test_newer_orders_before_older(newer, older):
    assert is_newer(newer, older)
    assert not is_newer(older, newer)


def test_equal_versions_are_not_newer():
    assert not is_newer("2.2.0", "2.2")
    assert not is_newer("2.2.1.dev5+x", "2.2.1.dev5+y")


@pytest.mark.parametrize("text", ["bundled", "", "latest"])
def test_unparseable_versions_never_win(text):
    assert version_key(text) is None
    assert not is_newer(text, "1.0.0")
    assert not is_newer("1.0.0", text)
