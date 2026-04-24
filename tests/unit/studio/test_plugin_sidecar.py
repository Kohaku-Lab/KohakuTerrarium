"""Plugin options-schema sidecar — write, read-back, and schema-route
integration."""

import json
from pathlib import Path

PLUGIN_SRC = '''\
"""Sample options-dict plugin."""

from kohakuterrarium.modules.plugin.base import BasePlugin


class MyPlugin(BasePlugin):
    name = "my_plugin"
    priority = 50

    def __init__(self, options: dict = None):
        self.options = options or {}

    async def pre_tool_execute(self, args: dict, **kwargs):
        return args
'''


SIDECAR = [
    {
        "name": "budget_usd",
        "type_hint": "float",
        "default": 5.0,
        "required": False,
        "description": "Monthly cap",
    },
    {
        "name": "model_allow",
        "type_hint": "list[str]",
        "default": None,
        "required": True,
        "description": "Allowed model ids",
    },
]


def _write_plugin(tmp: Path) -> Path:
    p = tmp / "modules" / "plugins" / "my_plugin.py"
    p.write_text(PLUGIN_SRC, encoding="utf-8")
    return p


def _write_sidecar(tmp: Path, data: list) -> Path:
    p = tmp / "modules" / "plugins" / "my_plugin.schema.json"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def test_save_module_writes_sidecar(client, tmp_workspace: Path):
    _write_plugin(tmp_workspace)
    resp = client.put(
        "/api/studio/modules/plugins/my_plugin",
        json={
            "mode": "simple",
            "form": {
                "class_name": "MyPlugin",
                "name": "my_plugin",
                "priority": 50,
                "description": "x",
                "enabled_hooks": [{"name": "pre_tool_execute", "body": "return args"}],
                "options_schema": SIDECAR,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    sidecar_path = tmp_workspace / "modules" / "plugins" / "my_plugin.schema.json"
    assert sidecar_path.is_file()
    loaded = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert loaded[0]["name"] == "budget_usd"
    assert loaded[1]["required"] is True


def test_load_module_round_trips_sidecar(client, tmp_workspace: Path):
    _write_plugin(tmp_workspace)
    _write_sidecar(tmp_workspace, SIDECAR)
    resp = client.get("/api/studio/modules/plugins/my_plugin")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    schema = body["form"].get("options_schema") or []
    assert len(schema) == 2
    assert schema[0]["name"] == "budget_usd"
    assert schema[1]["required"] is True


def test_schema_route_surfaces_sidecar_for_options_dict_plugin(
    client, tmp_workspace: Path
):
    _write_plugin(tmp_workspace)
    _write_sidecar(tmp_workspace, SIDECAR)
    resp = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "plugins",
            "name": "my_plugin",
            "type": "custom",
            "module": "modules.plugins.my_plugin",
            "class_name": "MyPlugin",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    params = body["params"]
    assert [p["name"] for p in params] == ["budget_usd", "model_allow"]
    assert params[0]["default"] == 5.0
    assert params[1]["required"] is True


def test_schema_route_falls_back_without_sidecar(client, tmp_workspace: Path):
    _write_plugin(tmp_workspace)
    resp = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "plugins",
            "name": "my_plugin",
            "type": "custom",
            "module": "modules.plugins.my_plugin",
            "class_name": "MyPlugin",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Without a sidecar we get the anonymous options dict back.
    assert [p["name"] for p in body["params"]] == ["options"]


def test_sidecar_ignored_when_init_is_not_options_dict(client, tmp_workspace: Path):
    src = '''\
"""Structured-init plugin."""
from kohakuterrarium.modules.plugin.base import BasePlugin


class StructPlugin(BasePlugin):
    name = "struct_plugin"
    def __init__(self, budget_usd: float = 1.0, label: str = ""):
        self.budget_usd = budget_usd
'''
    (tmp_workspace / "modules" / "plugins" / "struct_plugin.py").write_text(
        src, encoding="utf-8"
    )
    (tmp_workspace / "modules" / "plugins" / "struct_plugin.schema.json").write_text(
        json.dumps([{"name": "ignored", "type_hint": "str"}]), encoding="utf-8"
    )
    resp = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "plugins",
            "name": "struct_plugin",
            "type": "custom",
            "module": "modules.plugins.struct_plugin",
            "class_name": "StructPlugin",
        },
    )
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()["params"]]
    assert "ignored" not in names
    assert "budget_usd" in names
