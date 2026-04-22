"""Packages route tests — rely on whatever is installed locally."""


def test_list_returns_list(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/packages")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # If at least one package is installed, verify shape
    if body:
        assert "name" in body[0]


def test_unknown_package_returns_404(no_workspace_client):
    resp = no_workspace_client.get(
        "/api/studio/packages/__definitely_not_installed__/creatures"
    )
    assert resp.status_code == 404


def test_unknown_modules_kind_returns_empty_or_404(no_workspace_client):
    resp = no_workspace_client.get(
        "/api/studio/packages/__definitely_not_installed__/modules/tools"
    )
    assert resp.status_code == 404
