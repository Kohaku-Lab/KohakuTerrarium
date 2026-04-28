"""Structured diff between two saved sessions (V6 viewer wave).

Compares the conversation message lists produced by
``replay_conversation`` for both sessions, identifies the longest
shared prefix, and returns the divergent suffix from each side along
with a per-turn classification of changes. Driven by
``GET /sessions/{name}/diff?other=<name>&agent=…``.
"""

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.store import SessionStore


def _agents_for(meta: dict[str, Any], requested: str | None) -> str:
    """Pick a single agent name to diff (one slice at a time)."""
    all_agents = list(meta.get("agents") or [])
    if requested is None:
        if not all_agents:
            raise HTTPException(404, "Session has no agents")
        return all_agents[0]
    if requested not in all_agents:
        raise HTTPException(404, f"Agent not found in session: {requested}")
    return requested


def _flatten(content: Any) -> str:
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


def _msg_signature(msg: dict[str, Any]) -> tuple:
    """Stable identity tuple for diff comparison.

    Two messages are "the same" iff they share role, flattened text,
    and tool-call signature (name + first 200 chars of args). This is
    deliberately coarse — exact dict-equality would flag noise like
    timestamp drift or tool_call_id reshuffles.
    """
    role = msg.get("role", "")
    content = _flatten(msg.get("content", ""))
    tcs = msg.get("tool_calls") or []
    tc_sig = tuple(
        (
            (t or {}).get("function", {}).get("name", ""),
            ((t or {}).get("function", {}).get("arguments", "") or "")[:200],
        )
        for t in tcs
    )
    return (role, content, tc_sig)


def _summarize_msg(msg: dict[str, Any]) -> dict[str, Any]:
    """One-line summary of a message for the diff payload."""
    return {
        "role": msg.get("role", ""),
        "content_preview": _flatten(msg.get("content", ""))[:200],
        "has_tool_calls": bool(msg.get("tool_calls")),
        "name": msg.get("name", ""),
    }


def _load_messages(path: Path, agent_arg: str | None) -> tuple[list[dict], str, str]:
    """Open the store, replay events, return ``(messages, name, agent)``."""
    store = SessionStore(path)
    try:
        meta = store.load_meta()
        name = str(meta.get("session_id") or path.stem)
        agent = _agents_for(meta, agent_arg)
        events = store.get_events(agent)
        msgs = replay_conversation(events) if events else []
        return msgs, name, agent
    finally:
        store.close(update_status=False)


def build_diff_payload(
    a_path: Path,
    b_path: Path,
    *,
    agent: str | None,
) -> dict[str, Any]:
    """Compute the diff payload for ``a`` vs ``b``.

    Both sessions are sliced to the named agent (or the first agent in
    the meta when ``agent is None``). Returns a payload describing the
    shared prefix length, divergence point, and the divergent suffixes
    from each side as one-line summaries — full message bodies stay
    server-side so a single diff request stays small.
    """
    a_msgs, a_name, a_agent = _load_messages(a_path, agent)
    b_msgs, b_name, b_agent = _load_messages(b_path, agent)

    # Shared-prefix length using the coarse signature.
    common = 0
    for ma, mb in zip(a_msgs, b_msgs):
        if _msg_signature(ma) == _msg_signature(mb):
            common += 1
            continue
        break

    a_diverge = a_msgs[common:]
    b_diverge = b_msgs[common:]

    return {
        "a": {"session_name": a_name, "agent": a_agent, "total_messages": len(a_msgs)},
        "b": {"session_name": b_name, "agent": b_agent, "total_messages": len(b_msgs)},
        "shared_prefix_length": common,
        "a_only": [_summarize_msg(m) for m in a_diverge],
        "b_only": [_summarize_msg(m) for m in b_diverge],
        "identical": not a_diverge and not b_diverge and len(a_msgs) == len(b_msgs),
    }
