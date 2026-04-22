"""Phase 2 integration: scaffold → edit → reload → delete."""

from pathlib import Path


def test_full_lifecycle(client, tmp_workspace: Path):
    # 1. Scaffold
    resp = client.post(
        "/api/studio/creatures",
        json={
            "name": "alpha",
            "description": "made by studio",
        },
    )
    assert resp.status_code == 201

    # 2. Reload — config ended up on disk
    resp = client.get("/api/studio/creatures/alpha")
    assert resp.status_code == 200
    orig_cfg = resp.json()["config"]
    assert orig_cfg["name"] == "alpha"

    # 3. Edit via PUT
    patched = {
        "controller": {"reasoning_effort": "high"},
        "tools": [{"name": "read", "type": "builtin"}],
    }
    resp = client.put(
        "/api/studio/creatures/alpha",
        json={
            "config": patched,
            "prompts": {"prompts/system.md": "# alpha v2"},
        },
    )
    assert resp.status_code == 200

    # 4. Reload — patch applied, merged with original
    resp = client.get("/api/studio/creatures/alpha")
    body = resp.json()
    assert body["config"]["controller"]["reasoning_effort"] == "high"
    assert body["config"]["name"] == "alpha"  # preserved from scaffold
    assert body["prompts"]["prompts/system.md"] == "# alpha v2"

    # 5. Validate (sanity check)
    resp = client.post(
        "/api/studio/validate/creature",
        json={"config": body["config"]},
    )
    assert resp.json()["ok"] is True

    # 6. Delete
    resp = client.delete("/api/studio/creatures/alpha?confirm=true")
    assert resp.status_code == 200

    # 7. 404 on reload
    resp = client.get("/api/studio/creatures/alpha")
    assert resp.status_code == 404
