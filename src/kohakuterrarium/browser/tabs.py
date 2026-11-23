"""Tab bookkeeping: stable ids, the active tab, and tabs opened since the last result."""

from dataclasses import dataclass, field

from kohakuterrarium.browser.errors import NoTabError
from kohakuterrarium.browser.protocol import PageDriver

NO_TAB_HINT = "open one with browser_tabs new or browser_navigate"


@dataclass(eq=False)
class Tab:
    """One page with its session id and the document id its refs were recorded on."""

    id: str
    page: PageDriver
    doc_id: object = None
    opened_by_page: bool = False
    notes: list[str] = field(default_factory=list)


class TabRegistry:
    """Tabs ``t1``, ``t2``, … in opening order; ids are never reused.

    The most recently opened tab becomes active. Closing the active tab
    activates the most recently active remaining tab, or none.
    """

    def __init__(self) -> None:
        self._counter = 0
        self._tabs: dict[str, Tab] = {}
        self._history: list[str] = []
        self._opened: list[str] = []

    def __len__(self) -> int:
        return len(self._tabs)

    def __iter__(self):
        return iter(list(self._tabs.values()))

    @property
    def active(self) -> Tab | None:
        return self._tabs[self._history[-1]] if self._history else None

    def find(self, page: PageDriver) -> Tab | None:
        """The tab for ``page``, or ``None``."""
        return next((t for t in self._tabs.values() if t.page is page), None)

    def add(self, page: PageDriver, *, opened_by_page: bool = False) -> Tab:
        """Register ``page`` (idempotent) and make it active.

        A tab added with ``opened_by_page`` is reported once by
        :meth:`drain_opened`.
        """
        tab = self.find(page)
        if tab is None:
            self._counter += 1
            tab = Tab(f"t{self._counter}", page, opened_by_page=opened_by_page)
            self._tabs[tab.id] = tab
            if opened_by_page:
                self._opened.append(tab.id)
        self.select(tab.id)
        return tab

    def get(self, tab_id: str | None = None) -> Tab:
        """The tab ``tab_id``, or the active tab; raises ``NoTabError``."""
        if tab_id is None:
            tab = self.active
            if tab is None:
                raise NoTabError(f"no tab is open; {NO_TAB_HINT}")
            return tab
        tab = self._tabs.get(tab_id)
        if tab is None:
            known = ", ".join(self._tabs) or "none"
            raise NoTabError(f"no tab {tab_id!r} (open tabs: {known})")
        return tab

    def select(self, tab_id: str) -> Tab:
        """Make ``tab_id`` active."""
        tab = self.get(tab_id)
        if tab_id in self._history:
            self._history.remove(tab_id)
        self._history.append(tab_id)
        return tab

    def remove(self, page: PageDriver) -> Tab | None:
        """Forget the tab of ``page``; returns it, or ``None`` when unknown."""
        tab = self.find(page)
        if tab is None:
            return None
        del self._tabs[tab.id]
        self._history = [t for t in self._history if t != tab.id]
        self._opened = [t for t in self._opened if t != tab.id]
        return tab

    def drain_opened(self) -> list[Tab]:
        """Tabs a page opened since the last call, still open, oldest first."""
        opened = [self._tabs[t] for t in self._opened if t in self._tabs]
        self._opened.clear()
        return opened

    def clear(self) -> None:
        """Forget every tab; the id counter keeps counting."""
        self._tabs.clear()
        self._history.clear()
        self._opened.clear()
