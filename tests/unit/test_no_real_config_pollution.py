"""Regression tests for the suite-level real-config pollution guard."""

import hashlib
import os
from pathlib import Path

import pytest

from kohakuterrarium.llm.profiles.api_keys import PROVIDER_KEY_MAP, get_api_key
from tests.helpers import real_config_guard


def _assert_no_real_config_leaks(
    before: dict[str, str | None], after: dict[str, str | None]
) -> None:
    leaks = real_config_guard.find_leaks(before, after)
    daemon = real_config_guard.running_daemon_pid()
    if leaks and daemon is not None:
        pytest.skip(
            f"a KohakuTerrarium daemon is running (pid {daemon}); its writes to "
            "the real config dir cannot be distinguished from a test leak. "
            "Stop it and re-run to make this guard meaningful. Saw:\n"
            + "\n".join(leaks)
        )
    assert not leaks, (
        "tests wrote to the operator's real ~/.kohakuterrarium/ — every "
        "save path must resolve through KT_CONFIG_DIR (the conftest "
        "autouse fixture redirects to tmp_path).  Leaks:\n" + "\n".join(leaks)
    )


def test_snapshot_hashes_regular_files_and_tracks_sessions_by_path(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    session_dir = config_dir / "sessions"
    session_dir.mkdir(parents=True)
    regular = config_dir / "settings.json"
    session = session_dir / "large.kohakutr"
    regular.write_bytes(b"settings")
    session.write_bytes(b"session contents")
    monkeypatch.setattr(real_config_guard, "REAL_CONFIG_DIR", config_dir)
    monkeypatch.setattr(real_config_guard, "HASH_CHUNK_SIZE", 3)

    snapshot = real_config_guard.snapshot()

    assert snapshot[str(regular)] == hashlib.sha256(b"settings").hexdigest()
    assert snapshot[str(session)] is None


def test_session_content_changes_are_ignored_but_new_files_are_detected(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    session_dir = config_dir / "sessions"
    session_dir.mkdir(parents=True)
    existing = session_dir / "existing.kohakutr"
    regular = config_dir / "settings.json"
    existing.write_bytes(b"before")
    regular.write_bytes(b"before")
    monkeypatch.setattr(real_config_guard, "REAL_CONFIG_DIR", config_dir)

    before = real_config_guard.snapshot()
    existing.write_bytes(b"after")
    regular.write_bytes(b"after")
    with pytest.raises(AssertionError, match="CONTENT-CHANGED:"):
        _assert_no_real_config_leaks(before, real_config_guard.snapshot())

    regular.write_bytes(b"before")
    assert _assert_no_real_config_leaks(before, real_config_guard.snapshot()) is None

    new_session = session_dir / "new.kohakutr"
    new_session.write_bytes(b"new")
    with pytest.raises(AssertionError, match="NEW:"):
        _assert_no_real_config_leaks(before, real_config_guard.snapshot())


def test_sidecars_are_runtime_state_but_the_store_itself_is_not():
    base = real_config_guard.REAL_CONFIG_DIR / "sessions" / "s.kohakutr"
    assert not real_config_guard.is_runtime_state(base)
    for suffix in real_config_guard.SIDECAR_SUFFIXES:
        assert real_config_guard.is_runtime_state(Path(str(base) + suffix)), suffix


def test_daemon_probe_ignores_a_missing_or_dead_pid(tmp_path, monkeypatch):
    assert real_config_guard.running_daemon_pid(tmp_path) is None

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "web.pid").write_text("not-a-pid")
    assert real_config_guard.running_daemon_pid(tmp_path) is None

    (run_dir / "web.pid").write_text(str(os.getpid()))
    assert real_config_guard.running_daemon_pid(tmp_path) == os.getpid()

    monkeypatch.setattr(real_config_guard, "pid_alive", lambda pid: False)
    (run_dir / "web.pid").write_text("999999")
    assert real_config_guard.running_daemon_pid(tmp_path) is None


async def test_a_test_cannot_start_a_real_provider_login():
    from kohakuterrarium.llm.codex import auth as codex_auth
    from kohakuterrarium.llm.codex import provider as codex_provider

    for module in (codex_auth, codex_provider):
        with pytest.raises(AssertionError, match="real provider login"):
            await module.oauth_login()


def test_operator_provider_keys_are_not_visible_to_tests():
    inherited = [name for name in PROVIDER_KEY_MAP.values() if name in os.environ]
    assert inherited == []
    for provider in PROVIDER_KEY_MAP:
        assert get_api_key(provider) == ""
