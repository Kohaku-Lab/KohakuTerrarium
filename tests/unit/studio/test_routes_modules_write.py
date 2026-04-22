"""Module write route tests — scaffold / save / delete across kinds."""

from pathlib import Path

# ---------- tools ----------------------------------------------------


def test_scaffold_tool(client, tmp_workspace: Path):
    resp = client.post("/api/studio/modules/tools", json={"name": "my_tool"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "my_tool"
    assert body["mode"] in ("simple", "raw")

    p = tmp_workspace / "modules" / "tools" / "my_tool.py"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    compile(text, str(p), "exec")
    assert "BaseTool" in text


def test_scaffold_tool_duplicate_409(client, tmp_workspace: Path):
    (tmp_workspace / "modules" / "tools" / "dup.py").write_text(
        "x = 1\n",
        encoding="utf-8",
    )
    resp = client.post("/api/studio/modules/tools", json={"name": "dup"})
    assert resp.status_code == 409


def test_save_tool_simple_mode(client, tmp_workspace: Path):
    client.post("/api/studio/modules/tools", json={"name": "edit_me"})
    # Now patch via PUT in simple mode
    resp = client.put(
        "/api/studio/modules/tools/edit_me",
        json={
            "mode": "simple",
            "form": {
                "class_name": "EditMeTool",
                "tool_name": "edit_me",
                "description": "patched",
                "execution_mode": "direct",
                "needs_context": False,
            },
            "execute_body": 'return ToolResult(output="patched!")',
        },
    )
    assert resp.status_code == 200
    text = (tmp_workspace / "modules" / "tools" / "edit_me.py").read_text(
        encoding="utf-8"
    )
    assert "patched" in text
    assert "patched!" in text


def test_save_tool_raw_mode(client, tmp_workspace: Path):
    raw = (
        '"""raw"""\n'
        "from typing import Any\n"
        "from kohakuterrarium.modules.tool.base import (\n"
        "    BaseTool, ExecutionMode, ToolResult,\n"
        ")\n\n"
        "class RawTool(BaseTool):\n"
        "    @property\n"
        "    def tool_name(self) -> str:\n"
        '        return "raw"\n'
        "    @property\n"
        "    def description(self) -> str:\n"
        '        return "x"\n'
        "    @property\n"
        "    def execution_mode(self) -> ExecutionMode:\n"
        "        return ExecutionMode.DIRECT\n"
        "    async def _execute(self, args, **kw):\n"
        '        return ToolResult(output="raw!")\n'
    )
    # Scaffold first so file exists
    client.post("/api/studio/modules/tools", json={"name": "raw"})
    resp = client.put(
        "/api/studio/modules/tools/raw",
        json={
            "mode": "raw",
            "raw_source": raw,
        },
    )
    assert resp.status_code == 200
    text = (tmp_workspace / "modules" / "tools" / "raw.py").read_text(encoding="utf-8")
    assert text == raw


def test_save_tool_roundtrip_fail_returns_422(client, tmp_workspace: Path):
    # File with no class — update_existing should fail
    bad_src = "# no class here\nx = 1\n"
    p = tmp_workspace / "modules" / "tools" / "broken.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(bad_src, encoding="utf-8")
    resp = client.put(
        "/api/studio/modules/tools/broken",
        json={
            "mode": "simple",
            "form": {"class_name": "Nope"},
            "execute_body": "return None",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "roundtrip_failed"


def test_delete_tool(client, tmp_workspace: Path):
    client.post("/api/studio/modules/tools", json={"name": "to_kill"})
    resp = client.delete("/api/studio/modules/tools/to_kill?confirm=true")
    assert resp.status_code == 200
    assert not (tmp_workspace / "modules" / "tools" / "to_kill.py").exists()


def test_delete_requires_confirm(client, tmp_workspace: Path):
    client.post("/api/studio/modules/tools", json={"name": "to_keep"})
    resp = client.delete("/api/studio/modules/tools/to_keep")
    assert resp.status_code == 428


# ---------- plugins --------------------------------------------------


def test_scaffold_plugin(client, tmp_workspace: Path):
    resp = client.post(
        "/api/studio/modules/plugins",
        json={
            "name": "logger",
        },
    )
    assert resp.status_code == 201
    text = (tmp_workspace / "modules" / "plugins" / "logger.py").read_text(
        encoding="utf-8"
    )
    compile(text, "<plugin>", "exec")
    assert "BasePlugin" in text


def test_save_plugin_with_hooks(client, tmp_workspace: Path):
    client.post("/api/studio/modules/plugins", json={"name": "p"})
    resp = client.put(
        "/api/studio/modules/plugins/p",
        json={
            "mode": "simple",
            "form": {
                "class_name": "PPlugin",
                "name": "p",
                "priority": 10,
                "description": "test",
                "enabled_hooks": [
                    {"name": "pre_tool_execute", "body": 'logger.info("pre")'},
                ],
            },
        },
    )
    assert resp.status_code == 200
    text = (tmp_workspace / "modules" / "plugins" / "p.py").read_text(encoding="utf-8")
    compile(text, "<plugin>", "exec")
    assert "pre_tool_execute" in text


# ---------- subagents ------------------------------------------------


def test_scaffold_subagent(client, tmp_workspace: Path):
    resp = client.post(
        "/api/studio/modules/subagents",
        json={
            "name": "helper",
        },
    )
    assert resp.status_code == 201
    text = (tmp_workspace / "modules" / "subagents" / "helper.py").read_text(
        encoding="utf-8"
    )
    compile(text, "<subagent>", "exec")
    assert "SubAgentConfig(" in text


def test_save_subagent_updates_tools(client, tmp_workspace: Path):
    client.post("/api/studio/modules/subagents", json={"name": "helper"})
    resp = client.put(
        "/api/studio/modules/subagents/helper",
        json={
            "mode": "simple",
            "form": {
                "name": "helper",
                "description": "a helper",
                "tools": ["read", "write"],
                "system_prompt": "You help.",
                "can_modify": False,
                "stateless": True,
                "interactive": False,
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["form"]["tools"] == ["read", "write"]


# ---------- triggers -------------------------------------------------


def test_scaffold_trigger(client, tmp_workspace: Path):
    resp = client.post(
        "/api/studio/modules/triggers",
        json={
            "name": "my_trig",
        },
    )
    assert resp.status_code == 201
    text = (tmp_workspace / "modules" / "triggers" / "my_trig.py").read_text(
        encoding="utf-8"
    )
    compile(text, "<trig>", "exec")
    assert "BaseTrigger" in text


# ---------- inputs / outputs -----------------------------------------


def test_scaffold_input(client, tmp_workspace: Path):
    resp = client.post("/api/studio/modules/inputs", json={"name": "my_in"})
    assert resp.status_code == 201
    text = (tmp_workspace / "modules" / "inputs" / "my_in.py").read_text(
        encoding="utf-8"
    )
    compile(text, "<in>", "exec")


def test_scaffold_output(client, tmp_workspace: Path):
    resp = client.post("/api/studio/modules/outputs", json={"name": "my_out"})
    assert resp.status_code == 201
    text = (tmp_workspace / "modules" / "outputs" / "my_out.py").read_text(
        encoding="utf-8"
    )
    compile(text, "<out>", "exec")
