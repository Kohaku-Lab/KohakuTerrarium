"""Unit tests for ``kohakuterrarium.browser.tabs``: ids, active tab, opened tabs."""

import pytest

from kohakuterrarium.browser.errors import NoTabError
from kohakuterrarium.browser.tabs import TabRegistry


class Page:
    url = "about:blank"


def test_ids_count_up_and_are_never_reused():
    tabs = TabRegistry()
    a, b = Page(), Page()
    assert tabs.add(a).id == "t1" and tabs.add(b).id == "t2"
    assert tabs.add(a).id == "t1"
    tabs.remove(b)
    assert tabs.add(Page()).id == "t3"
    tabs.clear()
    assert len(tabs) == 0 and tabs.active is None
    assert tabs.add(Page()).id == "t4"


def test_newest_is_active_and_closing_it_returns_to_the_last_active():
    tabs = TabRegistry()
    pages = [Page() for _ in range(3)]
    for page in pages:
        tabs.add(page)
    tabs.select("t1")
    tabs.select("t3")
    assert tabs.active.id == "t3"
    tabs.remove(pages[2])
    assert tabs.active.id == "t1"
    tabs.remove(pages[0])
    assert tabs.active.id == "t2"
    tabs.remove(pages[1])
    assert tabs.active is None
    assert tabs.remove(pages[1]) is None


def test_get_without_tabs_and_unknown_ids():
    tabs = TabRegistry()
    with pytest.raises(NoTabError, match="no tab is open; open one with"):
        tabs.get()
    tabs.add(Page())
    with pytest.raises(NoTabError, match=r"no tab 't7' \(open tabs: t1\)"):
        tabs.get("t7")
    assert tabs.get("t1") is tabs.get()


def test_opened_by_page_is_reported_once_and_only_while_open():
    tabs = TabRegistry()
    tabs.add(Page())
    popup, gone = Page(), Page()
    tabs.add(popup, opened_by_page=True)
    tabs.add(gone, opened_by_page=True)
    tabs.remove(gone)
    assert [t.id for t in tabs.drain_opened()] == ["t2"]
    assert tabs.drain_opened() == []
    assert [t.id for t in tabs] == ["t1", "t2"]
    assert tabs.find(popup).id == "t2" and tabs.find(gone) is None
