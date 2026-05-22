"""``kt admin show-host-qr`` — QR-code rendering of the host URL.

Pulled out of ``cli.admin`` so the parent stays under the file-size
budget.  The verb itself is dispatched from ``admin_cli`` in
``admin.py``; this module owns the implementation + URI helpers
that the QR scanner Vue component decodes.
"""

import io
import os
import socket
import sys
from urllib.parse import quote, urlparse

import segno

from kohakuterrarium.api.auth.config import load_auth_config


def show_host_qr(url: str, yes: bool) -> int:
    """Render a ``ktconnect://`` URI as a terminal QR.

    The URI encodes ``host_url`` + ``host_token`` so a mobile-app
    user can scan one code instead of typing both.  Format:

        ktconnect://<host>:<port>/?token=<host_token>&scheme=<scheme>

    The mobile client's Add-Host flow understands this scheme and
    pre-populates its form.

    Requires ``--yes`` because the QR encodes the host_token in
    plaintext — anyone with a camera in line of sight gets full
    L2 access.  Mirrors the ``show-host-token`` confirmation.
    """
    if not yes:
        print(
            "this command prints the host_token via QR — anyone who "
            "sees the screen can scan it and connect.",
            file=sys.stderr,
        )
        print("re-run with --yes if you really want to.", file=sys.stderr)
        return 1
    cfg = load_auth_config()
    if not cfg.host_token:
        print(
            "(host_token is not set; run ``kt admin set-host-token`` first)",
            file=sys.stderr,
        )
        return 1

    resolved_url = (url or "").strip() or default_host_url()
    uri = build_ktconnect_uri(resolved_url, cfg.host_token)

    qr = segno.make(uri, error="m")
    # Buffer segno's unicode-block output before writing — Windows
    # cp950 cmd can't render U+2580 et al.  On UTF-8 stdouts (every
    # Linux / macOS / mobile-app shell) we pass through verbatim;
    # on cp950 we fall back to ASCII.
    buf = io.StringIO()
    qr.terminal(out=buf, compact=True, border=1)
    output = buf.getvalue()

    try:
        sys.stdout.write(output)
        sys.stdout.flush()
    except UnicodeEncodeError:
        ascii_buf = io.StringIO()
        qr.terminal(out=ascii_buf, compact=False, border=1)
        ascii_text = ascii_buf.getvalue().replace("█", "##").replace(" ", "  ")
        sys.stdout.write(ascii_text)
        sys.stdout.flush()

    print()
    print(f"URI: {uri}")
    print(f"URL: {resolved_url}")
    print("On the phone: Settings → Connection → Add host → Scan QR.")
    return 0


def default_host_url() -> str:
    """Best-effort LAN URL for the host.  Falls back to localhost
    when no LAN interface is detectable — the operator sees an
    obvious 127.0.0.1 link and can pass ``--url`` explicitly to
    fix it."""
    port = int(os.environ.get("KT_SERVE_PORT", "8001"))
    # Trick: open a UDP socket toward a routable address; the
    # kernel populates getsockname() with the interface that would
    # be used, without any packets sent.
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        ip = "127.0.0.1"
    return f"http://{ip}:{port}"


def build_ktconnect_uri(host_url: str, host_token: str) -> str:
    """Compose the ``ktconnect://...`` URI scheme.

    Format::

        ktconnect://<authority>/?token=<host_token>&scheme=<scheme>

    where ``<authority>`` is the ``<host>:<port>`` portion of the
    given URL.  The scheme is kept distinct from ``http(s)://`` so
    that scanning the QR into a generic browser doesn't accidentally
    leak the token into the URL bar history; mobile-only handlers
    consume it.
    """
    parsed = urlparse(host_url)
    if not parsed.netloc:
        # Treat raw "host:port" inputs as authority-only.
        authority = host_url.lstrip("/")
    else:
        authority = parsed.netloc
    # Preserve scheme hint so the client knows TLS vs plain — encode
    # as a query param (the ktconnect scheme is transport-agnostic).
    scheme = parsed.scheme or "http"
    token_q = quote(host_token, safe="")
    return f"ktconnect://{authority}/?token={token_q}&scheme={scheme}"


__all__ = ["build_ktconnect_uri", "default_host_url", "show_host_qr"]
