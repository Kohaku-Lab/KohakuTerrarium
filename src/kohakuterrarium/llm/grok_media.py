"""Small authenticated HTTP client shared by Grok image and video tools."""

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.llm.grok_auth import GROK_CLI_SOURCE, GrokToken, GrokTokens


class GrokMediaError(RuntimeError):
    """A redacted xAI media request failure."""

    def __init__(
        self,
        status_code: int | None,
        operation: str,
        *,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        detail = f"HTTP {status_code}" if status_code is not None else "transport error"
        safe_code = _safe_error_field(code)
        safe_request_id = _safe_error_field(request_id)
        if safe_code:
            detail += f", code={safe_code}"
        if safe_request_id:
            detail += f", request_id={safe_request_id}"
        super().__init__(f"xAI {operation} failed with {detail}")


@dataclass(frozen=True)
class GrokMediaResponse:
    """Decoded response paired with the credential that created it."""

    data: dict[str, Any]
    token: GrokToken


class GrokMediaClient:
    """Call documented xAI media endpoints with ordered OAuth candidates."""

    def __init__(
        self,
        *,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
        _client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport
        self._client = _client

    @asynccontextmanager
    async def request_session(self) -> AsyncIterator["GrokMediaClient"]:
        """Yield an isolated client reused for one generation workflow."""
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            yield GrokMediaClient(
                timeout=self.timeout,
                transport=self.transport,
                _client=client,
            )

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: GrokToken | None = None,
        operation: str,
    ) -> GrokMediaResponse:
        """Send one request, falling back only on explicit auth rejection."""
        if token is None or token.source == GROK_CLI_SOURCE:
            await GrokTokens.ensure_fresh_cli()
        if token is not None:
            pending = [_current_cli_token(token)]
        else:
            pending = GrokTokens.load_candidates()
        if not pending:
            raise LLMNotConfiguredError(
                "No usable Grok subscription login was found for media generation"
            )

        last_status: int | None = None
        refresh_attempted = False
        while pending:
            candidate = pending.pop(0)
            try:
                response = await self._request(candidate, method, path, payload=payload)
            except httpx.TransportError as exc:
                raise GrokMediaError(None, operation) from exc
            last_status = response.status_code
            if (
                response.status_code == 401
                and candidate.source == GROK_CLI_SOURCE
                and not refresh_attempted
            ):
                refresh_attempted = True
                refreshed = await GrokTokens.ensure_fresh_cli(force=True)
                if refreshed is not None:
                    pending.insert(0, refreshed)
            if response.status_code in {401, 403} and pending:
                continue
            if response.status_code >= 400:
                raise _response_error(response, operation)
            try:
                data = response.json()
            except ValueError as exc:
                raise GrokMediaError(response.status_code, operation) from exc
            if not isinstance(data, dict):
                raise GrokMediaError(response.status_code, operation)
            return GrokMediaResponse(data=data, token=candidate)
        raise GrokMediaError(last_status, operation)

    async def download_bytes(self, url: str, *, max_bytes: int) -> tuple[bytes, str]:
        """Download a temporary xAI media URL with a strict bounded policy."""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "x.ai" or hostname.endswith(".x.ai")
        ):
            raise GrokMediaError(None, "media download")
        try:
            if self._client is not None:
                return await self._download_bytes(self._client, url, max_bytes)
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                return await self._download_bytes(client, url, max_bytes)
        except httpx.TransportError as exc:
            raise GrokMediaError(None, "media download") from exc

    @staticmethod
    async def _download_bytes(
        client: httpx.AsyncClient, url: str, max_bytes: int
    ) -> tuple[bytes, str]:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400 or response.is_redirect:
                raise _response_error(response, "media download")
            length = response.headers.get("content-length")
            if length:
                try:
                    too_large = int(length) > max_bytes
                except ValueError as exc:
                    raise GrokMediaError(
                        response.status_code, "media download"
                    ) from exc
                if too_large:
                    raise GrokMediaError(response.status_code, "media download")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise GrokMediaError(response.status_code, "media download")
                chunks.append(chunk)
            mime = response.headers.get("content-type", "").split(";", 1)[0]
            return b"".join(chunks), mime

    async def _request(
        self,
        token: GrokToken,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Content-Type": "application/json",
        }
        token_auth = token.extra_headers.get("X-XAI-Token-Auth")
        if token_auth:
            headers["X-XAI-Token-Auth"] = token_auth
        url = f"{token.media_base_url.rstrip('/')}/{path.lstrip('/')}"
        kwargs = {"json": payload} if payload is not None else {}
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, **kwargs)
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            return await client.request(method, url, headers=headers, **kwargs)


def _current_cli_token(token: GrokToken) -> GrokToken:
    if token.source != GROK_CLI_SOURCE:
        return token
    for candidate in GrokTokens.load_candidates():
        if candidate.source == GROK_CLI_SOURCE:
            return candidate
    return token


def _response_error(response: httpx.Response, operation: str) -> GrokMediaError:
    code = None
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code") or error.get("type")
        if code is None:
            code = data.get("code")
    request_id = response.headers.get("x-request-id") or response.headers.get(
        "request-id"
    )
    return GrokMediaError(
        response.status_code,
        operation,
        code=str(code) if code is not None else None,
        request_id=request_id,
    )


def _safe_error_field(value: str | None) -> str | None:
    if not value:
        return None
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "", str(value))[:80]
    return safe or None


__all__ = ["GrokMediaClient", "GrokMediaError", "GrokMediaResponse"]
