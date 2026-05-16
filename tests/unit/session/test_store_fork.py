"""Unit tests for :mod:`kohakuterrarium.session.store_fork`."""

import pytest

from kohakuterrarium.session.errors import ForkNotStableError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store_fork import (
    _build_lineage,
    _call_id_for,
    _child_session_id,
    _collect_copy_range,
    _copy_artifacts,
    _copy_table,
    _decode_key,
    _record_child_in_parent,
    check_fork_stability,
    perform_fork,
)

# ── helpers ───────────────────────────────────────────────────────


def _store(tmp_path, name="src.kohakutr") -> SessionStore:
    return SessionStore(str(tmp_path / name))


# ── small helpers ─────────────────────────────────────────────────


class TestDecodeKey:
    def test_bytes(self):
        assert _decode_key(b"x") == "x"

    def test_str(self):
        assert _decode_key("x") == "x"


class TestCallIdFor:
    def test_prefers_call_id(self):
        assert _call_id_for({"call_id": "c1", "job_id": "j1"}) == "c1"

    def test_falls_back_to_job_id(self):
        assert _call_id_for({"job_id": "j1"}) == "j1"

    def test_empty_when_neither(self):
        assert _call_id_for({}) == ""


class TestChildSessionId:
    def test_format(self):
        cid = _child_session_id("parent-1")
        assert cid.startswith("parent-1-fork-")
        assert len(cid.split("-fork-")[-1]) == 8


# ── check_fork_stability ──────────────────────────────────────────


class TestCheckForkStability:
    def test_empty_range_is_stable(self):
        check_fork_stability([], at_event_id=1)

    def test_closed_span_is_stable(self):
        events = [
            ("k1", {"type": "tool_call", "call_id": "c1"}),
            ("k2", {"type": "tool_result", "call_id": "c1"}),
        ]
        # No pending: stable.
        check_fork_stability(events, at_event_id=2)

    def test_open_span_not_in_pending_is_stable(self):
        # Tool started, no result in range — but pending set doesn't
        # contain it (so it finished after the cut).
        events = [("k1", {"type": "tool_call", "call_id": "c1"})]
        check_fork_stability(events, at_event_id=1, pending_job_ids=set())

    def test_open_span_in_pending_raises(self):
        events = [("k1", {"type": "tool_call", "call_id": "c1"})]
        with pytest.raises(ForkNotStableError, match="c1"):
            check_fork_stability(events, at_event_id=1, pending_job_ids={"c1"})

    def test_open_subagent_span_in_pending_raises(self):
        events = [("k1", {"type": "subagent_call", "job_id": "j1"})]
        with pytest.raises(ForkNotStableError, match="j1"):
            check_fork_stability(events, at_event_id=1, pending_job_ids={"j1"})

    def test_calls_without_id_skipped(self):
        events = [("k1", {"type": "tool_call"})]
        # No ID → cannot match, treated as fine.
        check_fork_stability(events, at_event_id=1, pending_job_ids={"x"})


# ── _build_lineage ───────────────────────────────────────────────


class TestBuildLineage:
    def test_basic(self):
        out = _build_lineage(
            parent_meta={"format_version": 2},
            parent_session_id="parent",
            at_event_id=5,
            mutate=None,
            created_at="2025-01-01T00:00:00",
        )
        assert "fork" in out
        assert out["fork"]["parent_session_id"] == "parent"
        assert out["fork"]["fork_point"] == 5
        assert out["fork"]["parent_format_version"] == 2
        # No mutation → label is None.
        assert out["fork"]["fork_mutation"] is None

    def test_named_mutate(self):
        def renamer(e):
            return e

        out = _build_lineage(
            parent_meta={},
            parent_session_id="p",
            at_event_id=1,
            mutate=renamer,
            created_at="now",
        )
        assert out["fork"]["fork_mutation"] == "renamer"

    def test_preserves_prior_lineage(self):
        out = _build_lineage(
            parent_meta={"lineage": {"migration": "v1_to_v2"}},
            parent_session_id="p",
            at_event_id=1,
            mutate=None,
            created_at="now",
        )
        assert out["migration"] == "v1_to_v2"
        assert "fork" in out


# ── _record_child_in_parent ──────────────────────────────────────


class TestRecordChildInParent:
    def test_appends_first_child(self, tmp_path):
        s = _store(tmp_path)
        try:
            _record_child_in_parent(
                s.meta,
                child_session_id="c1",
                child_path="/some/path",
                at_event_id=5,
                created_at="t",
            )
            children = s.meta["forked_children"]
            assert len(children) == 1
            assert children[0]["session_id"] == "c1"
        finally:
            s.close()

    def test_appends_to_existing_list(self, tmp_path):
        s = _store(tmp_path)
        try:
            _record_child_in_parent(s.meta, "c1", "/p1", 5, "t")
            _record_child_in_parent(s.meta, "c2", "/p2", 6, "t2")
            assert len(s.meta["forked_children"]) == 2
        finally:
            s.close()

    def test_corrupt_existing_value_replaced_with_fresh_list(self, tmp_path):
        # If ``forked_children`` is somehow not a list (corrupt meta),
        # the recorder discards it and starts a fresh single-item list.
        s = _store(tmp_path)
        try:
            s.meta["forked_children"] = "not-a-list"
            _record_child_in_parent(s.meta, "c1", "/p1", 5, "t")
            children = s.meta["forked_children"]
            assert isinstance(children, list)
            assert len(children) == 1
            assert children[0]["session_id"] == "c1"
        finally:
            s.close()


# ── perform_fork: bad inputs ──────────────────────────────────────


class TestPerformForkBadInputs:
    def test_invalid_event_id(self, tmp_path):
        s = _store(tmp_path)
        try:
            with pytest.raises(ValueError, match=">= 1"):
                perform_fork(s, str(tmp_path / "child.kohakutr"), at_event_id=0)
        finally:
            s.close()

    def test_no_events_in_range(self, tmp_path):
        s = _store(tmp_path)
        try:
            # Empty store — no events.
            with pytest.raises(ValueError, match="No events"):
                perform_fork(s, str(tmp_path / "child.kohakutr"), at_event_id=5)
        finally:
            s.close()

    def test_target_already_exists(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "user_message", {"content": "hi"})
            s.flush()
            target = tmp_path / "child.kohakutr"
            target.write_bytes(b"")
            with pytest.raises(FileExistsError):
                perform_fork(s, str(target), at_event_id=1)
        finally:
            s.close()

    def test_unstable_fork_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "tool_call", {"call_id": "c1", "name": "bash"})
            s.flush()
            with pytest.raises(ForkNotStableError):
                perform_fork(
                    s,
                    str(tmp_path / "child.kohakutr"),
                    at_event_id=1,
                    pending_job_ids={"c1"},
                )
        finally:
            s.close()


# ── perform_fork: happy paths ─────────────────────────────────────


class TestPerformForkHappy:
    def test_basic_fork(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("parent", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "user_message", {"content": "hi"})
            s.append_event("alice", "text_chunk", {"content": "ok"})
            s.flush()
            child = perform_fork(s, str(tmp_path / "child.kohakutr"), at_event_id=2)
            try:
                # Child has 2 events.
                evts = child.get_events("alice")
                assert len(evts) == 2
                # Lineage stamped.
                meta = child.load_meta()
                assert meta["lineage"]["fork"]["parent_session_id"] == "parent"
                assert meta["status"] == "paused"
            finally:
                child.close(update_status=False)
        finally:
            s.close()

    def test_events_beyond_fork_excluded(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "x", {})  # eid=1
            s.append_event("alice", "x", {})  # eid=2
            s.append_event("alice", "x", {})  # eid=3
            s.flush()
            child = perform_fork(s, str(tmp_path / "child.kohakutr"), at_event_id=2)
            try:
                evts = child.get_events("alice")
                assert len(evts) == 2
                # All copied events have event_id <= 2.
                assert all(e["event_id"] <= 2 for e in evts)
            finally:
                child.close(update_status=False)
        finally:
            s.close()

    def test_fork_skips_malformed_events_in_scan(self, tmp_path):
        """Contract: the fork range-scan tolerates a corrupt events
        table — an event value that isn't a dict, and a dict event with
        a non-int ``event_id``, are both skipped, while the well-formed
        events still fork. Drives the malformed-event guard branches in
        ``_collect_copy_range`` (a corrupt ``.kohakutr`` must not crash
        a fork)."""
        s = _store(tmp_path)
        try:
            s.append_event("alice", "user_message", {"content": "good"})
            s.flush()
            # Inject corruption directly into the events table: a
            # non-dict value, and a dict with no usable event_id.
            s.events["alice:not-a-dict"] = ["junk"]
            s.events["alice:no-event-id"] = {"type": "x", "event_id": "oops"}
            s.events.flush_cache()

            child = perform_fork(s, str(tmp_path / "child.kohakutr"), at_event_id=1)
            try:
                evts = child.get_events("alice")
                # Only the one well-formed event survived the fork.
                assert len(evts) == 1
                assert evts[0]["content"] == "good"
            finally:
                child.close(update_status=False)
        finally:
            s.close()

    def test_mutate_rewrites_fork_event(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "user_message", {"content": "old"})
            s.flush()

            def rewrite(evt):
                evt["content"] = "new"
                return evt

            child = perform_fork(
                s,
                str(tmp_path / "child.kohakutr"),
                at_event_id=1,
                mutate=rewrite,
            )
            try:
                evts = child.get_events("alice")
                assert evts[0]["content"] == "new"
            finally:
                child.close(update_status=False)
        finally:
            s.close()

    def test_mutate_returning_none_drops_event(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "user_message", {"content": "first"})
            s.append_event("alice", "user_message", {"content": "second"})
            s.flush()

            child = perform_fork(
                s,
                str(tmp_path / "child.kohakutr"),
                at_event_id=2,
                mutate=lambda evt: None,
            )
            try:
                # The fork-point event (eid=2) was dropped.
                evts = child.get_events("alice")
                assert len(evts) == 1
                assert evts[0]["content"] == "first"
            finally:
                child.close(update_status=False)
        finally:
            s.close()

    def test_mutate_bad_return_type_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "user_message", {"content": "x"})
            s.flush()
            with pytest.raises(TypeError):
                perform_fork(
                    s,
                    str(tmp_path / "child.kohakutr"),
                    at_event_id=1,
                    mutate=lambda evt: "not-a-dict",
                )
        finally:
            s.close()

    def test_mutate_raises_wrapped(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "user_message", {"content": "x"})
            s.flush()

            def boom(evt):
                raise ValueError("inner")

            with pytest.raises(RuntimeError, match="mutate callable"):
                perform_fork(
                    s,
                    str(tmp_path / "child.kohakutr"),
                    at_event_id=1,
                    mutate=boom,
                )
        finally:
            s.close()

    def test_records_in_parent_forked_children(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "x", {})
            s.flush()
            child = perform_fork(s, str(tmp_path / "child.kohakutr"), at_event_id=1)
            child.close(update_status=False)
            children = s.meta["forked_children"]
            assert len(children) == 1
        finally:
            s.close()

    def test_state_and_other_tables_copied(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "x", {})
            s.save_state("alice", scratchpad={"k": "v"}, turn_count=3)
            s.save_channel_message("ch", {"content": "hello world"})
            s.flush()
            child = perform_fork(s, str(tmp_path / "child.kohakutr"), at_event_id=1)
            try:
                assert child.load_scratchpad("alice") == {"k": "v"}
                assert child.load_turn_count("alice") == 3
                assert child.get_channel_messages("ch")[0]["content"] == ("hello world")
            finally:
                child.close(update_status=False)
        finally:
            s.close()

    def test_parent_forked_children_not_inherited_by_child(self, tmp_path):
        # ``forked_children`` is parent-local bookkeeping — when the
        # parent already has one (from a prior fork), a *new* fork's
        # child must NOT inherit the parent's forked_children list.
        s = _store(tmp_path)
        try:
            s.init_meta("parent", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "x", {})
            s.flush()
            # First fork stamps forked_children onto the parent.
            child1 = perform_fork(s, str(tmp_path / "child1.kohakutr"), at_event_id=1)
            child1.close(update_status=False)
            assert "forked_children" in s.meta
            # Second fork: the new child's meta must omit the parent's
            # forked_children entirely.
            child2 = perform_fork(s, str(tmp_path / "child2.kohakutr"), at_event_id=1)
            try:
                child2_meta = child2.load_meta()
                assert "forked_children" not in child2_meta
            finally:
                child2.close(update_status=False)
        finally:
            s.close()


# ── _copy_artifacts ──────────────────────────────────────────────


class TestCopyArtifacts:
    def test_missing_source_noop(self, tmp_path):
        source = tmp_path / "src.kohakutr"
        dest = tmp_path / "dst.kohakutr"
        dest.write_bytes(b"")
        # No raise.
        _copy_artifacts(source, dest)

    def test_copies_files(self, tmp_path):
        source = tmp_path / "src.kohakutr"
        source.write_bytes(b"")
        art = tmp_path / "src.artifacts"
        art.mkdir()
        (art / "a.png").write_bytes(b"data")
        dest = tmp_path / "dst.kohakutr.v2"
        dest.write_bytes(b"")
        _copy_artifacts(source, dest)
        # Destination artifacts dir created with the file.
        dest_art = tmp_path / "dst.kohakutr.artifacts"
        assert (dest_art / "a.png").read_bytes() == b"data"

    def test_copies_subdirs(self, tmp_path):
        source = tmp_path / "src.kohakutr"
        source.write_bytes(b"")
        art = tmp_path / "src.artifacts"
        sub = art / "sub"
        sub.mkdir(parents=True)
        (sub / "b.png").write_bytes(b"nested")
        dest = tmp_path / "dst.kohakutr.v2"
        dest.write_bytes(b"")
        _copy_artifacts(source, dest)
        dest_art = tmp_path / "dst.kohakutr.artifacts"
        assert (dest_art / "sub" / "b.png").read_bytes() == b"nested"

    def test_pre_existing_target_artifact_is_not_overwritten(self, tmp_path):
        # If the child already has an artifact with the same name, the
        # copy skips it — the existing child file wins.
        from kohakuterrarium.session.artifacts import artifacts_dir_for

        source = tmp_path / "src.kohakutr"
        source.write_bytes(b"")
        art = tmp_path / "src.artifacts"
        art.mkdir()
        (art / "shared.png").write_bytes(b"parent-bytes")
        dest = tmp_path / "dst.kohakutr.v2"
        dest.write_bytes(b"")
        # Pre-seed the child's artifacts dir with a same-named file.
        dest_art = artifacts_dir_for(dest)
        dest_art.mkdir(parents=True, exist_ok=True)
        (dest_art / "shared.png").write_bytes(b"child-bytes")
        _copy_artifacts(source, dest)
        # The pre-existing child file is preserved, not clobbered.
        assert (dest_art / "shared.png").read_bytes() == b"child-bytes"


# ── _copy_table defensive row handling ───────────────────────────


class TestCopyTable:
    def test_missing_key_is_skipped(self, tmp_path):
        # When an explicit key list names a key absent from the source,
        # _copy_table skips it (KeyError) and copies the rest.
        src = _store(tmp_path, "src.kohakutr")
        dst = _store(tmp_path, "dst.kohakutr")
        try:
            src.state["alice:real"] = {"v": 1}
            src.flush()
            written = _copy_table(
                src.state, dst.state, keys=["alice:real", "alice:ghost"]
            )
            # Only the existing key copied.
            assert written == 1
            assert dst.state["alice:real"] == {"v": 1}
        finally:
            src.close()
            dst.close()

    def test_read_failure_is_skipped(self, tmp_path):
        # A row whose read raises (non-KeyError) is logged + skipped;
        # _copy_table still returns the count of good rows.
        src = _store(tmp_path, "src.kohakutr")
        dst = _store(tmp_path, "dst.kohakutr")
        try:
            src.state["alice:good"] = {"v": 1}
            src.flush()

            class _FlakySrc:
                def __init__(self, inner):
                    self._inner = inner

                def keys(self, **kw):
                    return self._inner.keys(**kw)

                def __getitem__(self, key):
                    k = key.decode() if isinstance(key, bytes) else key
                    if k == "alice:bad":
                        raise RuntimeError("read exploded")
                    return self._inner[key]

            written = _copy_table(
                _FlakySrc(src.state),
                dst.state,
                keys=["alice:good", "alice:bad"],
            )
            assert written == 1
            assert dst.state["alice:good"] == {"v": 1}
        finally:
            src.close()
            dst.close()


# ── _collect_copy_range defensive scan ───────────────────────────


class TestCollectCopyRange:
    def test_unreadable_event_skipped_during_scan(self, tmp_path):
        # An event row that raises on read is skipped — the surrounding
        # good events still land in the copy range.
        s = _store(tmp_path)
        try:
            _, e1 = s.append_event("alice", "x", {"n": 1})
            _, e2 = s.append_event("alice", "x", {"n": 2})
            s.flush()
            # The first event's storage key (whatever its padded form).
            all_keys = [
                k.decode() if isinstance(k, bytes) else k
                for k in s.events.keys(limit=2**31 - 1)
            ]
            bad_key = sorted(all_keys)[0]

            class _FlakyEvents:
                def __init__(self, inner):
                    self._inner = inner

                def flush_cache(self):
                    return self._inner.flush_cache()

                def keys(self, **kw):
                    return self._inner.keys(**kw)

                def __getitem__(self, key):
                    k = key.decode() if isinstance(key, bytes) else key
                    if k == bad_key:
                        raise RuntimeError("event read exploded")
                    return self._inner[key]

            s.events = _FlakyEvents(s.events)
            in_range, fork_point = _collect_copy_range(s, at_event_id=e2)
            # The first (unreadable) event is dropped; the second survives.
            ids = [evt.get("event_id") for _, evt in in_range]
            assert e1 not in ids
            assert e2 in ids
        finally:
            s._closed = True
