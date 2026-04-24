"""Phase-5 smoke — exercise the full author-to-creature chain via REST.

Matches the Phase-5 exit criteria in ``plans/kt-studio/phases.md § 5.9``.
Stops before running the agent — the "file on disk loads via core
without exception" assertion is the terminal signal we want from this
pipeline. No LLM, no WebSocket traffic, no `kt run`.

What this covers end-to-end:

1. Scaffold a workspace tool (``POST /modules/tools``).
2. Save it with form state + execute body (``PUT /modules/tools/<n>``).
3. Introspect it — the schema route reports the canonical framework
   ToolConfig params (the tool file itself has no extra init params).
4. Sync the tool into ``kohaku.yaml`` — idempotent second call.
5. Write a sidecar skill doc (``PUT /modules/tools/<n>/doc``) and read
   it back.
6. Scaffold a plugin with an options-schema sidecar and confirm the
   schema route surfaces the per-key layout back to a consumer.
7. Scaffold a creature, wire the tool + plugin, save, and hand the
   config folder to ``core.config.load_agent_config`` — it must parse
   without raising.
"""

from pathlib import Path

from kohakuterrarium.core.config import load_agent_config


def test_phase5_smoke_end_to_end(client, tmp_workspace: Path):
    # ── 1. Scaffold a tool ──────────────────────────────────────
    r = client.post(
        "/api/studio/modules/tools",
        json={"name": "currency_convert"},
    )
    assert r.status_code == 201, r.text
    scaffold = r.json()
    assert scaffold["kind"] == "tools"
    assert scaffold["name"] == "currency_convert"

    # ── 2. Save edits — description + an execute body ───────────
    r = client.put(
        "/api/studio/modules/tools/currency_convert",
        json={
            "mode": "simple",
            "form": {
                "class_name": scaffold["form"]["class_name"],
                "tool_name": "currency_convert",
                "description": "Convert between currencies via a live FX lookup.",
                "execution_mode": "direct",
                "needs_context": False,
                "require_manual_read": False,
                "params": [],
            },
            "execute_body": 'return ToolResult(output="stub")',
        },
    )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["form"]["description"].startswith("Convert between currencies")
    assert "stub" in saved["execute_body"]

    # ── 3. Introspect the tool's schema ─────────────────────────
    r = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "tools",
            "name": "currency_convert",
            "type": "custom",
            "module": "modules.tools.currency_convert",
            "class_name": scaffold["form"]["class_name"],
        },
    )
    assert r.status_code == 200, r.text
    schema = r.json()
    # The generated tool only declares the framework's __init__ — the
    # schema is empty beyond that. The call must still succeed cleanly.
    assert "params" in schema
    assert "warnings" in schema

    # ── 4. Manifest sync — once, then again (idempotent) ────────
    r = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "tools", "name": "currency_convert"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added"] is True
    r = client.post(
        "/api/studio/workspace/manifest/sync",
        json={"kind": "tools", "name": "currency_convert"},
    )
    assert r.status_code == 200
    assert r.json()["added"] is False  # already there

    manifest_text = (tmp_workspace / "kohaku.yaml").read_text(encoding="utf-8")
    assert "currency_convert" in manifest_text
    assert "modules.tools.currency_convert" in manifest_text

    # ── 5. Skill doc sidecar round-trip ─────────────────────────
    r = client.put(
        "/api/studio/modules/tools/currency_convert/doc",
        json={"content": "# currency_convert\n\nLooks up FX rates.\n"},
    )
    assert r.status_code == 200
    r = client.get("/api/studio/modules/tools/currency_convert/doc")
    assert r.status_code == 200
    doc = r.json()
    assert doc["exists"] is True
    assert "Looks up FX rates" in doc["content"]

    # ── 6. Plugin with options-schema sidecar ───────────────────
    r = client.post(
        "/api/studio/modules/plugins",
        json={"name": "budget_guard"},
    )
    assert r.status_code == 201, r.text
    plugin_scaffold = r.json()
    plugin_class = plugin_scaffold["form"]["class_name"]

    options_schema = [
        {
            "name": "budget_usd",
            "type_hint": "float",
            "default": 5.0,
            "required": False,
            "description": "Monthly cap",
        },
    ]
    r = client.put(
        "/api/studio/modules/plugins/budget_guard",
        json={
            "mode": "simple",
            "form": {
                "class_name": plugin_class,
                "name": "budget_guard",
                "priority": 50,
                "description": "Cap monthly spend.",
                "enabled_hooks": [
                    {"name": "pre_llm_call", "body": "return messages"},
                ],
                "options_schema": options_schema,
            },
        },
    )
    assert r.status_code == 200, r.text

    sidecar_file = tmp_workspace / "modules" / "plugins" / "budget_guard.schema.json"
    assert sidecar_file.is_file()

    # The schema route swaps the anonymous ``options: dict`` param for
    # the sidecar's per-key descriptors when present.
    r = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "plugins",
            "name": "budget_guard",
            "type": "custom",
            "module": "modules.plugins.budget_guard",
            "class_name": plugin_class,
        },
    )
    assert r.status_code == 200
    params = r.json()["params"]
    assert [p["name"] for p in params] == ["budget_usd"]
    assert params[0]["default"] == 5.0

    # ── 7. Scaffold a creature, wire the module, save ───────────
    r = client.post(
        "/api/studio/creatures",
        json={"name": "smoke_creature"},
    )
    assert r.status_code == 201, r.text
    creature = r.json()
    config = creature["config"]
    config.setdefault("tools", []).append(
        {
            "name": "currency_convert",
            "type": "custom",
            "module": "modules.tools.currency_convert",
            "class": scaffold["form"]["class_name"],
        }
    )
    config.setdefault("plugins", []).append(
        {
            "name": "budget_guard",
            "type": "custom",
            "module": "modules.plugins.budget_guard",
            "class": plugin_class,
            "options": {"budget_usd": 5.0},
        }
    )
    r = client.put(
        "/api/studio/creatures/smoke_creature",
        json={"config": config, "prompts": creature.get("prompts") or {}},
    )
    assert r.status_code == 200, r.text

    # ── 8. Core loader accepts the saved creature ───────────────
    creature_dir = tmp_workspace / "creatures" / "smoke_creature"
    assert creature_dir.is_dir()
    loaded = load_agent_config(creature_dir)
    assert loaded.name == "smoke_creature"

    tool_names = [t.name for t in (loaded.tools or [])]
    assert "currency_convert" in tool_names
    plugin_entries = loaded.plugins or []
    plugin_names = [
        (p.name if hasattr(p, "name") else p.get("name")) for p in plugin_entries
    ]
    assert "budget_guard" in plugin_names
