"""SSRF protection — URL validation utilities for admin-configured endpoints."""

import ipaddress
import re
from urllib.parse import urlparse

from fastapi import HTTPException

# Private IP ranges that should never be reachable from server-side requests
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("fd00:ec2::/128"),  # AWS IPv6 metadata
]

_ALLOWED_SCHEMES = {"http", "https"}


def validate_url_against_ssrf(url: str, *, allow_schemes: set[str] | None = None) -> str:
    """Validate that a URL does not point to a private/internal network.

    Raises HTTPException 400 if the URL:
    - Uses a disallowed scheme (default: http, https only)
    - Points to a private IP address (RFC 1918, loopback, link-local, cloud metadata)
    - Has no valid hostname

    Returns the URL string if valid.

    This is a defense-in-depth check for admin-configured URLs (LLM backends,
    MCP servers) that are used for server-side requests. Even though these
    require admin auth, validating URLs prevents accidental or malicious SSRF
    to cloud metadata endpoints and internal services.
    """
    if not url or not url.strip():
        return url

    allowed = allow_schemes or _ALLOWED_SCHEMES

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise HTTPException(400, f"Invalid URL: {e}") from e

    if parsed.scheme not in allowed:
        raise HTTPException(
            400,
            f"URL scheme '{parsed.scheme}' not allowed (allowed: {', '.join(sorted(allowed))})",
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(400, "URL has no hostname")

    # Check against private networks
    try:
        ip = ipaddress.ip_address(hostname)
        for network in _PRIVATE_NETWORKS:
            if ip in network:
                raise HTTPException(
                    400,
                    f"URL hostname '{hostname}' resolves to a private/internal network ({network}), which is not allowed",
                )
    except ValueError:
        # Not an IP address — could be a domain name. Check for common
        # metadata hostnames but allow general domains.
        metadata_hostnames = {"metadata.google.internal", "metadata.internal"}
        if hostname.lower() in metadata_hostnames:
            raise HTTPException(
                400,
                f"URL hostname '{hostname}' is a known cloud metadata endpoint, which is not allowed",
            )

    return url
