"""Finding a KT server and its running sessions, for ``kt attach`` and the ``kt-ui``
launcher: ``--url``, else ``KT_SERVER_URL``, else the server ``kt serve`` recorded in
``~/.kohakuterrarium/run/web.json``, else ``http://127.0.0.1:8001``.
"""

import json
import os
from pathlib import Path

import httpx

from kohakuterrarium.builtins.ktui.remote_proxies import server_token

DEFAULT_URL = "http://127.0.0.1:8001"
STATE_FILE = Path.home() / ".kohakuterrarium" / "run" / "web.json"


def server_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit if "://" in explicit else f"http://{explicit}"
    if os.environ.get("KT_SERVER_URL"):
        return os.environ["KT_SERVER_URL"]
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        url = state.get("url") if isinstance(state, dict) else None
        if url:
            # a server bound to every interface is reached on this machine's loopback
            return str(url).replace("://0.0.0.0", "://127.0.0.1")
    except (OSError, ValueError):
        pass
    return DEFAULT_URL


def server_log(url: str) -> Path | None:
    """The log file of the server at ``url`` when it is the one ``kt serve`` runs on this
    machine (else None: its log is not here)."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or not state.get("log_path"):
        return None
    ours = str(state.get("url", "")).replace("://0.0.0.0", "://127.0.0.1").rstrip("/")
    if ours != url.rstrip("/"):
        return None
    path = Path(str(state["log_path"]))
    return path if path.is_file() else None


def tail(path: Path, lines: int, max_bytes: int = 256 * 1024) -> list[str]:
    """The last ``lines`` lines of a (possibly large) text file."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace").splitlines()
    return text[-lines:]


def _client(url: str) -> httpx.AsyncClient:
    token = server_token()
    headers = {"X-KT-Host-Token": token} if token else {}
    return httpx.AsyncClient(base_url=url, headers=headers, timeout=5)


async def stop_session(url: str, session_id: str) -> str | None:
    """Stops a running session in the server (as the web's "stop session"); the error, or
    None when it stopped."""
    try:
        async with _client(url) as http:
            response = await http.delete(f"/api/sessions/active/{session_id}")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return str(exc) or type(exc).__name__
    return None


async def running_sessions(url: str) -> list[dict]:
    """The server's running sessions (``[]`` when it is not reachable)."""
    try:
        async with _client(url) as http:
            response = await http.get("/api/sessions/active")
            response.raise_for_status()
            listed = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    return [s for s in listed or [] if isinstance(s, dict) and s.get("session_id")]
