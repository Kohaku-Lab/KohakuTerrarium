"""Explicit Windows agy probes with redacted output and fixed Google destinations."""

import argparse
import base64
import ctypes
import json
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

BASE_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:"
OPERATIONS = {"loadCodeAssist", "fetchAvailableModels", "streamGenerateContent"}


class ProbeError(Exception):
    """A failure containing a fixed diagnostic code only."""


@dataclass(frozen=True)
class BorrowedCredential:
    access_token: str = field(repr=False)
    expires_at: datetime
    source: str


class WindowsCredential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def parse_credential(raw: str, source: str) -> BorrowedCredential:
    """Extract a consumer access token without retaining a refresh token."""
    try:
        if len(raw) > 65536:
            raise ValueError
        if raw.startswith("go-keyring-base64:"):
            raw = base64.b64decode(raw.split(":", 1)[1], validate=True).decode("utf-8")
        payload = json.loads(raw)
        if payload.get("auth_method") != "consumer":
            raise ValueError
        token = payload["token"]
        access = token["access_token"]
        if not isinstance(access, str) or not re.fullmatch(
            r"[A-Za-z0-9._~+/=-]+", access
        ):
            raise ValueError
        if token.get("token_type", "").lower() != "bearer":
            raise ValueError
        expiry = datetime.fromisoformat(token["expiry"].replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError
        return BorrowedCredential(access, expiry, source)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ProbeError("unsupported_or_malformed_credential") from None


def read_windows_keyring() -> str | None:
    """Read only the established agy generic credential target."""
    if os.name != "nt":
        raise ProbeError("windows_probe_only")
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(WindowsCredential)),
    ]
    library.CredReadW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.POINTER(WindowsCredential)()
    if not library.CredReadW("gemini:antigravity", 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ProbeError("credential_store_unavailable")
    try:
        length = pointer.contents.CredentialBlobSize
        if length > 65536:
            raise ProbeError("credential_too_large")
        return ctypes.string_at(pointer.contents.CredentialBlob, length).decode("utf-8")
    finally:
        library.CredFree(pointer)


def read_sources() -> dict[str, BorrowedCredential | None]:
    """Read the known keyring target and known agy fallback file."""
    raw = read_windows_keyring()
    sources = {
        "windows_keyring": parse_credential(raw, "windows_keyring") if raw else None
    }
    path = Path.home() / ".gemini/antigravity-cli/antigravity-oauth-token"
    if path.is_file():
        if path.stat().st_size > 65536:
            raise ProbeError("credential_too_large")
        sources["agy_file"] = parse_credential(
            path.read_text(encoding="utf-8"), "agy_file"
        )
    else:
        sources["agy_file"] = None
    return sources


def describe(credential: BorrowedCredential | None) -> dict:
    """Report source presence and validity, excluding all token material."""
    if credential is None:
        return {"state": "missing"}
    remaining = round(
        (credential.expires_at - datetime.now(timezone.utc)).total_seconds()
    )
    return {
        "source": credential.source,
        "state": "usable" if remaining > 60 else "expired",
        "auth_method": "consumer",
        "remaining_seconds": remaining,
    }


def select_credential() -> BorrowedCredential:
    sources = [value for value in read_sources().values() if value is not None]
    if not sources:
        raise ProbeError("login_required")
    if len(sources) != 1:
        raise ProbeError("ambiguous_credential_sources")
    credential = sources[0]
    if describe(credential)["state"] != "usable":
        raise ProbeError("auth_expired_run_agy")
    return credential


def operation_url(operation: str) -> str:
    if operation not in OPERATIONS:
        raise ProbeError("unsupported_operation")
    return (
        BASE_URL
        + operation
        + ("?alt=sse" if operation == "streamGenerateContent" else "")
    )


def safe_error(status: int, payload: dict) -> dict:
    """Keep only the HTTP status and the upstream status enum."""
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    enum = error.get("status", "") if isinstance(error, dict) else ""
    return {
        "http_status": status,
        "error_status": (
            enum
            if isinstance(enum, str) and re.fullmatch("[A-Z_]+", enum)
            else "UNKNOWN"
        ),
    }


def request_headers(
    credential: BorrowedCredential, operation: str, version: str
) -> dict:
    return {
        "Authorization": "Bearer " + credential.access_token,
        "Content-Type": "application/json",
        "User-Agent": f"antigravity-cli/{version}/{operation} (windows; x86_64)",
        "X-Goog-Api-Client": "google-api-go-client/0.5",
        "Client-Metadata": "ideType=ANTIGRAVITY,platform=WINDOWS_AMD64,pluginType=GEMINI",
    }


def checked_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        raise ProbeError("non_json_response") from None
    if response.status_code != 200:
        raise ProbeError(json.dumps(safe_error(response.status_code, data)))
    if not isinstance(data, dict):
        raise ProbeError("unexpected_response_schema")
    return data


def load_project(
    client: httpx.Client, credential: BorrowedCredential, version: str
) -> str:
    response = client.post(
        operation_url("loadCodeAssist"),
        headers=request_headers(credential, "loadCodeAssist", version),
        json={
            "metadata": {
                "ideType": "ANTIGRAVITY",
                "platform": "WINDOWS_AMD64",
                "pluginType": "GEMINI",
            }
        },
    )
    data = checked_json(response)
    project = data.get("cloudaicompanionProject")
    if isinstance(project, dict):
        project = project.get("id")
    if not isinstance(project, str) or not project:
        raise ProbeError("project_not_ready_complete_agy_onboarding")
    return project


def probe_discovery(version: str) -> dict:
    credential = select_credential()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        load_project(client, credential, version)
        response = client.post(
            operation_url("fetchAvailableModels"),
            headers=request_headers(credential, "fetchAvailableModels", version),
            json={},
        )
        data = checked_json(response)
    models = data.get("models")
    if not isinstance(models, dict):
        raise ProbeError("unexpected_models_schema")
    ids = sorted(
        key for key in models if re.fullmatch(r"(gemini|claude)[A-Za-z0-9._/-]*", key)
    )
    return {
        "stage": "discover",
        "project_ready": True,
        "model_count": len(models),
        "model_ids": ids,
    }


def probe_generation(version: str, model: str) -> dict:
    """Run one fixed short text prompt with the shared SSE collector."""
    if not re.fullmatch(r"(gemini|claude)[A-Za-z0-9._/-]*", model):
        raise ProbeError("invalid_model_id")
    credential = select_credential()
    started = time.monotonic()
    with httpx.Client(
        timeout=httpx.Timeout(30, connect=10), follow_redirects=False
    ) as client:
        project = load_project(client, credential, version)
        body = {
            "project": project,
            "model": model,
            "userPromptId": str(uuid4()),
            "request": {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": "Reply exactly AGY_PROBE_OK. Do not use tools."}
                        ],
                    }
                ],
                "generationConfig": {"maxOutputTokens": 128},
            },
        }
        parts, finish = stream_parts(client, credential, version, body)
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    return {
        "stage": "generate",
        "model": model,
        "http_status": 200,
        "expected_reply": text.strip() == "AGY_PROBE_OK",
        "text_characters": len(text),
        "finish_reason": finish,
        "signature_parts": sum(bool(part.get("thoughtSignature")) for part in parts),
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def probe_refresh(executable: str) -> dict:
    before = read_sources()
    started = time.monotonic()
    try:
        result = subprocess.run(
            [executable, "--output-format", "json", "models"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE="true"),
        )
    except subprocess.TimeoutExpired:
        raise ProbeError("agy_models_timeout") from None
    after = read_sources()
    changed = {
        key: bool(
            before.get(key) and value and before[key].access_token != value.access_token
        )
        for key, value in after.items()
    }
    return {
        "stage": "refresh",
        "returncode": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 2),
        "before": {key: describe(value) for key, value in before.items()},
        "after": {key: describe(value) for key, value in after.items()},
        "access_token_changed": changed,
    }


def stream_parts(
    client: httpx.Client, credential: BorrowedCredential, version: str, body: dict
) -> tuple[list[dict], str]:
    """Collect one small SSE turn in memory without persisting opaque parts."""
    parts = []
    finish = ""
    pending = []
    started = time.monotonic()
    with client.stream(
        "POST",
        operation_url("streamGenerateContent"),
        headers=request_headers(credential, body["model"], version),
        json=body,
    ) as response:
        if response.status_code != 200:
            response.read()
            checked_json(response)
        for line in response.iter_lines():
            if time.monotonic() - started > 60:
                raise ProbeError("probe_deadline_exceeded")
            if line.startswith("data:"):
                pending.append(line[5:].lstrip())
            elif not line and pending:
                data = "\n".join(pending)
                pending = []
                if data == "[DONE]":
                    continue
                event = json.loads(data)
                chunk = event.get("response", event)
                for candidate in chunk.get("candidates", []):
                    if candidate.get("index", 0) != 0:
                        continue
                    finish = candidate.get("finishReason") or finish
                    parts.extend(candidate.get("content", {}).get("parts", []))
    if not finish or pending:
        raise ProbeError("incomplete_stream")
    return parts, finish


def probe_tool_roundtrip(version: str, model: str) -> dict:
    """Generate one harmless function call and replay its exact signed parts."""
    if not re.fullmatch(r"(gemini|claude)[A-Za-z0-9._/-]*", model):
        raise ProbeError("invalid_model_id")
    credential = select_credential()
    prompt = "Call probe_echo exactly once with value AGY_PROBE_OK. After receiving the result, reply exactly with its value."
    request = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "probe_echo",
                        "description": "Return the supplied value without side effects.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"value": {"type": "STRING"}},
                            "required": ["value"],
                        },
                    }
                ]
            }
        ],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["probe_echo"],
            }
        },
        "generationConfig": {"maxOutputTokens": 512},
    }
    with httpx.Client(
        timeout=httpx.Timeout(30, connect=10), follow_redirects=False
    ) as client:
        project = load_project(client, credential, version)
        body = {
            "project": project,
            "model": model,
            "userPromptId": str(uuid4()),
            "request": request,
        }
        parts, first_finish = stream_parts(client, credential, version, body)
        calls = [part["functionCall"] for part in parts if part.get("functionCall")]
        if (
            len(calls) != 1
            or calls[0].get("name") != "probe_echo"
            or calls[0].get("args") != {"value": "AGY_PROBE_OK"}
        ):
            raise ProbeError("unexpected_probe_tool_call")
        request["contents"].extend(
            [
                {"role": "model", "parts": parts},
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "probe_echo",
                                "response": {"value": "AGY_PROBE_OK"},
                            }
                        }
                    ],
                },
            ]
        )
        request["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        body["userPromptId"] = str(uuid4())
        reply_parts, second_finish = stream_parts(client, credential, version, body)
    text = "".join(
        part.get("text", "") for part in reply_parts if not part.get("thought")
    )
    return {
        "stage": "tool-roundtrip",
        "model": model,
        "inference_calls": 2,
        "tool_name_valid": True,
        "tool_args_valid": True,
        "signature_parts": sum(bool(part.get("thoughtSignature")) for part in parts),
        "expected_reply": text.strip() == "AGY_PROBE_OK",
        "first_finish": first_finish,
        "second_finish": second_finish,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=["inspect", "refresh", "discover", "generate", "tool-roundtrip"],
    )
    parser.add_argument("--agy-executable", default=shutil.which("agy"))
    parser.add_argument("--version", default="1.2.8")
    parser.add_argument("--model")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
            raise ProbeError("invalid_cli_version")
        match args.stage:
            case "inspect":
                result = {
                    "stage": "inspect",
                    "sources": {
                        key: describe(value) for key, value in read_sources().items()
                    },
                }
            case "refresh":
                if not args.agy_executable:
                    raise ProbeError("agy_not_installed")
                result = probe_refresh(args.agy_executable)
            case "discover":
                result = probe_discovery(args.version)
            case "generate" | "tool-roundtrip":
                if not args.model:
                    raise ProbeError("model_required")
                result = (
                    probe_tool_roundtrip
                    if args.stage == "tool-roundtrip"
                    else probe_generation
                )(args.version, args.model)
        print(json.dumps(result, indent=2))
        return 0
    except ProbeError as error:
        print(json.dumps({"stage": args.stage, "error": str(error)}))
        return 1
    except Exception as error:
        print(
            json.dumps(
                {
                    "stage": args.stage,
                    "error_type": type(error).__name__,
                    "details": "redacted",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
