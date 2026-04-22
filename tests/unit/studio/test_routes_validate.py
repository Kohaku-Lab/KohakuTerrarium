"""Validate route tests — schema + reference validation."""


def test_validate_minimal_valid(client):
    resp = client.post(
        "/api/studio/validate/creature",
        json={
            "config": {"name": "alpha", "version": "1.0"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == []


def test_validate_missing_name(client):
    resp = client.post(
        "/api/studio/validate/creature",
        json={
            "config": {"version": "1.0"},  # no name
        },
    )
    body = resp.json()
    assert body["ok"] is False
    codes = [e["code"] for e in body["errors"]]
    assert any(c == "schema_error" for c in codes)


def test_validate_unknown_builtin_tool(client):
    resp = client.post(
        "/api/studio/validate/creature",
        json={
            "config": {
                "name": "alpha",
                "tools": [{"name": "bogus_tool", "type": "builtin"}],
            },
        },
    )
    body = resp.json()
    assert body["ok"] is False
    codes = [e["code"] for e in body["errors"]]
    assert "unknown_builtin_tool" in codes


def test_validate_known_builtin_tool(client):
    resp = client.post(
        "/api/studio/validate/creature",
        json={
            "config": {
                "name": "alpha",
                "tools": [{"name": "read", "type": "builtin"}],
            },
        },
    )
    assert resp.json()["ok"] is True


def test_validate_custom_tool_missing_module(client):
    resp = client.post(
        "/api/studio/validate/creature",
        json={
            "config": {
                "name": "alpha",
                "tools": [{"name": "custom", "type": "custom"}],
            },
        },
    )
    body = resp.json()
    assert body["ok"] is False
    codes = [e["code"] for e in body["errors"]]
    assert "missing_module" in codes


def test_validate_unknown_builtin_subagent(client):
    resp = client.post(
        "/api/studio/validate/creature",
        json={
            "config": {
                "name": "alpha",
                "subagents": [{"name": "nope", "type": "builtin"}],
            },
        },
    )
    body = resp.json()
    assert body["ok"] is False
    codes = [e["code"] for e in body["errors"]]
    assert "unknown_builtin_subagent" in codes


def test_validate_module_syntax_ok(client):
    resp = client.post(
        "/api/studio/validate/module",
        json={
            "kind": "tools",
            "source": "x = 1\n",
        },
    )
    assert resp.json() == {"ok": True, "errors": []}


def test_validate_module_syntax_error(client):
    resp = client.post(
        "/api/studio/validate/module",
        json={
            "kind": "tools",
            "source": "def broken(:\n",
        },
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"][0]["code"] == "syntax_error"
