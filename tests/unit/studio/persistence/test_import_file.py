"""Importing a session file from outside the session folder."""

import sqlite3

import pytest

from kohakuterrarium.studio.persistence.import_file import (
    SessionImportError,
    import_session_file,
)


def _database(path, rows=("a", "b")):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE events (body TEXT)")
    connection.executemany("INSERT INTO events VALUES (?)", [(row,) for row in rows])
    connection.commit()
    connection.close()


def _rows(path):
    connection = sqlite3.connect(path)
    try:
        return [row[0] for row in connection.execute("SELECT body FROM events")]
    finally:
        connection.close()


def test_a_session_from_elsewhere_arrives_with_its_content_and_a_fresh_name(tmp_path):
    source = tmp_path / "downloads" / "plan.kohakutr"
    source.parent.mkdir()
    _database(source)
    sessions = tmp_path / "sessions"

    name = import_session_file(source, sessions)

    assert name == "plan"
    assert _rows(sessions / "plan.kohakutr") == ["a", "b"]
    assert source.exists()

    second = import_session_file(source, sessions)
    third = import_session_file(source, sessions)
    assert (second, third) == ("plan-2", "plan-3")
    assert _rows(sessions / "plan-3.kohakutr") == ["a", "b"]


def test_a_versioned_session_of_the_same_name_is_not_overwritten(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _database(sessions / "plan.kohakutr.v2", rows=("old",))
    source = tmp_path / "plan.kohakutr"
    _database(source)

    assert import_session_file(source, sessions) == "plan-2"
    assert _rows(sessions / "plan.kohakutr.v2") == ["old"]


def test_a_write_ahead_log_is_not_lost(tmp_path):
    source = tmp_path / "wal.kohakutr"
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE events (body TEXT)")
    writer.execute("INSERT INTO events VALUES ('only-in-wal')")
    writer.commit()
    try:
        name = import_session_file(source, tmp_path / "sessions")
    finally:
        writer.close()

    assert _rows(tmp_path / "sessions" / f"{name}.kohakutr") == ["only-in-wal"]


def test_a_file_already_in_the_folder_is_left_alone(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _database(sessions / "mine.kohakutr")

    assert import_session_file(sessions / "mine.kohakutr", sessions) == "mine"
    assert sorted(path.name for path in sessions.iterdir()) == ["mine.kohakutr"]


@pytest.mark.parametrize("name", ["notes.txt", "plan.kohakutr.bak", "plan"])
def test_other_files_are_refused(tmp_path, name):
    source = tmp_path / name
    source.write_text("hello")
    with pytest.raises(SessionImportError, match="not a .kohakutr file"):
        import_session_file(source, tmp_path / "sessions")


def test_a_missing_file_or_a_folder_is_refused(tmp_path):
    with pytest.raises(SessionImportError):
        import_session_file(tmp_path / "gone.kohakutr", tmp_path / "sessions")
    folder = tmp_path / "folder.kohakutr"
    folder.mkdir()
    with pytest.raises(SessionImportError):
        import_session_file(folder, tmp_path / "sessions")


def test_a_file_that_is_not_a_database_leaves_nothing_behind(tmp_path):
    source = tmp_path / "fake.kohakutr"
    source.write_text("this is not sqlite" * 50)
    sessions = tmp_path / "sessions"

    with pytest.raises(SessionImportError, match="not a session file"):
        import_session_file(source, sessions)

    assert list(sessions.iterdir()) == []
