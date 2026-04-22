"""Prompt file read/write routes."""

from pathlib import Path


def _seed(tmp_workspace: Path):
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir(parents=True)
    (cdir / "config.yaml").write_text("name: alpha\n", encoding="utf-8")
    return cdir


def test_read_prompt(client, tmp_workspace: Path):
    cdir = _seed(tmp_workspace)
    (cdir / "prompts").mkdir()
    (cdir / "prompts" / "system.md").write_text("hello", encoding="utf-8")

    resp = client.get("/api/studio/creatures/alpha/prompts/prompts/system.md")
    assert resp.status_code == 200
    assert resp.json()["content"] == "hello"


def test_read_missing_404(client, tmp_workspace: Path):
    _seed(tmp_workspace)
    resp = client.get("/api/studio/creatures/alpha/prompts/prompts/none.md")
    assert resp.status_code == 404


def test_write_creates_dirs(client, tmp_workspace: Path):
    cdir = _seed(tmp_workspace)
    resp = client.put(
        "/api/studio/creatures/alpha/prompts/prompts/system.md",
        json={"content": "# hi"},
    )
    assert resp.status_code == 200
    assert (cdir / "prompts" / "system.md").read_text(encoding="utf-8") == "# hi"


def test_write_escape_blocked(client, tmp_workspace: Path):
    _seed(tmp_workspace)
    resp = client.put(
        "/api/studio/creatures/alpha/prompts/..%2F..%2Fetc%2Fpasswd",
        json={"content": "bad"},
    )
    assert resp.status_code in (400, 404)


def test_round_trip(client, tmp_workspace: Path):
    _seed(tmp_workspace)
    body = "line1\nline2\n"
    client.put(
        "/api/studio/creatures/alpha/prompts/prompts/notes.md",
        json={"content": body},
    )
    resp = client.get("/api/studio/creatures/alpha/prompts/prompts/notes.md")
    assert resp.json()["content"] == body
