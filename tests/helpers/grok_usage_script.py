"""Script the Grok CLI billing seam for workflow tests.

Only the fixed billing host and the external CLI process are replaced.
Credential files, token parsing, and usage normalization stay real.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from kohakuterrarium.llm import grok_auth
from kohakuterrarium.studio.identity.grok_account import BILLING_URL

BILLING_HOST = "cli-chat-proxy.grok.com"
FAKE_ACCESS = "workflow-grok-access"
FAKE_REFRESHED = "workflow-grok-refreshed"
_END = datetime(2026, 9, 28, 15, 8, 17, 982351, tzinfo=timezone.utc)
END_EPOCH = _END.timestamp()


def billing_body(used_percent: float) -> dict:
    """Return a weekly billing body with one product and a zero balance."""
    end = _END.isoformat()
    return {
        "config": {
            "creditUsagePercent": used_percent,
            "prepaidBalance": {"val": 0},
            "currentPeriod": {
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
                "start": "2026-09-21T15:08:17.982351+00:00",
                "end": end,
            },
            "productUsage": [{"product": "GrokBuild", "usagePercent": used_percent}],
        }
    }


def install_grok_home(root: Path, monkeypatch, *, executable: bool = True) -> Path:
    """Point Grok and OpenCode auth at ``root`` and clear the version cache."""
    grok_home = root / "grok"
    grok_home.mkdir(parents=True, exist_ok=True)
    if executable:
        bundled = grok_home / "bin" / ("grok.exe" if os.name == "nt" else "grok")
        bundled.parent.mkdir(parents=True, exist_ok=True)
        bundled.write_bytes(b"workflow grok placeholder")
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    monkeypatch.setenv("OPENCODE_AUTH_FILE", str(root / "opencode-missing.json"))
    grok_auth._grok_version_cache.clear()
    return grok_home


def write_cli_auth(grok_home: Path, access: str, *, hours: float = 24 * 400) -> None:
    """Write a CLI auth file whose expiry is far beyond the refresh window."""
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)
    (grok_home / "auth.json").write_text(
        json.dumps(
            {
                "issuer/client": {
                    "key": access,
                    "expires_at": expires.isoformat().replace("+00:00", "Z"),
                }
            }
        ),
        encoding="utf-8",
    )


def write_metadata_version(grok_home: Path, version: str = "1.0.5") -> None:
    """Write the metadata version used when the CLI probe is disabled."""
    (grok_home / ".metadata_version").write_text(version + "\n", encoding="utf-8")


def patch_cli_version_probe(monkeypatch) -> None:
    """Disable the real ``grok --version`` subprocess and keep metadata."""
    monkeypatch.setattr(grok_auth, "_grok_cli_executable", lambda: None)
    grok_auth._grok_version_cache.clear()


class ScriptedBilling:
    """Answer only the fixed billing URL; every other host fails closed."""

    def __init__(self) -> None:
        self.responses: list[httpx.Response] = []
        self.requests: list[dict] = []

    def push(self, status_code: int, body: dict | None = None) -> None:
        """Queue one billing response. Bodies stay off non-200 replies."""
        if status_code == 200:
            self.responses.append(httpx.Response(200, json=body or {}))
            return
        self.responses.append(httpx.Response(status_code))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Record redacted request facts and return the next scripted reply."""
        if request.url.host != BILLING_HOST:
            raise AssertionError(
                f"billing script saw unexpected host {request.url.host}"
            )
        if str(request.url) != BILLING_URL:
            raise AssertionError(f"billing script saw unexpected url {request.url}")
        authorization = request.headers.get("authorization", "")
        self.requests.append(
            {
                "url": str(request.url),
                "authorization": authorization,
                "token_auth": request.headers.get("x-xai-token-auth"),
                "version": request.headers.get("x-grok-client-version"),
                "accept": request.headers.get("accept"),
            }
        )
        if not self.responses:
            raise AssertionError("billing script has no queued response")
        return self.responses.pop(0)


def install_billing_script(monkeypatch) -> ScriptedBilling:
    """Replace ``AsyncHTTPTransport.handle_async_request`` for this process."""
    script = ScriptedBilling()

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        return await script.handle_async_request(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _handle)
    return script


def assert_usage_ok(payload: dict, used_percent: float) -> None:
    """Pin one live weekly usage payload without credential material."""
    assert payload["status"] == "ok"
    assert payload["source"] == "live"
    assert payload["credential_source"] == "grok-cli"
    assert isinstance(payload["captured_at"], float)
    assert payload["window"] == {
        "period": "weekly",
        "used_percent": used_percent,
        "resets_at": END_EPOCH,
    }
    assert payload["products"] == [{"name": "GrokBuild", "used_percent": used_percent}]
    assert payload["prepaid_balance"] == 0
    dumped = json.dumps(payload)
    assert FAKE_ACCESS not in dumped
    assert FAKE_REFRESHED not in dumped


def assert_billing_request(seen: dict, access: str, *, version: str | None) -> None:
    """Pin the fixed billing request while the token stays in the header only."""
    assert seen["url"] == BILLING_URL
    assert seen["authorization"] == f"Bearer {access}"
    assert seen["token_auth"] == "xai-grok-cli"
    assert seen["accept"] == "application/json"
    assert seen["version"] == version


def assert_empty(payload: dict, status: str) -> None:
    """Pin a non-ok usage payload that carries no window or balance."""
    assert payload["status"] == status
    assert payload["source"] == "live"
    assert payload["captured_at"] is None
    assert payload["window"] is None
    assert payload["products"] == []
    assert payload["prepaid_balance"] is None
    assert FAKE_ACCESS not in json.dumps(payload)
