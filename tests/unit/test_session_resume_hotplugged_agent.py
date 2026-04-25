"""Resume must recover creatures added via ``terrarium/hotplug.py``.

``SessionStore.init_meta`` (``session/store.py:~503``) sets
``meta["agents"]`` once at session creation. ``terrarium/hotplug.py``
appends creatures at runtime but never updates that list.
``session/resume.py:resume_terrarium`` loops over ``meta["agents"]``
when rebuilding resume data, so a creature added via hotplug disappears
on resume even though its events are still in the store.
"""

import pytest

from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def hotplug_session(tmp_path):
    """Simulate a terrarium session where a creature was hot-plugged mid-run."""
    path = tmp_path / "hotplug.kohakutr"
    store = SessionStore(path)

    # Initial terrarium — 'root' manages 'swe'.
    store.init_meta(
        session_id="hotplug",
        config_type="terrarium",
        config_path="/tmp/terrarium",
        pwd=str(tmp_path),
        agents=["root", "swe"],
        terrarium_name="team",
        terrarium_channels=[{"name": "tasks", "type": "queue"}],
        terrarium_creatures=[{"name": "swe", "listen": ["tasks"], "send": []}],
    )

    # Normal activity from the initial creatures.
    store.append_event("root", "user_input", {"content": "Plan the refactor"})
    store.append_event("root", "processing_end", {})
    store.append_event("swe", "trigger_fired", {"channel": "tasks"})
    store.append_event("swe", "processing_end", {})
    store.save_conversation("root", [{"role": "user", "content": "plan"}])
    store.save_conversation("swe", [{"role": "user", "content": "triggered"}])

    # Mid-run: a 'reviewer' creature is hot-plugged via
    # terrarium/hotplug.py:add_creature. It writes its own events and
    # conversation, but meta["agents"] is NOT updated by that code path.
    store.append_event(
        "reviewer",
        "trigger_fired",
        {"channel": "review", "content": "new PR"},
    )
    store.append_event("reviewer", "text", {"content": "LGTM with nits"})
    store.append_event("reviewer", "processing_end", {})
    store.save_conversation(
        "reviewer",
        [
            {"role": "system", "content": "You review PRs."},
            {"role": "user", "content": "review PR #42"},
            {"role": "assistant", "content": "LGTM with nits"},
        ],
    )
    store.save_state("reviewer", scratchpad={"last_pr": "42"})
    store.close()
    return path


class TestHotplugInMeta:
    """Wave B: ``load_meta`` augments ``meta["agents"]`` via event scan."""

    def test_init_meta_agents_raw_row_is_frozen(self, hotplug_session):
        """The raw ``meta["agents"]`` row written by ``init_meta`` is
        preserved; only ``load_meta`` merges discovered agents in."""
        store = SessionStore(hotplug_session)
        try:
            # Raw row from init_meta — still the original two.
            raw = store.meta["agents"]
            assert raw == ["root", "swe"]
        finally:
            store.close()

    def test_load_meta_merges_discovered_agents(self, hotplug_session):
        """Wave B: ``load_meta`` merges creatures discovered by event
        scan so resume picks up hot-plugged creatures."""
        store = SessionStore(hotplug_session)
        try:
            meta = store.load_meta()
            assert "root" in meta["agents"]
            assert "swe" in meta["agents"]
            assert "reviewer" in meta["agents"]
        finally:
            store.close()

    def test_hotplugged_creature_has_live_event_and_conversation_data(
        self, hotplug_session
    ):
        """Data for the hot-plugged creature IS in the store — just not
        indexed by meta["agents"]."""
        store = SessionStore(hotplug_session)
        try:
            events = store.get_events("reviewer")
            assert len(events) == 3
            assert events[1]["content"] == "LGTM with nits"

            convo = store.load_conversation("reviewer")
            assert convo is not None
            assert convo[-1]["content"] == "LGTM with nits"

            pad = store.load_scratchpad("reviewer")
            assert pad == {"last_pr": "42"}
        finally:
            store.close()


class TestResumeRecoversHotpluggedAgent:
    """Resume should rebuild the hot-plugged creature's state.

    ``session/resume.py:resume_terrarium`` iterates ``meta["agents"]``
    when building ``resume_data`` / ``resume_events`` /
    ``resume_triggers``. Today the reviewer creature is silently
    skipped because it never made it into that list. Wave C (or the
    final fix) is expected to either (a) update ``meta["agents"]`` on
    hot-plug, or (b) discover creatures by scanning event key
    prefixes.
    """

    def test_meta_agents_updated_after_hotplug(self, hotplug_session):
        store = SessionStore(hotplug_session)
        try:
            meta = store.load_meta()
            # Wave B fix: ``load_meta`` scans event-key prefixes and
            # merges discovered agent names into ``meta["agents"]`` so
            # creatures hot-plugged during the session (even those that
            # only land via raw ``append_event`` — not through the
            # hotplug codepath) are visible to resume.
            assert "reviewer" in meta["agents"]
        finally:
            store.close()

    def test_resume_iteration_list_covers_hotplugged_creature(self, hotplug_session):
        """The list ``resume_terrarium`` iterates today misses reviewer.

        ``session/resume.py:resume_terrarium`` does::

            for name in meta.get("agents", []):
                resume_data[name] = {...}

        The assertion below models that exact iteration. Post-fix,
        whatever authoritative set the framework uses must include
        hot-plugged creatures whose data is already on disk.
        """
        store = SessionStore(hotplug_session)
        try:
            meta = store.load_meta()
            # This is the exact expression resume_terrarium uses to
            # decide which creatures to restore. Post-fix it must
            # contain "reviewer".
            resume_iteration_names = list(meta.get("agents", []))
            assert "reviewer" in resume_iteration_names
        finally:
            store.close()
