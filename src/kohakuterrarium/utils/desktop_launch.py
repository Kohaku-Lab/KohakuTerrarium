"""Names and URLs shared by desktop shells and the server they start.

A shell generates a token, hands it to the server through ``LAUNCH_ENV`` and opens
``launch_url``. The server side lives in ``api/auth/launch_token.py``; both sides
import their names from here so they cannot drift.
"""

import secrets

LAUNCH_ENV = "KT_DESKTOP_LAUNCH_TOKEN"
QUERY_PARAM = "kt_launch"
COOKIE_NAME = "kt_launch"
LOOPBACK_HOST = "127.0.0.1"


def new_launch_token() -> str:
    """Return an unguessable single-use launch token."""
    return secrets.token_urlsafe(32)


def home_url(port: int) -> str:
    """Return the app root a window returns to."""
    return f"http://{LOOPBACK_HOST}:{port}/"


def launch_url(port: int, token: str) -> str:
    """Return the first URL a window loads; the server exchanges the token."""
    return f"{home_url(port)}?{QUERY_PARAM}={token}"
