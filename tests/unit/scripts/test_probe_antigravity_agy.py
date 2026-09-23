"""Offline checks for the explicit, redacted Antigravity experiment."""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/probe_antigravity_agy.py"


@pytest.fixture
def probe():
    assert SCRIPT.is_file(), "The reusable probe has not been implemented"
    spec = importlib.util.spec_from_file_location("agy_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def payload(**overrides):
    data = {
        "auth_method": "consumer",
        "token": {
            "access_token": "access-secret-canary",
            "refresh_token": "refresh-secret-canary",
            "token_type": "Bearer",
            "expiry": "2099-01-01T00:00:00Z",
        },
    }
    data.update(overrides)
    return json.dumps(data)


@pytest.mark.parametrize("encoded", [False, True])
def test_borrows_only_access_and_redacts_output(probe, encoded):
    raw = payload()
    if encoded:
        raw = "go-keyring-base64:" + base64.b64encode(raw.encode()).decode()
    credential = probe.parse_credential(raw, "fixture")
    assert credential.access_token == "access-secret-canary"
    assert not hasattr(credential, "refresh_token")
    assert credential.expires_at.year == 2099
    summary = json.dumps(probe.describe(credential))
    assert "access-secret-canary" not in summary + repr(credential)
    assert "refresh-secret-canary" not in summary + repr(credential)
    assert probe.describe(credential)["state"] == "usable"


@pytest.mark.parametrize(
    "raw",
    [
        "{secret-canary",
        "go-keyring-base64:!!!secret-canary",
        payload(auth_method="firstparty"),
        payload(token={"access_token": "secret-canary", "expiry": "not-a-date"}),
        payload(
            token={
                "access_token": "secret-canary\r\nX-Test: injected",
                "token_type": "Bearer",
                "expiry": "2099-01-01T00:00:00Z",
            }
        ),
    ],
)
def test_rejects_invalid_credentials_without_echoing_input(probe, raw):
    with pytest.raises(probe.ProbeError) as error:
        probe.parse_credential(raw, "fixture")
    assert "secret-canary" not in str(error.value)


def test_expiry_is_observed_without_changing_the_source(probe):
    value = json.loads(payload())
    value["token"]["expiry"] = "2000-01-01T00:00:00Z"
    credential = probe.parse_credential(json.dumps(value), "fixture")
    assert probe.describe(credential)["state"] == "expired"
    assert credential.access_token == "access-secret-canary"


def test_error_report_never_returns_the_upstream_body(probe):
    report = probe.safe_error(
        403,
        {
            "error": {
                "status": "PERMISSION_DENIED",
                "message": "secret-canary project private-account@example.test",
            }
        },
    )
    assert report["http_status"] == 403
    assert report["error_status"] == "PERMISSION_DENIED"
    assert "secret-canary" not in json.dumps(report)
    assert "private-account" not in json.dumps(report)


def test_unsupported_operations_cannot_select_an_arbitrary_destination(probe):
    with pytest.raises(probe.ProbeError):
        probe.operation_url("https://unrelated.example/collect")
    assert (
        probe.operation_url("loadCodeAssist")
        == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
    )


def test_discovery_uses_only_google_and_redacts_project(probe, monkeypatch):
    credential = probe.parse_credential(payload(), "fixture")
    monkeypatch.setattr(probe, "select_credential", lambda: credential)
    client_class = httpx.Client
    seen = []

    def respond(request):
        seen.append(request.url.path)
        assert request.url.host == "daily-cloudcode-pa.googleapis.com"
        assert request.headers["authorization"] == "Bearer access-secret-canary"
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(
                200, json={"cloudaicompanionProject": "project-secret-canary"}
            )
        assert request.url.path.endswith("fetchAvailableModels")
        assert json.loads(request.content) == {}
        return httpx.Response(
            200,
            json={"models": {"gemini-probe-model": {}, "internal-private-name": {}}},
        )

    monkeypatch.setattr(
        probe.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = probe.probe_discovery("1.2.8")
    assert result == {
        "stage": "discover",
        "project_ready": True,
        "model_count": 2,
        "model_ids": ["gemini-probe-model"],
    }
    assert seen == ["/v1internal:loadCodeAssist", "/v1internal:fetchAvailableModels"]
    assert "secret-canary" not in json.dumps(result)


def test_generation_sends_fixed_probe_and_requires_complete_stream(probe, monkeypatch):
    credential = probe.parse_credential(payload(), "fixture")
    monkeypatch.setattr(probe, "select_credential", lambda: credential)
    client_class = httpx.Client

    def respond(request):
        assert request.url.host == "daily-cloudcode-pa.googleapis.com"
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(
                200, json={"cloudaicompanionProject": "project-secret-canary"}
            )
        body = json.loads(request.content)
        assert body["project"] == "project-secret-canary"
        assert body["request"]["contents"] == [
            {
                "role": "user",
                "parts": [{"text": "Reply exactly AGY_PROBE_OK. Do not use tools."}],
            }
        ]
        assert body["request"]["generationConfig"]["maxOutputTokens"] == 128
        assert request.url.query == b"alt=sse"
        return httpx.Response(
            200,
            text='data: {"response":{"candidates":[{"content":{"parts":[{"text":"AGY_PROBE_OK"}]},"finishReason":"STOP"}]}}\n\n',
        )

    monkeypatch.setattr(
        probe.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = probe.probe_generation("1.2.8", "gemini-probe-model")
    assert result["expected_reply"] is True
    assert result["finish_reason"] == "STOP"
    assert "secret-canary" not in json.dumps(result)


def test_incomplete_sse_is_not_success(probe, monkeypatch):
    monkeypatch.setattr(
        probe, "select_credential", lambda: probe.parse_credential(payload(), "fixture")
    )
    client_class = httpx.Client

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(
                200, json={"cloudaicompanionProject": "project-secret-canary"}
            )
        return httpx.Response(
            200,
            text='data: {"response":{"candidates":[{"content":{"parts":[{"text":"partial"}]}}]}}\n\n',
        )

    monkeypatch.setattr(
        probe.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(probe.ProbeError, match="incomplete_stream"):
        probe.probe_generation("1.2.8", "gemini-probe-model")


def test_tool_probe_replays_exact_parts_and_pairs_the_function_result(
    probe, monkeypatch
):
    monkeypatch.setattr(
        probe, "select_credential", lambda: probe.parse_credential(payload(), "fixture")
    )
    client_class = httpx.Client
    turns = []
    original_parts = [
        {
            "functionCall": {"name": "probe_echo", "args": {"value": "AGY_PROBE_OK"}},
            "thoughtSignature": "signature-canary",
        }
    ]

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(
                200, json={"cloudaicompanionProject": "project-canary"}
            )
        body = json.loads(request.content)
        turns.append(body)
        if len(turns) == 1:
            assert (
                body["request"]["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
            )
            parts = original_parts
        else:
            assert body["request"]["contents"][1] == {
                "role": "model",
                "parts": original_parts,
            }
            assert body["request"]["contents"][2] == {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "probe_echo",
                            "response": {"value": "AGY_PROBE_OK"},
                        }
                    }
                ],
            }
            parts = [{"text": "AGY_PROBE_OK"}]
        return httpx.Response(
            200,
            text="data: "
            + json.dumps(
                {
                    "response": {
                        "candidates": [
                            {"content": {"parts": parts}, "finishReason": "STOP"}
                        ]
                    }
                }
            )
            + "\n\n",
        )

    monkeypatch.setattr(
        probe.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = probe.probe_tool_roundtrip("1.2.8", "gemini-probe-model")
    assert len(turns) == 2
    assert result["tool_name_valid"] is True
    assert result["tool_args_valid"] is True
    assert result["signature_parts"] == 1
    assert result["expected_reply"] is True
    assert "canary" not in json.dumps(result)
