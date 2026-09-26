"""Secret path never reaches downstream request logs or ungated discovery."""

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from kohakuterrarium.api.auth.mcp_secret import MCPSecretPath


def test_authentication_and_scope_redaction():
    async def endpoint(scope, receive, send):
        await JSONResponse(
            {
                k: scope[k].decode() if isinstance(scope[k], bytes) else scope[k]
                for k in ("path", "raw_path", "query_string")
            }
        )(scope, receive, send)

    secret = "a" * 43
    client = TestClient(MCPSecretPath(endpoint, secret=secret))
    response = client.post("/mcp/" + secret + "?credential=hidden")
    assert response.json() == {"path": "/mcp", "raw_path": "/mcp", "query_string": ""}
    for path in ("/mcp", "/mcp/invalid", "/mcp/" + secret + "/"):
        assert client.post(path).status_code == 404
    with pytest.raises(ValueError, match="token"):
        MCPSecretPath(endpoint, secret="guessable")
