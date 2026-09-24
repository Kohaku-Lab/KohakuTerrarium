"""Fixed-destination transport for the agy consumer Cloud Code protocol."""

import asyncio
import hashlib

import httpx

from kohakuterrarium.llm.antigravity_auth import AgyCredentials, AntigravityError
from kohakuterrarium.llm.recovery import ErrorClass, RetryPolicy

BASE_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:"
METADATA = {
    "ideType": "ANTIGRAVITY",
    "platform": "WINDOWS_AMD64",
    "pluginType": "GEMINI",
}


class RequestError(AntigravityError):
    def __init__(self, status: int):
        self.status = status
        super().__init__("http_" + str(status))


def headers(token, operation: str) -> dict:
    return {
        "Authorization": "Bearer " + token.access_token,
        "Content-Type": "application/json",
        "User-Agent": f"antigravity-cli/1.2.9/{operation} (windows; x86_64)",
        "X-Goog-Api-Client": "google-api-go-client/0.5",
        "Client-Metadata": "ideType=ANTIGRAVITY,platform=WINDOWS_AMD64,pluginType=GEMINI",
    }


def url(operation: str) -> str:
    if operation not in {
        "loadCodeAssist",
        "fetchAvailableModels",
        "streamGenerateContent",
    }:
        raise AntigravityError("unsupported_operation")
    return (
        BASE_URL
        + operation
        + ("?alt=sse" if operation == "streamGenerateContent" else "")
    )


def check_status(response):
    if response.status_code != 200:
        raise RequestError(response.status_code)


def retryable(exc: Exception, policy: RetryPolicy) -> bool:
    category = None
    if isinstance(exc, RequestError):
        category = (
            ErrorClass.RATE_LIMIT
            if exc.status == 429
            else (ErrorClass.SERVER if 500 <= exc.status < 600 else None)
        )
    elif isinstance(exc, httpx.TransportError):
        category = ErrorClass.TRANSIENT
    return category in policy.retry_classes


async def backoff(policy: RetryPolicy, attempt: int):
    await asyncio.sleep(
        max(0, min(policy.max_delay, policy.base_delay * 2 ** min(attempt, 10)))
    )


async def post_json(
    client, operation: str, payload: dict, policy: RetryPolicy
) -> tuple:
    rejected, rotated = None, False
    for attempt in range(max(0, policy.max_retries) + 2):
        token = await AgyCredentials.ensure_fresh(rejected=rejected)
        rejected = None
        try:
            response = await client.post(
                url(operation), headers=headers(token, operation), json=payload
            )
            check_status(response)
            if len(response.content) > 4_194_304:
                raise AntigravityError("oversized_response")
            try:
                value = response.json()
            except ValueError:
                raise AntigravityError("malformed_response") from None
            if not isinstance(value, dict):
                raise AntigravityError("malformed_response")
            return value, token
        except (RequestError, httpx.TransportError) as exc:
            if isinstance(exc, RequestError) and exc.status == 401 and not rotated:
                rejected, rotated = token.fingerprint, True
                continue
            if attempt < policy.max_retries and retryable(exc, policy):
                await backoff(policy, attempt)
                continue
            if isinstance(exc, RequestError):
                raise exc from None
            raise AntigravityError("network_error") from None
    raise AntigravityError("retry_exhausted")


async def load_project(client, policy: RetryPolicy) -> tuple:
    for _ in range(3):
        data, token = await post_json(
            client, "loadCodeAssist", {"metadata": METADATA}, policy
        )
        current = await AgyCredentials.ensure_fresh()
        if current.fingerprint != token.fingerprint:
            continue
        project = data.get("cloudaicompanionProject")
        if isinstance(project, dict):
            project = project.get("id")
        if not isinstance(project, str) or not project:
            raise AntigravityError("account_not_onboarded")
        return project, hashlib.sha256(project.encode()).hexdigest(), current
    raise AntigravityError("credentials_changed_during_request")


def make_client(transport=None):
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=httpx.Timeout(90, connect=15),
    )


async def discover_models(*, transport=None) -> list[dict]:
    async with make_client(transport) as client:
        data, _ = await post_json(client, "fetchAvailableModels", {}, RetryPolicy())
    models = data.get("models")
    if not isinstance(models, dict):
        raise AntigravityError("malformed_model_catalog")
    return [
        {"id": name, "name": value.get("displayName", name)}
        for name, value in sorted(models.items())
        if isinstance(name, str) and isinstance(value, dict)
    ]
