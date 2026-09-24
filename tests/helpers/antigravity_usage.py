"""External credential-store and HTTP fixtures for Antigravity quota journeys."""

import json
import time

import httpx

from kohakuterrarium.llm import antigravity_auth as auth


def install_quota_script(monkeypatch):
    state = {"logged_in": False, "status": 200, "remaining": 0.75}
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: (
            [auth.BorrowedCredential("agy-usage-canary", time.time() + 3600, "test")]
            if state["logged_in"]
            else []
        ),
    )

    async def handle(self, request):
        assert request.url.host == "daily-cloudcode-pa.googleapis.com"
        assert request.headers["authorization"] == "Bearer agy-usage-canary"
        if request.url.path.endswith(":loadCodeAssist"):
            return httpx.Response(
                200, json={"cloudaicompanionProject": "project-canary"}
            )
        assert request.url.path.endswith(":retrieveUserQuotaSummary")
        assert json.loads(request.content) == {"project": "project-canary"}
        return httpx.Response(
            state["status"],
            json={
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "window": "5h",
                                "remainingFraction": state["remaining"],
                            }
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return state


def assert_quota(payload, used):
    assert payload["status"] == "ok"
    assert payload["groups"] == [
        {
            "id": "gemini",
            "windows": [
                {
                    "id": "gemini-5h",
                    "period": "5h",
                    "used_percent": used,
                    "resets_at": None,
                }
            ],
        }
    ]
    assert "canary" not in json.dumps(payload)
