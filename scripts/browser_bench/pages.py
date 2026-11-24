"""HTML and response helpers shared by the task pages."""

import html
import json
from typing import Any

from .model import Response

BASE_CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;color:#1d2433;
background:#f6f7f9;line-height:1.45}
header.top{background:#22304a;color:#fff;padding:12px 24px;display:flex;gap:24px;
align-items:center}
header.top .brand{font-weight:700;font-size:18px}
main{max-width:960px;margin:24px auto;padding:0 24px}
.card{background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:20px;
margin-bottom:16px}
label{display:block;margin:10px 0 4px;font-weight:600;font-size:14px}
input,select,textarea{font:inherit;padding:7px 9px;border:1px solid #b9c0cc;
border-radius:5px;min-width:240px}
button,.btn{font:inherit;padding:8px 16px;border-radius:5px;border:1px solid #2d5bd3;
background:#2d5bd3;color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
button.secondary,.btn.secondary{background:#fff;color:#2d5bd3}
button.danger{background:#c62828;border-color:#c62828}
button:disabled{background:#c5cad3;border-color:#c5cad3;cursor:not-allowed}
.muted{color:#6b7385;font-size:13px}
.error{color:#b3261e;background:#fdecea;padding:8px 12px;border-radius:5px}
.ok{color:#1b5e20;background:#e8f5e9;padding:8px 12px;border-radius:5px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border-bottom:1px solid #e3e6ec;padding:6px 10px;text-align:left;font-size:14px}
th{background:#eef1f6}
"""
NO_ICON = '<link rel="icon" href="data:,">'


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def page(
    title: str,
    body: str,
    *,
    css: str = "",
    script: str = "",
    header: str = "",
    status: int = 200,
) -> Response:
    """A complete, self-contained HTML page response."""
    top = (
        f'<header class="top"><span class="brand">{header}</span></header>'
        if header
        else ""
    )
    text = (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>{NO_ICON}"
        f"<title>{esc(title)}</title><style>{BASE_CSS}{css}</style></head>"
        f"<body>{top}<main>{body}</main>"
        f"{f'<script>{script}</script>' if script else ''}</body></html>"
    )
    return Response(text.encode("utf-8"), status)


def fragment(text: str, status: int = 200) -> Response:
    """A bare HTML document without the site chrome (frames, internal pages)."""
    doc = f"<!doctype html><html><head><meta charset=utf-8>{NO_ICON}</head><body>{text}</body></html>"
    return Response(doc.encode("utf-8"), status)


def redirect(location: str, status: int = 303) -> Response:
    return Response(b"", status, headers={"Location": location})


def json_response(value: Any, status: int = 200) -> Response:
    return Response(json.dumps(value).encode("utf-8"), status, "application/json")


def raw(body: bytes, content_type: str, **headers: str) -> Response:
    return Response(body, 200, content_type, dict(headers))
