"""Read-only export builders for the Session Viewer V6 wave.

Three formats:

* ``md``    — Human-readable markdown transcript (default).
* ``html``  — Same content wrapped in collapsible ``<details>`` for
              bug-report attachments.
* ``jsonl`` — One event per line, suitable for fine-tuning fixtures
              and external analytics.

All formats are streamed as text. Driven by
``GET /sessions/{name}/export?format=…&agent=…`` on the viewer router.
"""

import html as _html_lib
import json
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.store import SessionStore

SUPPORTED_FORMATS = ("md", "html", "jsonl")

CONTENT_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "jsonl": "application/jsonl; charset=utf-8",
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _flatten_content(content: Any) -> str:
    """Best-effort text from any stored message-content shape.

    Mirrors the tolerance ladder in ``core/compact_text.py`` so the
    export reflects exactly what a future compact-summary or trace
    viewer would see.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                elif isinstance(part.get("content"), str):
                    chunks.append(part["content"])
            elif hasattr(part, "text"):
                t = getattr(part, "text") or ""
                if t:
                    chunks.append(t)
        return " ".join(chunks)
    return ""


def _agents_for(meta: dict[str, Any], requested: str | None) -> list[str]:
    all_agents = list(meta.get("agents") or [])
    if requested is None:
        return all_agents
    if requested not in all_agents:
        raise HTTPException(404, f"Agent not found in session: {requested}")
    return [requested]


# ─────────────────────────────────────────────────────────────────────
# Markdown
# ─────────────────────────────────────────────────────────────────────


def _render_markdown(store: SessionStore, session_name: str, agent: str | None) -> str:
    meta = store.load_meta()
    agents = _agents_for(meta, agent)

    lines: list[str] = []
    lines.append(f"# Session: {session_name}")
    lines.append("")
    lines.append(
        "> Created: {c}  ·  Last active: {l}  ·  Format: v{v}".format(
            c=meta.get("created_at", "—"),
            l=meta.get("last_active", "—"),
            v=meta.get("format_version", 1),
        )
    )
    lines.append("")

    for ag in agents:
        lines.append(f"## Agent: `{ag}`")
        lines.append("")
        events = store.get_events(ag)
        messages = replay_conversation(events) if events else []
        if not messages:
            lines.append("_(no recorded conversation)_")
            lines.append("")
            continue

        for msg in messages:
            role = msg.get("role", "user")
            content = _flatten_content(msg.get("content", ""))
            tool_calls = msg.get("tool_calls") or []
            if role == "system":
                lines.append("**System:**")
                lines.append("")
                if content:
                    lines.append("```")
                    lines.append(content)
                    lines.append("```")
                lines.append("")
            elif role == "user":
                lines.append(f"**User:** {content}")
                lines.append("")
            elif role == "assistant":
                if content:
                    lines.append(f"**Assistant:** {content}")
                    lines.append("")
                for tc in tool_calls:
                    fn = (tc or {}).get("function", {}) or {}
                    name = fn.get("name", "")
                    args = fn.get("arguments", "")
                    lines.append(f"> 🔧 `{name}`({args})")
                    lines.append("")
            elif role == "tool":
                name = msg.get("name", "tool")
                lines.append(f"> ↪ result of `{name}`:")
                lines.append("")
                if content:
                    lines.append("```")
                    lines.append(content)
                    lines.append("```")
                    lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────────


# CSS lives outside ``str.format`` so its literal ``{}`` braces don't
# collide with format-spec parsing — just plain string concatenation.
_HTML_CSS = """<style>
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 880px; margin: 2em auto; padding: 0 1em; color: #1a1816; }
h1 { font-size: 1.4em; }
h2 { font-size: 1.15em; margin-top: 1.5em; }
.meta { color: #666; font-size: 12px; margin-bottom: 1em; }
.role { font-weight: 600; margin-top: 0.7em; display: inline-block; }
.role-user { color: #4a7a3e; }
.role-assistant { color: #555; }
.role-system { color: #999; }
.role-tool { color: #b06000; }
pre { background: #f5f3ef; padding: 0.6em 0.8em; border-radius: 4px; overflow: auto; }
.tool { color: #b06000; font-family: monospace; font-size: 12px; }
details { margin: 0.4em 0; }
summary { cursor: pointer; color: #555; }
hr { border: 0; border-top: 1px solid #ddd; margin: 2em 0; }
</style>"""

_HTML_FOOT = "</body></html>\n"


def _esc(s: Any) -> str:
    return _html_lib.escape(str(s or ""), quote=True)


def _html_head(session_name: str) -> str:
    return (
        "<!doctype html>\n<html><head>\n"
        '<meta charset="utf-8">\n'
        f"<title>Session: {_esc(session_name)}</title>\n"
        f"{_HTML_CSS}\n"
        "</head><body>\n"
    )


def _render_html(store: SessionStore, session_name: str, agent: str | None) -> str:
    meta = store.load_meta()
    agents = _agents_for(meta, agent)

    out: list[str] = [_html_head(session_name)]
    out.append(f"<h1>Session: {_esc(session_name)}</h1>")
    out.append(
        '<div class="meta">Created: {c} · Last active: {l} · Format: v{v}</div>'.format(
            c=_esc(meta.get("created_at", "—")),
            l=_esc(meta.get("last_active", "—")),
            v=_esc(meta.get("format_version", 1)),
        )
    )

    for ag in agents:
        out.append(f"<h2>Agent: <code>{_esc(ag)}</code></h2>")
        events = store.get_events(ag)
        messages = replay_conversation(events) if events else []
        if not messages:
            out.append("<p><em>(no recorded conversation)</em></p>")
            continue
        for msg in messages:
            role = msg.get("role", "user")
            content = _flatten_content(msg.get("content", ""))
            tool_calls = msg.get("tool_calls") or []
            cls = f"role role-{_esc(role)}"
            label = role.capitalize()
            out.append(f'<div class="{cls}">{label}:</div>')
            if content:
                if role in ("system", "tool"):
                    out.append(f"<pre>{_esc(content)}</pre>")
                else:
                    out.append(f"<p>{_esc(content)}</p>")
            for tc in tool_calls:
                fn = (tc or {}).get("function", {}) or {}
                name = fn.get("name", "")
                args = fn.get("arguments", "")
                preview = _esc(args)[:80]
                out.append(
                    f"<details><summary class='tool'>🔧 {_esc(name)}({preview})</summary>"
                    f"<pre>{_esc(args)}</pre></details>"
                )
        out.append("<hr>")

    out.append(_HTML_FOOT)
    return "".join(out)


# ─────────────────────────────────────────────────────────────────────
# JSONL
# ─────────────────────────────────────────────────────────────────────


def _render_jsonl(store: SessionStore, session_name: str, agent: str | None) -> str:
    meta = store.load_meta()
    agents = _agents_for(meta, agent)
    out: list[str] = []
    for ag in agents:
        for evt in store.get_events(ag):
            payload = dict(evt)
            payload.setdefault("agent", ag)
            out.append(json.dumps(payload, ensure_ascii=False))
    return "\n".join(out) + ("\n" if out else "")


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────


def build_export(
    store: SessionStore, session_name: str, fmt: str, agent: str | None
) -> tuple[str, str]:
    """Return ``(content_type, body)`` for the requested format."""
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(
            400,
            f"Unsupported export format: {fmt!r}. "
            f"Use one of {', '.join(SUPPORTED_FORMATS)}.",
        )
    content_type = CONTENT_TYPES[fmt]
    if fmt == "md":
        body = _render_markdown(store, session_name, agent)
    elif fmt == "html":
        body = _render_html(store, session_name, agent)
    else:
        body = _render_jsonl(store, session_name, agent)
    return content_type, body
