"""The registry knows which session files a live store holds open in this process."""

import gc
from concurrent.futures import ThreadPoolExecutor

from kohakuterrarium.session.open_registry import live_store_for
from kohakuterrarium.session.store import SessionStore


def open_store(path):
    store = SessionStore(str(path))
    store.init_meta("sid", "agent", "", "", ["alice"])
    return store


class TestOpenRegistry:
    def test_open_store_is_found_under_any_spelling_until_it_closes(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "a.kohakutr"
        store = open_store(path)
        assert live_store_for(path) is store
        assert live_store_for(path.as_uri()) is store
        monkeypatch.chdir(tmp_path)
        assert live_store_for("a.kohakutr") is store
        assert live_store_for(tmp_path / "other.kohakutr") is None

        store.close()
        assert live_store_for(path) is None

    def test_store_is_registered_while_opening_and_ready_only_afterwards(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "opening.kohakutr"
        seen = {}
        real_open = SessionStore._open_tables

        def spy(self):
            seen["registered"] = live_store_for(path) is self
            seen["ready"] = self.is_ready
            real_open(self)

        monkeypatch.setattr(SessionStore, "_open_tables", spy)
        store = SessionStore(str(path))
        assert seen == {"registered": True, "ready": False}
        assert store.is_ready
        store.close()

    def test_closing_one_of_two_handles_keeps_the_other_registered(self, tmp_path):
        path = tmp_path / "shared.kohakutr"
        first = open_store(path)
        second = SessionStore(str(path))
        first.close()
        assert live_store_for(path) is second
        second.close()
        assert live_store_for(path) is None

    def test_failed_open_leaves_nothing_registered(self, tmp_path, monkeypatch):
        path = tmp_path / "broken.kohakutr"

        def explode(self):
            raise RuntimeError("cannot open tables")

        monkeypatch.setattr(SessionStore, "_open_tables", explode)
        try:
            SessionStore(str(path))
        except RuntimeError:
            pass
        assert live_store_for(path) is None

    def test_unclosed_store_that_is_collected_is_not_kept_alive(self, tmp_path):
        path = tmp_path / "leaked.kohakutr"
        store = open_store(path)
        store.close()
        del store
        gc.collect()
        assert live_store_for(path) is None

    def test_concurrent_open_and_close_leaves_an_empty_registry(self, tmp_path):
        paths = [tmp_path / f"s{i}.kohakutr" for i in range(8)]

        def cycle(path):
            for _ in range(5):
                store = open_store(path)
                assert live_store_for(path) is store
                store.close()

        with ThreadPoolExecutor(8) as pool:
            list(pool.map(cycle, paths))
        assert [live_store_for(path) for path in paths] == [None] * 8
