"""Retired-file routing is published only after destination durability."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.readonly_view import SessionReadView
from kohakuterrarium.session.resume_target import resolve_resume_path
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.session_successor import (
    publish_successors,
    stage_successor,
)


def test_pending_unowned_source_uses_live_members_and_survives_failed_checkpoint(
    tmp_path, monkeypatch
):
    source = SessionStore(tmp_path / "old.kohakutr")
    target = SessionStore(tmp_path / "current.kohakutr")
    source.init_meta("old", "agent", "/cfg", str(tmp_path), ["alice", "ghost"])
    target.init_meta("current", "agent", "/cfg", str(tmp_path), ["alice"])
    source.meta["live_graph_manifest"] = {"creatures": [{"name": "alice"}]}
    engine = SimpleNamespace(
        _session_stores={"current": target},
        _creatures={},
        _topology=SimpleNamespace(
            graphs={"current": SimpleNamespace(creature_ids=set())}
        ),
    )
    try:
        stage_successor(engine, source, [target])
        marker = source.meta["resume_successor"]
        assert marker["agents"] == ["alice"]
        assert marker["state"] == "pending"
        assert not source._closed
        with monkeypatch.context() as patch:
            patch.setattr(
                target,
                "checkpoint",
                lambda: (_ for _ in ()).throw(OSError("disk full")),
            )
            with pytest.raises(OSError, match="disk full"):
                publish_successors(engine, "current")
        with pytest.raises(SessionNotResumableError, match="pending"):
            resolve_resume_path(source.path)
        publish_successors(engine, "current")
        assert source.meta["resume_successor"]["state"] == "ready"
        assert resolve_resume_path(source.path) == tmp_path / "current.kohakutr"
        assert not source._closed
    finally:
        source.close(update_status=False)
        target.close(update_status=False)


def test_legacy_destinations_receive_durable_distinct_successor_identities(tmp_path):
    source = SessionStore(tmp_path / "parent.kohakutr")
    targets = [SessionStore(tmp_path / f"child-{i}.kohakutr") for i in range(2)]
    engine = SimpleNamespace()
    try:
        stage_successor(engine, source, targets, kind="split")
        refs = source.meta["resume_successor"]["targets"]
        assert all(ref["conversation_id"] for ref in refs)
        assert len({ref["conversation_id"] for ref in refs}) == 2
        for target, ref in zip(targets, refs):
            assert target.meta["conversation_id"] == ref["conversation_id"]
            with SessionReadView(target.path) as view:
                assert view.get("meta", "conversation_id") == ref["conversation_id"]
    finally:
        source.close(update_status=False)
        for target in targets:
            target.close(update_status=False)
