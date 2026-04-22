"""Creature write route tests — scaffold / save / delete."""

from pathlib import Path


def test_scaffold_creates_disk_layout(client, tmp_workspace: Path):
    resp = client.post(
        "/api/studio/creatures",
        json={
            "name": "alpha",
            "description": "a new creature",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "alpha"

    # Files on disk
    cdir = tmp_workspace / "creatures" / "alpha"
    assert cdir.is_dir()
    assert (cdir / "config.yaml").is_file()
    assert (cdir / "prompts" / "system.md").is_file()

    cfg_text = (cdir / "config.yaml").read_text(encoding="utf-8")
    assert "name: alpha" in cfg_text


def test_scaffold_with_base(client, tmp_workspace: Path):
    resp = client.post(
        "/api/studio/creatures",
        json={
            "name": "beta",
            "base_config": "@kt-biome/creatures/general",
        },
    )
    assert resp.status_code == 201
    cfg_text = (tmp_workspace / "creatures" / "beta" / "config.yaml").read_text(
        encoding="utf-8"
    )
    assert "@kt-biome/creatures/general" in cfg_text
    assert "base_config:" in cfg_text


def test_scaffold_duplicate_409(client, tmp_workspace: Path):
    (tmp_workspace / "creatures" / "alpha").mkdir()
    (tmp_workspace / "creatures" / "alpha" / "config.yaml").write_text(
        "name: alpha\n",
        encoding="utf-8",
    )
    resp = client.post("/api/studio/creatures", json={"name": "alpha"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "name_exists"


def test_scaffold_invalid_name(client):
    resp = client.post("/api/studio/creatures", json={"name": "../oops"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_name"


def test_save_round_trip_preserves_comments(client, tmp_workspace: Path):
    # Seed with a file that carries a comment
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir(parents=True)
    (cdir / "prompts").mkdir()
    (cdir / "config.yaml").write_text(
        'name: alpha\nversion: "1.0"\n'
        "# Tweak me\ncontroller:\n  reasoning_effort: medium\n",
        encoding="utf-8",
    )
    (cdir / "prompts" / "system.md").write_text("# alpha", encoding="utf-8")

    # PUT an update to reasoning_effort only
    resp = client.put(
        "/api/studio/creatures/alpha",
        json={
            "config": {"controller": {"reasoning_effort": "high"}},
            "prompts": {},
        },
    )
    assert resp.status_code == 200

    text = (cdir / "config.yaml").read_text(encoding="utf-8")
    assert "# Tweak me" in text  # comment preserved
    assert "reasoning_effort: high" in text  # value updated
    assert "name: alpha" in text  # other keys intact


def test_save_writes_prompts(client, tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir(parents=True)
    (cdir / "config.yaml").write_text(
        "name: alpha\n",
        encoding="utf-8",
    )

    resp = client.put(
        "/api/studio/creatures/alpha",
        json={
            "config": {"description": "edited"},
            "prompts": {"prompts/system.md": "# new prompt"},
        },
    )
    assert resp.status_code == 200
    assert (cdir / "prompts" / "system.md").read_text(
        encoding="utf-8"
    ) == "# new prompt"


def test_save_rejects_escape_in_prompt_path(client, tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir(parents=True)
    (cdir / "config.yaml").write_text("name: alpha\n", encoding="utf-8")

    resp = client.put(
        "/api/studio/creatures/alpha",
        json={
            "config": {},
            "prompts": {"../../etc/passwd": "bad"},
        },
    )
    assert resp.status_code == 400


def test_delete_requires_confirm(client, tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir(parents=True)
    (cdir / "config.yaml").write_text("name: alpha\n", encoding="utf-8")

    resp = client.delete("/api/studio/creatures/alpha")
    assert resp.status_code == 428


def test_delete_with_confirm(client, tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir(parents=True)
    (cdir / "config.yaml").write_text("name: alpha\n", encoding="utf-8")

    resp = client.delete("/api/studio/creatures/alpha?confirm=true")
    assert resp.status_code == 200
    assert not cdir.exists()


def test_delete_missing_404(client):
    resp = client.delete("/api/studio/creatures/ghost?confirm=true")
    assert resp.status_code == 404
