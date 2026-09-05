"""Canonical tool identities and their legacy UI labels."""

import pytest

from kohakuterrarium.core.job_label import canonical_tool_name, make_job_label


@pytest.mark.parametrize("name", ["web_search", "mcp__server_tool", "read-file"])
def test_canonical_name_round_trips_display_label(name):
    canonical, label = make_job_label(f"{name}_abc123ff")
    assert canonical == name
    assert canonical_tool_name(label) == name
    assert canonical_tool_name(name) == name


@pytest.mark.parametrize("name", ["tool[custom]", "tool[abc12]", "tool_suffix", ""])
def test_unrecognized_names_remain_unchanged(name):
    assert canonical_tool_name(name) == name
