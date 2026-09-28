"""Bounded cursor pagination for non-destructive activity observation."""

import pytest

from kohakuterrarium.mcp_server.delegation_history import DelegationHistory


async def test_cursor_reports_eviction_and_never_replays_old_page():
    history = DelegationHistory(capacity=2)
    await history.write("first")
    await history.write_stream("second")
    page = history.page(limit=1)
    assert page["events"][0]["text"] == "first" and page["has_more"]
    await history.write("third")
    assert history.page()["truncated"]
    following = history.page(page["next_cursor"])
    assert [e["text"] for e in following["events"]] == ["second", "third"]
    assert not history.page(following["next_cursor"])["events"]
    history.on_activity_with_metadata("reasoning", "hidden", {})
    assert "hidden" not in str(history.page())


@pytest.mark.parametrize("cursor, limit", [(-1, 1), (0, 0), (0, 201), (True, 10)])
def test_invalid_cursor_or_limit_rejected(cursor, limit):
    with pytest.raises(ValueError):
        DelegationHistory().page(cursor, limit)
