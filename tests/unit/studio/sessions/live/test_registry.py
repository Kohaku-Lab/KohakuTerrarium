"""Unit tests for :mod:`kohakuterrarium.studio.sessions.live.registry`."""

import os

from kohakuterrarium.studio.sessions.live import registry as reg


def test_rows_are_added_updated_and_removed_by_resolved_path(tmp_path):
    live = reg.LiveSessions(tmp_path / "runtime" / "live.kvault")
    target = tmp_path / "s" / "a.kohakutr"
    row = live.add(
        target, session_id="g1", session_dir=str(tmp_path / "s"), boot_id="b1"
    )
    assert row["path"] == str(target.resolve())
    assert (row["session_id"], row["boot_id"], row["claimed_by"], row["failed"]) == (
        "g1",
        "b1",
        None,
        None,
    )
    same = tmp_path / "s" / ".." / "s" / "a.kohakutr"
    assert live.get(same) == row
    updated = live.update(same, failed="boom", claimed_by="b2")
    assert (updated["failed"], updated["claimed_by"]) == ("boom", "b2")
    readded = live.add(target, session_id="g2", session_dir="d", boot_id="b3")
    assert readded["added_at"] == row["added_at"]
    assert readded["failed"] is None and readded["session_id"] == "g2"
    assert readded["claimed_by"] == "b2"
    assert live.update(tmp_path / "missing", failed="x") is None
    assert live.remove(target) is True
    assert live.remove(target) is False
    assert live.rows() == []
    live.close()


def test_rows_come_back_oldest_first_after_reopening(tmp_path):
    file = tmp_path / "live.kvault"
    live = reg.LiveSessions(file)
    for name in ("a", "b", "c"):
        live.add(
            tmp_path / f"{name}.kohakutr", session_id=name, session_dir="", boot_id="b"
        )
    live.close()
    reopened = reg.LiveSessions(file)
    assert [r["session_id"] for r in reopened.rows()] == ["a", "b", "c"]
    reopened.close()


def test_the_process_handle_follows_the_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "one"))
    one = reg.live_sessions()
    assert one is reg.live_sessions()
    assert reg.registry_file() == tmp_path / "one" / "runtime" / "live.kvault"
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "two"))
    two = reg.live_sessions()
    assert two is not one and two.file == tmp_path / "two" / "runtime" / "live.kvault"
    reg.close_all()
    assert reg.live_sessions() is not two


def test_path_key_folds_case_where_the_filesystem_does(tmp_path):
    assert reg.path_key(tmp_path / "A.kohakutr") == os.path.normcase(
        str((tmp_path / "A.kohakutr").resolve())
    )
