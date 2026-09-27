"""Local registration commands preserve endpoint identity and do not start it."""

import asyncio
import json
import os

import pytest

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.management import (
    manage,
    serve_management,
    management_snapshot,
)
from kohakuterrarium.mcp_server.config import GlobalToolsConfig
from kohakuterrarium.mcp_server.records import write_json
from kohakuterrarium.mcp_server.service import is_running
from kohakuterrarium.mcp_server.workspaces import WorkspacePool


def test_offline_registry_lifecycle_does_not_create_or_start_endpoint(tmp_path):
    store = EndpointStore(tmp_path / "home")
    first = manage(store, "add", name="a", path=tmp_path)
    manage(store, "add", name="b", path=tmp_path)
    assert not store.record_path.exists() and not is_running(store)
    with pytest.raises(ValueError, match="already"):
        manage(store, "add", name="a", path=tmp_path)
    listed = manage(store, "list")
    assert len(listed["workspaces"]) == 2
    assert listed["management"]["state"] == "stopped"
    assert "outcome" not in listed["management"]
    assert manage(store, "remove", name="a")["removed"]
    second = manage(store, "add", name="a", path=tmp_path)
    assert first["registration_id"] != second["registration_id"]
    assert len(manage(store, "list")["workspaces"]) == 2


async def response_for(store, request_id):
    for _ in range(200):
        if store.response_path.is_file():
            data = json.loads(store.response_path.read_text())
            if data.get("request_id") == request_id:
                return data
        await asyncio.sleep(0.01)
    pytest.fail("Management request was not acknowledged")


@pytest.mark.parametrize("payload", ["{broken", "[]", "null"])
async def test_damaged_request_does_not_kill_consumer(tmp_path, payload):
    store = EndpointStore(tmp_path / "home")
    store.directory.mkdir(parents=True)
    store.control_path.write_text(payload)
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        task = asyncio.create_task(serve_management(store, "run", pool))
        try:
            await asyncio.sleep(0.1)
            assert not task.done()
            write_json(
                store.control_path,
                {
                    "run_id": "run",
                    "request_id": "fixed",
                    "operation": "add",
                    "name": "project",
                    "path": str(tmp_path),
                    "force": False,
                },
            )
            result = await response_for(store, "fixed")
            assert result["run_id"] == "run"
            assert (
                result["result"]["registration_id"]
                == store.registry.read()["project"].registration_id
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_response_write_retry_does_not_execute_twice(tmp_path, monkeypatch):
    store = EndpointStore(tmp_path / "home")
    write_json(
        store.control_path,
        {
            "run_id": "run",
            "request_id": "add-once",
            "operation": "add",
            "name": "project",
            "path": str(tmp_path),
            "force": False,
        },
    )
    replace = os.replace
    blocked = True

    def fail_response(source, destination):
        if blocked and destination == store.response_path:
            raise OSError("Response disk unavailable")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_response)
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        task = asyncio.create_task(serve_management(store, "run", pool))
        try:
            for _ in range(100):
                if store.registry.read():
                    break
                await asyncio.sleep(0.01)
            registration = store.registry.read()["project"]
            await asyncio.sleep(0.1)
            assert not task.done()
            blocked = False
            result = await response_for(store, "add-once")
            assert "error" not in result
            assert result["result"]["registration_id"] == registration.registration_id
            assert store.registry.read()["project"] == registration
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_unconfirmed_command_allows_read_only_snapshot(tmp_path):
    store = EndpointStore(tmp_path / "home")
    store.registry.add("existing", tmp_path)
    write_json(
        store.runtime_path,
        {"run_id": "run", "management": {"protocol_version": 1, "state": "ready"}},
    )
    with store.instance_lock:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            manage(store, "add", name="project", path=tmp_path, wait=0.01)
        before = store.control_path.read_bytes()
        result = manage(store, "list", wait=0.01)
        assert result["source"] == "registry_snapshot"
        assert result["workspaces"][0]["state"] == "unknown"
        assert result["workspaces"][0]["workspace_id"] == "existing"
        assert result["management"]["outcome"] == "unconfirmed"
        with pytest.raises(RuntimeError, match="pending"):
            manage(store, "add", name="other", path=tmp_path, wait=0.01)
        assert store.control_path.read_bytes() == before


def test_other_run_response_does_not_acknowledge_pending_request(tmp_path):
    store = EndpointStore(tmp_path / "home")
    write_json(
        store.runtime_path,
        {"run_id": "run", "management": {"protocol_version": 1, "state": "ready"}},
    )
    write_json(
        store.control_path,
        {"run_id": "run", "request_id": "pending", "operation": "list"},
    )
    write_json(
        store.response_path,
        {"run_id": "old", "request_id": "pending", "result": {"workspaces": []}},
    )
    before = store.control_path.read_bytes()
    with store.instance_lock:
        with pytest.raises(RuntimeError, match="pending"):
            manage(store, "add", name="project", path=tmp_path, wait=0.01)
    assert store.control_path.read_bytes() == before


async def test_stop_fences_management_requests(tmp_path):
    store = EndpointStore(tmp_path / "home")
    write_json(
        store.runtime_path,
        {"run_id": "run", "management": {"protocol_version": 1, "state": "ready"}},
    )
    write_json(store.stop_path, {"run_id": "run"})
    write_json(
        store.control_path,
        {
            "run_id": "run",
            "request_id": "late",
            "operation": "add",
            "name": "project",
            "path": str(tmp_path),
        },
    )
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        task = asyncio.create_task(serve_management(store, "run", pool))
        try:
            await asyncio.sleep(0.1)
            assert not store.registry.read()
            with store.instance_lock:
                with pytest.raises(RuntimeError, match="stopping"):
                    manage(store, "add", name="other", path=tmp_path, wait=0.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_unknown_offline_operation_is_rejected(tmp_path):
    store = EndpointStore(tmp_path / "home")
    with pytest.raises(ValueError, match="operation"):
        manage(store, "typo")


def test_old_supervisor_requires_restart_before_new_command(tmp_path):
    store = EndpointStore(tmp_path / "home")
    write_json(store.runtime_path, {"run_id": "old"})
    with store.instance_lock:
        with pytest.raises(RuntimeError, match="restart"):
            manage(store, "add", name="project", path=tmp_path, wait=0.01)
    assert not store.control_path.exists() and not store.registry.read()


@pytest.mark.parametrize(
    "change",
    [{"force": "yes"}, {"operation": "oops"}, {"name": None}, {"unexpected": True}],
)
async def test_invalid_command_is_acknowledged_without_mutation(tmp_path, change):
    store = EndpointStore(tmp_path / "home")
    entry = store.registry.add("project", tmp_path)
    write_json(
        store.control_path,
        {
            "run_id": "run",
            "request_id": "invalid",
            "operation": "remove",
            "name": "project",
            "force": False,
            **change,
        },
    )
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        task = asyncio.create_task(serve_management(store, "run", pool))
        try:
            result = await response_for(store, "invalid")
            assert "not executed" in result["error"]
            assert store.registry.read()["project"] == entry
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_list_does_not_wait_on_mutating_cli_lock(tmp_path):
    store = EndpointStore(tmp_path / "home")
    store.registry.add("project", tmp_path)
    with store.command_lock:
        result = manage(store, "list")
        assert result["source"] == "registry_snapshot"
        assert result["workspaces"][0]["state"] == "unknown"
    assert not store.control_path.exists()


async def test_restart_ignores_old_request_and_response(tmp_path):
    store = EndpointStore(tmp_path / "home")
    write_json(
        store.control_path,
        {
            "run_id": "old",
            "request_id": "old-request",
            "operation": "add",
            "name": "old",
            "path": str(tmp_path),
        },
    )
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        task = asyncio.create_task(serve_management(store, "new", pool))
        try:
            await asyncio.sleep(0.1)
            assert not store.registry.read()
            write_json(
                store.response_path,
                {"run_id": "old", "request_id": "reused", "result": {"workspaces": []}},
            )
            write_json(
                store.control_path,
                {
                    "run_id": "new",
                    "request_id": "reused",
                    "operation": "add",
                    "name": "new",
                    "path": str(tmp_path),
                },
            )
            for _ in range(100):
                if store.registry.read():
                    break
                await asyncio.sleep(0.01)
            assert set(store.registry.read()) == {"new"}
            for _ in range(100):
                reply = json.loads(store.response_path.read_text())
                if reply.get("run_id") == "new":
                    break
                await asyncio.sleep(0.01)
            assert reply["run_id"] == "new" and reply["result"]["workspace_id"] == "new"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "payload",
    ["{broken", "[]", '{"run_id":"run","request_id":"pending","result":"broken"}'],
)
async def test_invalid_acknowledgement_never_authorizes_execution(tmp_path, payload):
    store = EndpointStore(tmp_path / "home")
    write_json(
        store.control_path,
        {
            "run_id": "run",
            "request_id": "pending",
            "operation": "add",
            "name": "project",
            "path": str(tmp_path),
        },
    )
    store.response_path.write_text(payload)
    health = {}
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        task = asyncio.create_task(serve_management(store, "run", pool, health=health))
        try:
            await asyncio.sleep(0.1)
            assert not store.registry.read()
            assert not task.done() and health["state"] == "degraded"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_unexpected_consumer_exit_is_visible_and_blocks_new_commands(tmp_path):
    store = EndpointStore(tmp_path / "home")
    health = {}
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        task = asyncio.create_task(serve_management(store, "run", pool, health=health))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        state = management_snapshot(task, health)
        assert state["state"] == "failed"
        write_json(store.runtime_path, {"run_id": "run", "management": state})
        with store.instance_lock:
            with pytest.raises(RuntimeError, match="restart"):
                manage(store, "add", name="project", path=tmp_path)
            result = manage(store, "list")
            assert (
                result["source"] == "registry_snapshot" and result["workspaces"] == []
            )
        assert not store.control_path.exists()


async def test_concurrent_callers_share_one_command_slot_without_overwrite(tmp_path):
    store = EndpointStore(tmp_path / "home")
    write_json(
        store.runtime_path,
        {"run_id": "run", "management": {"protocol_version": 1, "state": "ready"}},
    )
    async with WorkspacePool(GlobalToolsConfig(), store.registry) as pool:
        with store.instance_lock:
            first = asyncio.create_task(
                asyncio.to_thread(
                    manage, store, "add", name="first", path=tmp_path, wait=2
                )
            )
            for _ in range(100):
                if store.control_path.exists():
                    break
                await asyncio.sleep(0.01)
            assert store.control_path.exists()
            second = asyncio.create_task(
                asyncio.to_thread(
                    manage, store, "add", name="second", path=tmp_path, wait=2
                )
            )
            await asyncio.sleep(0.05)
            consumer = asyncio.create_task(serve_management(store, "run", pool))
            try:
                results = await asyncio.gather(first, second, return_exceptions=True)
                assert [r["workspace_id"] for r in results if isinstance(r, dict)] == [
                    "first",
                    "second",
                ], results
                assert set(store.registry.read()) == {"first", "second"}
            finally:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
