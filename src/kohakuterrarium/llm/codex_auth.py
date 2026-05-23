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

from kohakuterrarium.utils.config_dir import config_dir
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

CODEX_CLI_TOKEN_PATH = Path.home() / ".codex" / "auth.json"


def _default_token_path() -> Path:
    """The Codex token cache path, honouring ``KT_CONFIG_DIR``.

    Resolved fresh each call so test isolation / operator re-homing
    works — a module constant computed at import would not.  Same
    config root as API keys + LLM profiles, so a host that is
    ``kt login``-ed for Codex stores the token where the lab-host's
    identity surface can read and share it with workers.
    """
    return config_dir() / "codex-auth.json"


# Back-compat: callers that imported the constant for *display* still
# resolve; the live read / write path uses :func:`_default_token_path`.
DEFAULT_TOKEN_PATH = Path.home() / ".kohakuterrarium" / "codex-auth.json"


# Worker-side resolver: when set, ``CodexTokens.load()`` consults this
# *before* the local on-disk paths.  Workers register a resolver that
# fetches tokens from the host's identity store via the
# ``studio.identity`` RPC + :class:`IdentityCache`, mirroring the api
# key resolver pattern in :mod:`llm.api_keys`.  Production / standalone
# runs never set the resolver, so the local-file load is unchanged.
_resolver: Any = None  # Callable[[], "CodexTokens | None"] | None


def register_codex_resolver(fn) -> None:
    """Install a worker-side codex-token resolver.

    The resolver is called from :meth:`CodexTokens.load` and must be a
    synchronous callable returning a :class:`CodexTokens` or ``None``.
    """
    global _resolver
    _resolver = fn


def clear_codex_resolver() -> None:
    """Remove any installed resolver.  Idempotent."""
    global _resolver
    _resolver = None


@dataclass
class CodexTokens:
    """OAuth token set for Codex backend access.

    `access_token` is used for the OpenAI API (`api.openai.com`).
    `id_token` is the OIDC JWT used for ChatGPT backend endpoints
    (e.g. `chatgpt.com/backend-api/codex/usage`). Codex CLI stores
    both; we do too.
    """

    access_token: str
    refresh_token: str
    expires_at: float = 0.0
    id_token: str = ""
    account_id: str = ""

    def is_expired(self) -> bool:
        """Check if the access token is expired (with 60s safety buffer)."""
        return time.time() >= self.expires_at - 60

    def save(self, path: Path | None = None) -> None:
        """Persist tokens to disk."""
        p = path or _default_token_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_at": self.expires_at,
                    "id_token": self.id_token,
                    "account_id": self.account_id,
                }
            )
        )
        logger.debug("Tokens saved", path=str(p))

    @classmethod
    def load(cls, path: Path | None = None) -> "CodexTokens | None":
        """Load tokens from disk.

        If `path` is given, only that file is tried. Otherwise:

        - **Worker mode** (``register_codex_resolver`` installed): the
          resolver is consulted first.  Worker-mode miss returns
          ``None`` immediately so the worker's local file is not
          silently substituted — same host-canonical rule as the api
          key resolver in :mod:`llm.api_keys`.
        - **Standalone**: we try our default path first, then fall back
          to the Codex CLI file at ``~/.codex/auth.json``.

        Supports two on-disk shapes:
          1. Our flat shape: {access_token, refresh_token, expires_at, ...}
          2. Codex CLI shape: {tokens: {id_token, access_token, refresh_token,
             account_id}, last_refresh: ISO8601}
        """
        if path is None and _resolver is not None:
            try:
                tokens = _resolver()
            except Exception:  # pragma: no cover - defensive
                logger.exception("codex resolver raised; treating as miss")
                tokens = None
            if isinstance(tokens, CodexTokens) and tokens.access_token:
                return tokens
            return None
        if path is not None:
            candidates = [path]
        else:
            candidates = [_default_token_path(), CODEX_CLI_TOKEN_PATH]
        for p in candidates:
            if p and p.exists():
                try:
                    data = json.loads(p.read_text())
                    tokens = cls._from_dict(data)
                    if tokens and tokens.access_token:
                        logger.info("Tokens loaded", path=str(p))
                        return tokens
                except Exception as e:
                    logger.warning("Failed to load tokens", path=str(p), error=str(e))
        return None

    @classmethod
    def _from_dict(cls, data: dict) -> "CodexTokens | None":
        # Codex CLI nested format.
        if isinstance(data.get("tokens"), dict):
            t = data["tokens"]
            # CLI stores ISO8601 last_refresh, not expires_at. Treat it
            # as "refreshed now" — is_expired() will trigger a refresh
            # on first use if the token is actually stale.
            expires_at = cls._parse_expires_at(data.get("last_refresh"))
            return cls(
                access_token=t.get("access_token", ""),
                refresh_token=t.get("refresh_token", ""),
                expires_at=expires_at,
                id_token=t.get("id_token", ""),
                account_id=t.get("account_id", ""),
            )
        # Our flat format.
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_at=float(data.get("expires_at", 0) or 0),
            id_token=data.get("id_token", ""),
            account_id=data.get("account_id", ""),
        )

    @staticmethod
    def _parse_expires_at(last_refresh: str | None) -> float:
        """CLI's `last_refresh` is ISO8601; convert to epoch + ~1h window."""
        if not last_refresh:
            return 0.0
        try:
            # Access tokens last ~1h; assume the same window the CLI uses.
            return (
                datetime.fromisoformat(last_refresh.replace("Z", "+00:00")).timestamp()
                + 3600
            )
        except Exception:
            return 0.0


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


async def _browser_flow(open_browser: bool = True) -> CodexTokens:
    """OAuth PKCE flow with browser redirect to localhost.

    The HTTP callback server on ``127.0.0.1:REDIRECT_PORT`` always
    starts; ``open_browser=False`` only suppresses the local
    ``webbrowser.open()`` call.  A user who manually clicks the
    printed auth URL still completes via the browser-redirect path.
    The SSE / WebView modal route passes ``open_browser=False`` —
    auto-popping a system browser on the server's machine is
    redundant (web UI = same machine) and on Android Chaquopy
    blocks the event loop hunting for a non-existent browser.
    """
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

    print("[Browser] Authentication URL:")
    print(auth_url)
    print()
    if open_browser:
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


async def _device_code_flow(
    on_device_code: "callable | None" = None,
) -> CodexTokens:
    """OAuth device code flow for headless / WebView environments.

    Polling matches codex-rs verbatim: 2xx = success, 403/404 = keep
    polling, anything else = abort.  The earlier 400/428 + error-
    JSON-parsing branch was a superset that occasionally tripped on
    transient errors and bailed the modal early.

    Success body: ``{authorization_code, code_verifier, code_challenge}``;
    exchanged at ``/oauth/token`` with the device-specific redirect_uri.

    ``on_device_code(verification_url, user_code, expires_in)`` is
    invoked as soon as the user-code is obtained, before any poll,
    so the SSE route can push it to the modal.
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

    # Push the user code + URL to any subscribed event sink (SSE /
    # WebSocket modal) so the frontend can show them immediately
    # instead of waiting for the entire poll-and-exchange to finish.
    if on_device_code is not None:
        try:
            res = on_device_code(DEVICE_VERIFY_URL, user_code, expires_in)
            if asyncio.iscoroutine(res):
                await res
        except Exception as exc:
            logger.warning(
                "on_device_code callback raised",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    deadline = time.time() + expires_in
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            try:
                resp = await client.post(
                    DEVICE_TOKEN_URL,
                    json={
                        "device_auth_id": device_auth_id,
                        "user_code": user_code,
                    },
                )
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                # Transient blip — treat same as a pending poll.
                logger.debug("Device-code poll transient error", error=str(exc))
                await asyncio.sleep(interval)
                continue

            if resp.status_code == 200:
                token_data = resp.json()
                auth_code = token_data.get("authorization_code", "")
                code_verifier = token_data.get("code_verifier", "")
                if auth_code and code_verifier:
                    return await _exchange_code(
                        auth_code, code_verifier, DEVICE_REDIRECT_URI
                    )
                # Direct-token shape (legacy / OIDC fallback path).
                if "access_token" in token_data:
                    tokens = CodexTokens(
                        access_token=token_data["access_token"],
                        refresh_token=token_data.get("refresh_token", ""),
                        expires_at=time.time() + token_data.get("expires_in", 3600),
                        id_token=token_data.get("id_token", ""),
                    )
                    tokens.save()
                    logger.info("Device code login successful")
                    return tokens
                # 200 with no usable fields — log + treat as pending.
                logger.debug(
                    "Device-code poll returned 200 with no auth code; will retry"
                )
                await asyncio.sleep(interval)
                continue

            # codex-rs treats 403 + 404 identically: "user hasn't
            # entered the code yet, keep polling."  No JSON parse,
            # no error matching — the simpler invariant survives the
            # widest range of OpenAI server-side response shapes.
            if resp.status_code in (403, 404):
                await asyncio.sleep(interval)
                continue

            # 429 — back off and keep polling.
            if resp.status_code == 429:
                interval = min(interval + 5, 30)
                await asyncio.sleep(interval)
                continue

            # Anything else (4xx that isn't 403/404/429, 5xx) is
            # fatal.  We surface the body so the modal can show
            # something useful instead of an opaque hang.
            body = resp.text[:200]
            raise RuntimeError(
                f"Device code poll failed: HTTP {resp.status_code} {body}"
            )

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
        id_token=data.get("id_token", ""),
    )
    tokens.save()
    logger.info("OAuth login successful")
    return tokens


# =========================================================================
# Public API
# =========================================================================


async def oauth_login(
    on_device_code: "callable | None" = None,
    open_browser: bool = True,
) -> CodexTokens:
    """Authenticate with OpenAI Codex OAuth.

    Runs BOTH flows in parallel (browser-redirect + device-code)
    and returns whichever completes first.  Either flow may be
    unusable in any given environment, so the dual race covers all
    cases — codex-rs ships the same shape.

    ``on_device_code`` is forwarded to the device-code branch; pass
    ``None`` (default) for the print-only behaviour.

    ``open_browser=False`` suppresses the local ``webbrowser.open()``
    call but the browser-redirect HTTP server still listens.  Modal
    callers pass ``False``; CLI keeps ``True``.
    """
    # Start both flows as concurrent tasks.
    browser_task = asyncio.create_task(_browser_flow_safe(open_browser=open_browser))
    device_task = asyncio.create_task(_device_code_flow(on_device_code=on_device_code))

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


async def _browser_flow_safe(open_browser: bool = True) -> CodexTokens:
    """Browser flow that doesn't crash if port is busy or browser fails."""
    try:
        return await _browser_flow(open_browser=open_browser)
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
        # Refresh responses sometimes omit id_token — keep the old one.
        id_token=data.get("id_token") or tokens.id_token,
        account_id=tokens.account_id,
    )
    new_tokens.save()
    logger.info("Tokens refreshed")
    return new_tokens
