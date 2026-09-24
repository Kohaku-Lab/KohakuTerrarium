"""Offline guard and workflow tests for the opt-in live model matrix."""

import importlib.util
import json
import time
from pathlib import Path

import httpx
import pytest

from kohakuterrarium.llm import antigravity_auth as auth

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/probe_antigravity_matrix.py"


@pytest.fixture
def probe(monkeypatch):
    assert SCRIPT.is_file()
    spec = importlib.util.spec_from_file_location("matrix_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: [auth.BorrowedCredential("secret-canary", time.time() + 3600, "test")],
    )
    return module


def frame(parts):
    return (
        "data: "
        + json.dumps(
            {
                "response": {
                    "candidates": [
                        {"content": {"parts": parts}, "finishReason": "STOP"}
                    ]
                }
            }
        )
        + "\n\n"
    )


async def test_matrix_covers_catalog_and_runs_text_then_signed_tools(probe):
    assert len(probe.CASES) == 13
    assert len(set(probe.CASES)) == 13
    for model, effort in probe.CASES:
        submitted = []

        def respond(request):
            assert request.url.host == "daily-cloudcode-pa.googleapis.com"
            if request.url.path.endswith("loadCodeAssist"):
                return httpx.Response(
                    200, json={"cloudaicompanionProject": "private-project-canary"}
                )
            body = json.loads(request.content)
            submitted.append(body)
            if len(submitted) == 2:
                parts = [
                    {
                        "functionCall": {
                            "name": "probe_echo",
                            "args": {"value": "AGY_MATRIX_OK"},
                            "id": "server-id-canary",
                        },
                        "thoughtSignature": "signature-canary",
                    }
                ]
            else:
                parts = [{"text": "AGY_MATRIX_OK"}]
            if len(submitted) == 3:
                assert (
                    body["request"]["contents"][1]["parts"][0]["thoughtSignature"]
                    == "signature-canary"
                )
                assert (
                    body["request"]["contents"][2]["parts"][0]["functionResponse"]["id"]
                    == "server-id-canary"
                )
            return httpx.Response(200, text=frame(parts))

        gate = probe.MatrixTransport(httpx.MockTransport(respond), max_requests=3)
        result = await probe.run_case(model, effort, gate)
        assert result["passed"] is True
        assert gate.inference_requests == 3
        assert [step["passed"] for step in result["steps"]] == [True, True, True]
        encoded = json.dumps(result)
        for secret in (
            "secret-canary",
            "private-project-canary",
            "signature-canary",
            "server-id-canary",
        ):
            assert secret not in encoded
        for body in submitted:
            config = body["request"]["generationConfig"]
            budget = config["thinkingConfig"].get("thinkingBudget", 0)
            assert config["maxOutputTokens"] == max(2048, budget + 1024)
        await gate.aclose()


@pytest.mark.parametrize("status", [401, 404, 429, 503])
async def test_failures_are_bounded_and_redacted(probe, status):
    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "p"})
        return httpx.Response(status, text="secret-canary")

    gate = probe.MatrixTransport(httpx.MockTransport(respond), max_requests=3)
    result = await probe.run_case("gemini-3.8-flash", "high", gate)
    assert result["passed"] is False
    assert gate.inference_requests == 1
    assert result["steps"][0]["http_status"] == status
    assert "secret-canary" not in json.dumps(result)
    await gate.aclose()


async def test_gate_blocks_unapproved_hosts_and_budget_overrun(probe):
    calls = []
    gate = probe.MatrixTransport(
        httpx.MockTransport(lambda req: (calls.append(req) or httpx.Response(200))),
        max_requests=1,
    )
    gate.start_step()
    with pytest.raises(ValueError, match="destination_not_allowed"):
        await gate.handle_async_request(
            httpx.Request(
                "POST", "https://example.org/v1internal:streamGenerateContent"
            )
        )
    req = httpx.Request(
        "POST",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
    )
    await gate.handle_async_request(req)
    gate.start_step()
    with pytest.raises(ValueError, match="inference_budget_exhausted"):
        await gate.handle_async_request(req)
    assert len(calls) == 1
    await gate.aclose()


@pytest.mark.parametrize(
    "body", [frame([]), frame([{"text": "unexpected-secret-canary"}]), "data: {\n"]
)
async def test_http_200_is_not_sufficient_for_matrix_success(probe, body):
    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "p"})
        return httpx.Response(200, text=body)

    gate = probe.MatrixTransport(httpx.MockTransport(respond), max_requests=3)
    result = await probe.run_case("gemini-3.8-flash", "high", gate)
    assert result["passed"] is False
    assert len(result["steps"]) == 1
    assert result["steps"][0]["http_status"] == 200
    assert "secret-canary" not in json.dumps(result)


async def test_live_transport_reuses_production_client_configuration(
    probe, monkeypatch
):
    seen = []

    def respond(request):
        seen.append(request.url.host)
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=False
    )
    monkeypatch.setattr(probe, "make_client", lambda: client, raising=False)
    transport = probe.ProductionTransport()
    response = await transport.handle_async_request(
        httpx.Request(
            "POST",
            "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        )
    )
    assert await response.aread() == b"ok"
    assert seen == ["daily-cloudcode-pa.googleapis.com"]
    await response.aclose()
    await transport.aclose()
    assert client.is_closed


async def test_error_diagnostics_redact_account_and_credential_material(probe):
    def respond(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "message": "Invalid thinkingBudget for private-project and bearer-secret; contact person@example.com at https://example.com/private. opaque_abcdefghijklmnopqrstuvwxyz",
                }
            },
        )

    gate = probe.MatrixTransport(httpx.MockTransport(respond), max_requests=1)
    request = httpx.Request(
        "POST",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
        headers={"Authorization": "Bearer bearer-secret"},
        json={"project": "private-project"},
    )
    with pytest.raises(ValueError, match="http_400"):
        await gate.handle_async_request(request)
    result = json.dumps(gate.last_diagnostic)
    assert "thinkingBudget" in result
    for secret in (
        "private-project",
        "bearer-secret",
        "person@example.com",
        "https://example.com/private",
        "opaque_abcdefghijklmnopqrstuvwxyz",
    ):
        assert secret not in result
