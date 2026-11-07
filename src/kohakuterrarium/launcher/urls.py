"""Which URLs the launcher fetches feeds and releases from: https, or plain http to this
machine only (a local mirror, or a release staged for testing)."""

from urllib.parse import urlsplit

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def allowed_url(url: str) -> bool:
    """https anywhere; http only to a loopback host."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK


def landed_url(resp, asked: str) -> str:
    """Where a response came from after redirects (``asked`` when it does not say)."""
    geturl = getattr(resp, "geturl", None)
    landed = geturl() if callable(geturl) else None
    return str(landed or asked)


__all__ = ["allowed_url", "landed_url"]
