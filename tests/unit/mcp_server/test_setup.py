"""Configuration sessions validate candidates and reject stale commits."""

import sys

import pytest

from kohakuterrarium.mcp_server.connection import ConnectionStore
from kohakuterrarium.mcp_server.setup import SetupSession


def test_partial_updates_clear_fields_and_concurrent_save(tmp_path):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    config = tmp_path / "tools.yaml"
    config.write_text(f"workspace: {tmp_path.as_posix()}\n")
    session = SetupSession.open(store)
    first = session.prepare(
        public_origin="https://old.example", tunnel="external", tools_config=config
    )
    assert not store.record_path.exists()
    session.save(first)
    earlier, later = SetupSession.open(store), SetupSession.open(store)
    second = later.prepare(public_origin="https://new.example", clear_tools_config=True)
    later.save(second)
    assert store.load().secret == first.secret
    assert store.load().tools_config is None
    with pytest.raises(ValueError, match="changed"):
        earlier.save(earlier.prepare(port=9001))
    assert store.load().public_origin == "https://new.example"
    assert store.load().port == 8765


def test_validation_and_mode_switch_leave_previous_record_intact(tmp_path):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    ngrok_config = tmp_path / "ngrok.yml"
    ngrok_config.write_text("version: '3'\n")
    session = SetupSession.open(store)
    initial = session.prepare(
        public_origin="https://example.com",
        ngrok_bin=sys.executable,
        ngrok_config=ngrok_config,
    )
    session.save(initial)
    session = SetupSession.open(store)
    for options in (
        {"ngrok_bin": "kt-missing-test-binary"},
        {"ngrok_config": tmp_path / "missing"},
        {"public_origin": "http://example.com"},
    ):
        with pytest.raises(ValueError):
            session.prepare(**options)
        assert store.load() == initial
    external = session.prepare(tunnel="external")
    assert external.ngrok_bin == "ngrok" and external.ngrok_config is None
    session.save(external)
    with pytest.raises(ValueError, match="ngrok"):
        SetupSession.open(store).prepare(ngrok_bin=sys.executable)
