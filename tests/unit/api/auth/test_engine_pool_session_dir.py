"""Unit tests for :func:`kohakuterrarium.api.auth.engine_pool.user_id_for_session_dir`."""

from kohakuterrarium.api.auth.engine_pool import (
    _user_session_dir,
    user_id_for_session_dir,
)
from kohakuterrarium.utils.config_dir import config_dir


def test_inverts_every_pool_directory():
    assert user_id_for_session_dir(_user_session_dir(None)) == (True, None)
    assert user_id_for_session_dir(_user_session_dir(42)) == (True, 42)
    assert user_id_for_session_dir(str(_user_session_dir(3))) == (True, 3)


def test_rejects_directories_the_pool_never_makes(tmp_path):
    assert user_id_for_session_dir(tmp_path / "sessions") == (False, None)
    assert user_id_for_session_dir(config_dir() / "users" / "bob" / "sessions") == (
        False,
        None,
    )
    assert user_id_for_session_dir(config_dir() / "users" / "4" / "other") == (
        False,
        None,
    )
    assert user_id_for_session_dir(config_dir() / "elsewhere" / "4" / "sessions") == (
        False,
        None,
    )
