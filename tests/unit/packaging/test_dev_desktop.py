"""Source runner prerequisites and configuration, without a desktop session."""

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "dev_desktop", ROOT / "scripts/dev_desktop.py"
)
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


def test_editable_checkout_is_required(tmp_path):
    dev.check_source(Path(sys.executable), ROOT)
    with pytest.raises(ValueError, match="expected editable source"):
        dev.check_source(Path(sys.executable), tmp_path)
    with pytest.raises(ValueError, match="Python not found"):
        dev.check_source(tmp_path / "missing", ROOT)


@pytest.mark.parametrize("ports", [(15173, 15173), (0, 18001), (15173, 65536)])
def test_invalid_ports_are_rejected(ports):
    with pytest.raises(ValueError, match="distinct"):
        dev.check_ports(*ports)


def test_busy_port_does_not_attach_or_kill_owner():
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen()
        port = owner.getsockname()[1]
        with pytest.raises(ValueError, match="in use"):
            dev.check_ports(port, 18001 if port != 18001 else 18002)
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            connection, _ = owner.accept()
            connection.close()


def test_configuration_is_serializable_and_uses_separate_identity(tmp_path):
    config = dev.configuration(
        Path(sys.executable), tmp_path / "dev data", 15173, 18001
    )
    assert json.loads(json.dumps(config)) == config
    assert config["data"] == str(tmp_path / "dev data")
    override = dev.tauri_config(config)
    assert override["identifier"] == "com.kohaku.terrarium.development"
    assert override["build"]["devUrl"] == "http://127.0.0.1:15173/"
    assert override["build"]["beforeDevCommand"]["wait"] is False
    # Tauri executes hooks through cmd /S /C on Windows: a leading quoted
    # executable can be re-quoted by the CLI and become an invalid command.
    assert (
        override["build"]["beforeDevCommand"]["script"]
        == "node scripts/desktop-vite.mjs"
    )
