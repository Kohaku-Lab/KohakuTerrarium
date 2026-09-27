"""Local registration commands preserve endpoint identity and do not start it."""

import pytest

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.management import manage
from kohakuterrarium.mcp_server.service import is_running


def test_offline_registry_lifecycle_does_not_create_or_start_endpoint(tmp_path):
    store = EndpointStore(tmp_path / "home")
    first = manage(store, "add", name="a", path=tmp_path)
    manage(store, "add", name="b", path=tmp_path)
    assert not store.record_path.exists() and not is_running(store)
    with pytest.raises(ValueError, match="already"):
        manage(store, "add", name="a", path=tmp_path)
    assert len(manage(store, "list")["workspaces"]) == 2
    assert manage(store, "remove", name="a")["removed"]
    second = manage(store, "add", name="a", path=tmp_path)
    assert first["registration_id"] != second["registration_id"]
    assert len(manage(store, "list")["workspaces"]) == 2
