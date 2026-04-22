"""Read-only creature route tests."""

from pathlib import Path


def _write_creature(tmp_workspace: Path, name: str, *, body: str | None = None):
    cdir = tmp_workspace / "creatures" / name
    cdir.mkdir(parents=True)
    (cdir / "config.yaml").write_text(
        body if body is not None else f'name: {name}\nversion: "1.0"\n',
        encoding="utf-8",
    )
    (cdir / "prompts").mkdir(exist_ok=True)
    (cdir / "prompts" / "system.md").write_text(f"# {name}", encoding="utf-8")


def test_list_empty(client):
    resp = client.get("/api/studio/creatures")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_two(client, tmp_workspace: Path):
    _write_creature(tmp_workspace, "alpha")
    _write_creature(tmp_workspace, "beta")
    resp = client.get("/api/studio/creatures")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert names == ["alpha", "beta"]


def test_load_by_name(client, tmp_workspace: Path):
    _write_creature(tmp_workspace, "alpha")
    resp = client.get("/api/studio/creatures/alpha")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "alpha"
    assert body["config"]["name"] == "alpha"
    assert "prompts/system.md" in body["prompts"]
    assert body["prompts"]["prompts/system.md"] == "# alpha"
    assert "effective" in body


def test_load_missing_returns_404(client):
    resp = client.get("/api/studio/creatures/ghost")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_load_unsafe_name_returns_400(client):
    resp = client.get("/api/studio/creatures/..%2Fetc")
    # Depends on path escaping — at minimum it must not 200 with attacker data.
    assert resp.status_code in (400, 404)
