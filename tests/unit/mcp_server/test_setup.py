"""Configuration sessions validate candidates and reject stale commits."""

import base64
import json
import sys

import pytest

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.setup import SetupSession


def test_partial_updates_clear_fields_and_concurrent_save(tmp_path):
    store = EndpointStore(tmp_path / "home")
    config = tmp_path / "tools.yaml"
    config.write_text("tools: [{name: read}]\n")
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
    store = EndpointStore(tmp_path / "home")
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
    for options, error in (
        ({"ngrok_bin": "kt-missing-test-binary"}, ValueError),
        ({"ngrok_config": tmp_path / "missing"}, OSError),
        ({"public_origin": "http://example.com"}, ValueError),
    ):
        with pytest.raises(error):
            session.prepare(**options)
        assert store.load() == initial
    saved = json.loads(store.record_path.read_text())
    assert "cloudflare_tunnel_id" not in saved
    assert "cloudflare_credentials" not in saved
    assert "cloudflared_bin" not in saved
    external = session.prepare(tunnel="external")
    assert external.ngrok_bin == "ngrok" and external.ngrok_config is None
    session.save(external)
    with pytest.raises(ValueError, match="ngrok"):
        SetupSession.open(store).prepare(ngrok_bin=sys.executable)


def test_probe_proxy_explicit_only_and_cleared(tmp_path):
    store = EndpointStore(tmp_path / "home")
    session = SetupSession.open(store)
    record = session.prepare(
        public_origin="https://example.com",
        tunnel="external",
        probe_proxy="http://127.0.0.1:7897",
    )
    session.save(record)
    assert store.load().probe_proxy == "http://127.0.0.1:7897"
    assert "probe_proxy" not in store.build(record, clear_probe_proxy=True).serialized()
    for invalid in (
        "http://user:secret@localhost:7897",
        "http://example.com:7897",
        "socks5://localhost:7897",
        "http://127.0.0.1",
        "http://localhost:7897/path",
        "http://127.0.0.1:0",
        "http://localhost:7897",
        "http://127.0.0.\t1:7897",
        "http://127.0.0.1:7897\n",
    ):
        with pytest.raises(ValueError):
            SetupSession.open(store).prepare(probe_proxy=invalid)
    with pytest.raises(ValueError):
        SetupSession.open(store).prepare(
            probe_proxy="http://127.0.0.1:7897", clear_probe_proxy=True
        )


def test_cloudflare_credentials_and_mode_switch(tmp_path):
    store = EndpointStore(tmp_path / "home")
    credentials = tmp_path / "credentials.json"
    tunnel_id = "f28d5968-1e07-4a79-8055-0c7e58f38341"
    secret = base64.b64encode(b"x" * 32).decode()
    credentials.write_text(
        json.dumps(
            {"TunnelID": tunnel_id, "TunnelSecret": secret, "AccountTag": "account"}
        )
    )
    session = SetupSession.open(store)
    record = session.prepare(
        public_origin="https://example.com",
        tunnel="cloudflare",
        port=9123,
        cloudflared_bin=sys.executable,
        cloudflare_tunnel_id=tunnel_id,
        cloudflare_credentials=credentials,
    )
    result = session.save(record)
    assert store.load().cloudflare_credentials == str(credentials.resolve())
    assert secret not in str(result)
    for invalid in (
        {"TunnelID": tunnel_id, "TunnelSecret": secret},
        {"TunnelID": tunnel_id, "TunnelSecret": "broken", "AccountTag": "account"},
    ):
        credentials.write_text(json.dumps(invalid))
        with pytest.raises(ValueError, match="Invalid Cloudflare"):
            SetupSession.open(store).prepare(port=1234)
    credentials.write_text(
        json.dumps(
            {
                "TunnelID": "00000000-0000-0000-0000-000000000000",
                "TunnelSecret": "secret-value",
            }
        )
    )
    with pytest.raises(ValueError, match="Invalid Cloudflare"):
        SetupSession.open(store).prepare(port=1234)
    assert store.load().port == 9123
    external = SetupSession.open(store).prepare(tunnel="external")
    assert (
        external.cloudflare_credentials is None
        and external.cloudflare_tunnel_id is None
    )
    with pytest.raises(ValueError, match="Cloudflare settings"):
        SetupSession.open(store).prepare(
            tunnel="external", cloudflare_credentials=credentials
        )
