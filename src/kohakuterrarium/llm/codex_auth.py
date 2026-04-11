"""
Codex OAuth PKCE authentication for ChatGPT subscription access.

Two flows:
- Browser flow (local machine): OAuth PKCE redirect to localhost
- Device code flow (headless/SSH): user visits URL and enters code

Token search order:
  1. ~/.kohakuterrarium/codex-auth.json  (our own cache)
  2. ~/.codex/auth.json                  (Codex CLI cache)
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

ISSUER = "https://auth.openai.com"
AUTH_URL = f"{ISSUER}/oauth/authorize"
TOKEN_URL = f"{ISSUER}/oauth/token"
DEVICE_USERCODE_URL = f"{ISSUER}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{ISSUER}/api/accounts/deviceauth/token"
DEVICE_VERIFY_URL = f"{ISSUER}/codex/device"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"
DEVICE_REDIRECT_URI = f"{ISSUER}/deviceauth/callback"
SCOPE = "openid email profile"
AUDIENCE = "https://api.openai.com/v1"

DEFAULT_TOKEN_PATH = Path.home() / ".kohakuterrarium" / "codex-auth.json"
CODEX_CLI_TOKEN_PATH = Path.home() / ".codex" / "auth.json"


@dataclass
class CodexTokens:
    """OAuth token set for Codex backend access."""

    access_token: str
    refresh_token: str
    expires_at: float = 0.0

    def is_expired(self) -> bool:
        """Check if the access token is expired (with 60s safety buffer)."""
        return time.time() >= self.expires_at - 60

    def save(self, path: Path | None = None) -> None:
        """Persist tokens to disk."""
        p = path or DEFAULT_TOKEN_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_at": self.expires_at,
                }
            )
        )
        logger.debug("Tokens saved", path=str(p))

    @classmethod
    def load(cls, path: Path | None = None) -> "CodexTokens | None":
        """Load tokens from disk. Tries our path, then Codex CLI fallback."""
        candidates = [path or DEFAULT_TOKEN_PATH, CODEX_CLI_TOKEN_PATH]
        for p in candidates:
            if p and p.exists():
                try:
                    data = json.loads(p.read_text())
                    tokens = cls(
                        access_token=data.get("access_token", ""),
                        refresh_token=data.get("refresh_token", ""),
                        expires_at=data.get("expires_at", 0),
                    )
                    if tokens.access_token:
                        logger.info("Tokens loaded", path=str(p))
                        return tokens
                except Exception as e:
                    logger.warning("Failed to load tokens", path=str(p), error=str(e))
        return None


# =========================================================================
# PKCE Helpers
# =========================================================================


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def _build_auth_url(code_challenge: str, state: str) -> str:
    """Construct the OAuth authorization URL with PKCE parameters."""
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "audience": AUDIENCE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _is_headless() -> bool:
    """Detect if running in a headless environment (no display)."""
    if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
        return True
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        # No display on Linux
        if os.name != "nt":  # Not Windows
            return True
    return False


# =========================================================================
# Browser Flow (local machine)
# =========================================================================


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Captures the OAuth callback on localhost."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        server: Any = self.server
        server.auth_code = qs.get("code", [None])[0]
        server.callback_state = qs.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body>"
            b"<h2>Authentication successful!</h2>"
            b"<p>You can close this tab.</p>"
            b"</body></html>"
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


async def _browser_flow() -> CodexTokens:
    """OAuth PKCE flow with browser redirect to localhost."""
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)
    auth_url = _build_auth_url(code_challenge, state)

    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _OAuthCallbackHandler)
    server.auth_code = None  # type: ignore[attr-defined]
    server.callback_state = None  # type: ignore[attr-defined]
    server.timeout = 300

    received = asyncio.Event()

    def _serve_once() -> None:
        server.handle_request()
        received._loop.call_soon_threadsafe(received.set)  # type: ignore[attr-defined]

    loop = asyncio.get_running_loop()
    received._loop = loop  # type: ignore[attr-defined]

    thread = Thread(target=_serve_once, daemon=True)
    thread.start()

    print("[Browser] Opening authentication URL:")
    print(auth_url)
    print()
    webbrowser.open(auth_url)

    try:
        await asyncio.wait_for(received.wait(), timeout=300)
    except asyncio.TimeoutError:
        raise RuntimeError("OAuth login timed out (300s)")
    finally:
        server.server_close()

    if server.callback_state != state:  # type: ignore[attr-defined]
        raise RuntimeError("OAuth state mismatch")
    auth_code = server.auth_code  # type: ignore[attr-defined]
    if not auth_code:
        raise RuntimeError("No authorization code received")

    return await _exchange_code(auth_code, code_verifier)


# =========================================================================
# Device Code Flow (headless/SSH)
# =========================================================================


async def _device_code_flow() -> CodexTokens:
    """OAuth device code flow for headless environments.

    Uses OpenAI's Codex-specific device auth endpoints:
    1. POST /api/accounts/deviceauth/usercode -> get device_auth_id + user_code
    2. User visits /codex/device and enters user_code
    3. Poll /api/accounts/deviceauth/token until user completes auth
    4. Server returns authorization_code + PKCE codes
    5. Exchange at /oauth/token for access + refresh tokens
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            DEVICE_USERCODE_URL,
            json={"client_id": CLIENT_ID},
        )
        resp.raise_for_status()
        data = resp.json()

    device_auth_id = data["device_auth_id"]
    user_code = data["user_code"]
    interval = int(data.get("interval", 5))
    if "expires_at" in data:
        expires_at_dt = datetime.fromisoformat(data["expires_at"])
        expires_in = max(
            60, int(expires_at_dt.astimezone(timezone.utc).timestamp() - time.time())
        )
    else:
        expires_in = int(data.get("expires_in", 900))

    print(f"[Device] Or visit: {DEVICE_VERIFY_URL}")
    print(f"  Code: {user_code}")
    print()
    print("Waiting for authentication (either method)...")

    deadline = time.time() + expires_in
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            await asyncio.sleep(interval)
            resp = await client.post(
                DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": device_auth_id,
                    "user_code": user_code,
                },
            )

            if resp.status_code == 200:
                token_data = resp.json()
                # Server returns authorization_code + PKCE codes
                auth_code = token_data.get("authorization_code", "")
                code_verifier = token_data.get("code_verifier", "")

                if auth_code and code_verifier:
                    # Exchange using device-specific redirect_uri
                    return await _exchange_code(
                        auth_code, code_verifier, DEVICE_REDIRECT_URI
                    )

                # Some responses may return tokens directly
                if "access_token" in token_data:
                    tokens = CodexTokens(
                        access_token=token_data["access_token"],
                        refresh_token=token_data.get("refresh_token", ""),
                        expires_at=time.time() + token_data.get("expires_in", 3600),
                    )
                    tokens.save()
                    logger.info("Device code login successful")
                    return tokens

            # Handle pending/error responses
            if resp.status_code in (403, 400, 428):
                try:
                    error_data = resp.json()
                    raw_error = error_data.get("error", "")
                    # error field may be a string or a dict {"message": ..., "type": ...}
                    if isinstance(raw_error, dict):
                        error = raw_error.get("code", raw_error.get("type", "unknown"))
                        error_msg = raw_error.get("message", str(raw_error))
                    else:
                        error = raw_error
                        error_msg = error
                except Exception as e:
                    logger.debug("Failed to parse error response JSON", error=str(e))
                    continue
                if error in (
                    "authorization_pending",
                    "pending",
                    "deviceauth_authorization_unknown",  # user hasn't completed yet
                ):
                    continue
                if error == "slow_down":
                    interval += 5  # slow down
                    continue
                if error in ("expired_token", "access_denied"):
                    raise RuntimeError(f"Device code auth failed: {error}")
                # Any other unrecognised error is fatal — don't loop until timeout
                raise RuntimeError(f"Device code auth error: {error_msg}")

    raise RuntimeError("Device code auth timed out")


# =========================================================================
# Token Exchange
# =========================================================================


async def _exchange_code(
    auth_code: str, code_verifier: str, redirect_uri: str = REDIRECT_URI
) -> CodexTokens:
    """Exchange authorization code for tokens.

    Uses application/x-www-form-urlencoded (matching Codex CLI).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            logger.error(
                "Token exchange failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
            resp.raise_for_status()
        data = resp.json()

    tokens = CodexTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=time.time() + int(data.get("expires_in", 3600)),
    )
    tokens.save()
    logger.info("OAuth login successful")
    return tokens


# =========================================================================
# Public API
# =========================================================================


async def oauth_login() -> CodexTokens:
    """
    Authenticate with OpenAI Codex OAuth.

    Runs BOTH flows simultaneously - browser redirect AND device code.
    Whichever completes first wins. This handles all environments:
    - Local machine: browser opens, redirect catches it
    - SSH/headless: user visits URL on another device, enters code
    - Remote desktop: either flow may work
    - Windows reserved port: browser fails fast, device code continues
    """
    # Start both flows as concurrent tasks
    browser_task = asyncio.create_task(_browser_flow_safe())
    device_task = asyncio.create_task(_device_code_flow())

    tasks = {browser_task, device_task}
    last_error: Exception | None = None

    while tasks:
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                tokens = task.result()
                # Success — cancel the remaining task if any
                for remaining in tasks:
                    remaining.cancel()
                    try:
                        await remaining
                    except (asyncio.CancelledError, Exception):
                        pass
                return tokens
            except Exception as e:
                logger.warning("Auth flow failed", error=str(e))
                last_error = e
                # Other task may still succeed — keep waiting

    raise RuntimeError(f"All authentication flows failed: {last_error}")


async def _browser_flow_safe() -> CodexTokens:
    """Browser flow that doesn't crash if port is busy or browser fails."""
    try:
        return await _browser_flow()
    except OSError as e:
        # Port already in use / permission denied (e.g. Windows reserved ports)
        logger.debug("Browser flow unavailable", error=str(e))
        raise RuntimeError(f"Browser flow unavailable: {e}") from e


async def refresh_tokens(tokens: CodexTokens) -> CodexTokens:
    """Refresh an expired access token using the refresh token."""
    if not tokens.refresh_token:
        raise RuntimeError("No refresh token available - please re-authenticate")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()

    new_tokens = CodexTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", tokens.refresh_token),
        expires_at=time.time() + int(data.get("expires_in", 3600)),
    )
    new_tokens.save()
    logger.info("Tokens refreshed")
    return new_tokens
