"""``kt-ui --daemon <creature>``: the session runs in the background KT server, ktui attaches.

The server is the one ``kt serve`` runs (one per machine, shared with the web UI). When none
runs, it is started for this, and then stops by itself a while after its last session ends.
Leaving ktui (``/exit``) detaches and the session keeps working; ``kt-ui attach`` (or the
launcher's running group) comes back to it, ``/stop`` there ends it.

A server older than this install (an upgrade since it started) is restarted when it has no
sessions; with sessions it is used as it is, and ktui says so.
"""

import argparse
import os
from pathlib import Path

import httpx

from kohakuterrarium.cli.run import _looks_like_recipe
from kohakuterrarium.cli.serve import serve_stop_cli
from kohakuterrarium.cli.serve_on_demand import ensure_server
from kohakuterrarium.cli.version import get_package_version
from kohakuterrarium.packages.resolve import resolve_package_path


def attach_cli(*args, **kwargs):
    from kohakuterrarium.terrarium.engine.cli.ktui_remote import attach_cli as attach

    return attach(*args, **kwargs)


def _headers() -> dict[str, str]:
    token = os.environ.get("KT_HOST_TOKEN")
    return {"X-KT-Host-Token": token} if token else {}


def _sessions(url: str) -> list:
    try:
        response = httpx.get(
            f"{url}/api/sessions/active", headers=_headers(), timeout=10
        )
        return list(response.json()) if response.status_code < 400 else []
    except (httpx.HTTPError, ValueError):
        return []


def server_for_daemon() -> tuple[str, str | None]:
    """The server's URL, started (or restarted when it is older and idle) as needed, and a
    note for the user when it runs another version than this install."""
    info = ensure_server()
    url, version = info["url"], info["version"]
    ours = get_package_version()
    if info["started"] or not version or version == ours:
        return url, None
    if not _sessions(url):
        serve_stop_cli(argparse.Namespace(timeout=15))
        return ensure_server()["url"], None
    return url, (
        f"the KT server runs {version} (this is {ours}); it restarts on this version "
        "once its sessions have ended"
    )


def _config_ref(ref: str) -> tuple[str, bool]:
    """The creature or recipe as the server can load it (an absolute path) and whether it is a
    recipe."""
    path = resolve_package_path(ref) if ref.startswith("@") else Path(ref)
    if path.exists():
        path = path.resolve()
        return str(path), _looks_like_recipe(str(path))
    # a name the server resolves itself (an installed creature)
    return ref, False


def start_session(url: str, ref: str, llm: str | None) -> str:
    """Starts ``ref`` in the server; its session id."""
    config_path, recipe = _config_ref(ref)
    body = {"config_path": config_path, "pwd": str(Path.cwd())}
    if llm:
        body["llm"] = llm
    kind = "terrarium" if recipe else "creature"
    response = httpx.post(
        f"{url}/api/sessions/active/{kind}", json=body, headers=_headers(), timeout=120
    )
    if response.status_code >= 400:
        detail = response.json().get("detail") if response.content else ""
        raise RuntimeError(f"the server did not start {ref}: {detail or response.text}")
    return str(response.json()["session_id"])


def daemon_cli(ns: argparse.Namespace, ref: str) -> int:
    """``kt-ui --daemon <ref>``."""
    if getattr(ns, "add_creatures", None) or getattr(ns, "add_channels", None):
        print(
            "kt-ui --daemon: --add / --channel are not taken here; add creatures and "
            "channels on the Graph page once attached."
        )
        return 2
    try:
        url, note = server_for_daemon()
        session_id = start_session(url, ref, getattr(ns, "llm", None))
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"kt-ui --daemon: {exc}")
        return 1
    if note:
        print(note)
    print(f"Started {ref} in the background (session {session_id}).")
    print(
        f"Leave with /exit and it keeps running; come back with: kt-ui attach {session_id}"
    )
    return attach_cli(session_id, url, None, getattr(ns, "renderer", None))


__all__ = ["daemon_cli", "server_for_daemon", "start_session"]
