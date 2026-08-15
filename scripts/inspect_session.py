#!/usr/bin/env python3
"""Inspect a .kohakutr session file.

Usage:
    python scripts/inspect_session.py path/to/session.kohakutr [--events AGENT] [--channels] [--search QUERY]
"""

import argparse
import json
import sys
from pathlib import Path

# Allow direct checkout execution without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kohakuterrarium.session.store import SessionStore


def print_meta(store: SessionStore) -> None:
    """Print session metadata with bounded previews of structured values."""
    meta = store.load_meta()
    print("=== Session Metadata ===")
    for k, v in sorted(meta.items()):
        if isinstance(v, (dict, list)):
            print(f"  {k}: {json.dumps(v, indent=4, default=str)[:200]}")
        else:
            print(f"  {k}: {v}")
    print()


def print_events(store: SessionStore, agent: str | None = None) -> None:
    """Print compact event summaries for one agent or the full session."""
    if agent:
        events = store.get_events(agent)
        print(f"=== Events for '{agent}' ({len(events)} total) ===")
    else:
        all_evts = store.get_all_events()
        events = [evt for _, evt in all_evts]
        print(f"=== All Events ({len(events)} total) ===")

    for i, evt in enumerate(events):
        etype = evt.get("type", "?")

        match etype:
            case "user_input":
                content = evt.get("content", "")[:80]
                print(f"  [{i:03d}] USER: {content}")
            case "text":
                content = evt.get("content", "")[:80]
                print(f"  [{i:03d}] TEXT: {content}")
            case "tool_call":
                name = evt.get("name", "?")
                args = json.dumps(evt.get("args", {}), default=str)[:60]
                print(f"  [{i:03d}] TOOL: {name}({args})")
            case "tool_result":
                name = evt.get("name", "?")
                output = evt.get("output", "")[:60]
                code = evt.get("exit_code", "?")
                print(f"  [{i:03d}] RESULT: {name} [{code}] {output}")
            case "subagent_call":
                name = evt.get("name", "?")
                task = evt.get("task", "")[:60]
                print(f"  [{i:03d}] SUBAGENT: {name} -> {task}")
            case "subagent_result":
                name = evt.get("name", "?")
                output = evt.get("output", "")[:60]
                tools = evt.get("tools_used", [])
                print(f"  [{i:03d}] SA_RESULT: {name} tools={tools} {output}")
            case "trigger_fired":
                ch = evt.get("channel", "?")
                sender = evt.get("sender", "?")
                content = evt.get("content", "")[:50]
                print(f"  [{i:03d}] TRIGGER: {ch} from {sender}: {content}")
            case "token_usage":
                p = evt.get("prompt_tokens", 0)
                c = evt.get("completion_tokens", 0)
                t = evt.get("total_tokens", 0)
                print(f"  [{i:03d}] TOKENS: {p} in, {c} out, {t} total")
            case "processing_start":
                print(f"  [{i:03d}] --- processing start ---")
            case "processing_end":
                print(f"  [{i:03d}] --- processing end ---")
            case _:
                detail = json.dumps(evt, default=str)[:80]
                print(f"  [{i:03d}] {etype}: {detail}")
    print()


def print_channels(store: SessionStore) -> None:
    """Discover and print persisted channel messages."""
    print("=== Channels ===")
    seen_channels = set()
    for key_bytes in store.channels.keys():
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        parts = key.rsplit(":m", 1)
        if len(parts) == 2:
            seen_channels.add(parts[0])

    for ch in sorted(seen_channels):
        msgs = store.get_channel_messages(ch)
        print(f"  {ch} ({len(msgs)} messages):")
        for msg in msgs:
            sender = msg.get("sender", "?")
            content = str(msg.get("content", ""))[:60]
            print(f"    [{sender}] {content}")
    print()


def print_subagents(store: SessionStore) -> None:
    """Print persisted sub-agent run metadata and conversation presence."""
    print("=== Sub-Agent Runs ===")
    seen = set()
    for key_bytes in store.subagents.keys():
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        if key.endswith(":meta"):
            prefix = key[: -len(":meta")]
            seen.add(prefix)

    for prefix in sorted(seen):
        meta = store.subagents[f"{prefix}:meta"]
        task = meta.get("task", "?")[:60] if isinstance(meta, dict) else "?"
        turns = meta.get("turns", "?") if isinstance(meta, dict) else "?"
        tools = meta.get("tools_used", []) if isinstance(meta, dict) else []
        success = meta.get("success", "?") if isinstance(meta, dict) else "?"
        print(f"  {prefix}: task={task} turns={turns} tools={tools} success={success}")

        has_conv = store.subagents.get(f"{prefix}:conversation") is not None
        if has_conv:
            print(f"    (conversation saved)")
    print()


def print_state(store: SessionStore) -> None:
    """Print persisted agent state entries."""
    print("=== Agent State ===")
    for key_bytes in sorted(store.state.keys()):
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        val = store.state[key_bytes]
        if isinstance(val, dict):
            print(f"  {key}: {json.dumps(val, default=str)[:100]}")
        else:
            print(f"  {key}: {val}")
    print()


_REASONING_TEXT_KEYS = ("reasoning_content", "reasoning", "reasoning_summary")
_ANTHROPIC_REASONING_TYPES = {"thinking", "redacted_thinking"}
_REASONING_PREVIEW_CHARS = 1200


def _bounded_text(text: str, limit: int) -> str:
    """Return ``text`` with a trailing truncation marker when over ``limit``."""
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [{len(text) - limit} more chars]"


def _reasoning_entries(message: dict) -> list[tuple[str, str]]:
    """Extract chain-of-thought fields from one raw conversation message."""
    entries: list[tuple[str, str]] = []
    fields = {**(message.get("extra_fields") or {}), **message}

    for key in _REASONING_TEXT_KEYS:
        value = fields.get(key)
        if isinstance(value, str) and value:
            entries.append((key, value))

    for idx, block in enumerate(fields.get("reasoning_details") or []):
        if not isinstance(block, dict):
            continue
        text = block.get("text") or block.get("thinking") or block.get("data") or ""
        signature = block.get("signature") or ""
        if signature:
            text = f"{text}\n[signature: {signature}]"
        if text:
            label = f"reasoning_details[{idx}]:{block.get('type', 'unknown')}"
            entries.append((label, text))

    native = fields.get("_kt_anthropic_content")
    if isinstance(native, list):
        for idx, block in enumerate(native):
            if not isinstance(block, dict):
                continue
            if block.get("type") not in _ANTHROPIC_REASONING_TYPES:
                continue
            text = block.get("thinking") or block.get("data") or ""
            signature = block.get("signature") or ""
            if signature:
                text = f"{text}\n[signature: {signature}]"
            if text:
                entries.append((f"anthropic:{block.get('type')}[{idx}]", text))

    return entries


def print_conversations(
    store: SessionStore,
    *,
    show_reasoning: bool = False,
    full_reasoning: bool = False,
) -> None:
    """Print bounded previews of persisted conversation snapshots.

    With ``show_reasoning``, assistant messages additionally print their
    captured chain-of-thought fields. ``full_reasoning`` disables the
    per-field preview truncation.
    """
    print("=== Conversation Snapshots ===")
    for key_bytes in sorted(store.conversation.keys()):
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        messages = store.load_conversation(key)
        if not messages:
            print(f"  {key}: (empty)")
            continue

        print(f"  {key}: {len(messages)} messages")
        for msg in messages[:3]:
            role = msg.get("role", "?")
            content = str(msg.get("content", ""))[:60]
            tc = " [+tool_calls]" if msg.get("tool_calls") else ""
            print(f"    [{role}]{tc} {content}")
        if len(messages) > 3:
            print(f"    ... ({len(messages) - 3} more)")

        if show_reasoning:
            found = False
            for idx, msg in enumerate(messages):
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                for label, text in _reasoning_entries(msg):
                    found = True
                    rendered = (
                        text
                        if full_reasoning
                        else _bounded_text(text, _REASONING_PREVIEW_CHARS)
                    )
                    print(f"    [msg {idx}] {label}:")
                    for line in rendered.splitlines() or [""]:
                        print(f"        {line}")
            if not found:
                print("    (no reasoning fields found in snapshot)")
    print()


def print_search(store: SessionStore, query: str) -> None:
    """Print ranked full-text search results for the session."""
    results = store.search(query, k=10)
    print(f"=== Search: '{query}' ({len(results)} results) ===")
    for r in results:
        score = r["score"]
        meta = r["meta"]
        key = meta.get("event_key") or meta.get("channel_key") or "?"
        etype = meta.get("type", "?")
        print(f"  [{score:.3f}] {key} ({etype})")
    print()


def print_summary(store: SessionStore) -> None:
    """Print session identity and aggregate event, channel, and sub-agent counts."""
    meta = store.load_meta()
    print(f"Session: {meta.get('session_id', '?')}")
    print(f"Type: {meta.get('config_type', '?')}")
    print(f"Config: {meta.get('config_path', '?')}")
    print(f"Status: {meta.get('status', '?')}")
    print(f"Created: {meta.get('created_at', '?')}")
    print(f"Last active: {meta.get('last_active', '?')}")
    print(f"Agents: {meta.get('agents', [])}")

    agents = meta.get("agents", [])
    for agent in agents:
        events = store.get_events(agent)
        print(f"  {agent}: {len(events)} events")

    seen_channels = set()
    for key_bytes in store.channels.keys():
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        parts = key.rsplit(":m", 1)
        if len(parts) == 2:
            seen_channels.add(parts[0])
    total_msgs = sum(len(store.get_channel_messages(ch)) for ch in seen_channels)
    print(f"Channels: {len(seen_channels)} ({total_msgs} messages)")

    sa_count = sum(
        1
        for k in store.subagents.keys()
        if (k.decode() if isinstance(k, bytes) else k).endswith(":meta")
    )
    print(f"Sub-agent runs: {sa_count}")
    print()


def main():
    """Parse display options and inspect one session store safely."""
    parser = argparse.ArgumentParser(description="Inspect a .kohakutr session file")
    parser.add_argument("path", help="Path to .kohakutr session file")
    parser.add_argument(
        "--events",
        nargs="?",
        const="__all__",
        default=None,
        help="Show events (optionally for specific agent)",
    )
    parser.add_argument("--channels", action="store_true", help="Show channel messages")
    parser.add_argument("--subagents", action="store_true", help="Show sub-agent runs")
    parser.add_argument("--state", action="store_true", help="Show agent state")
    parser.add_argument(
        "--conversations", action="store_true", help="Show conversation snapshots"
    )
    parser.add_argument(
        "--reasoning",
        action="store_true",
        help="Show chain-of-thought fields from conversation snapshots",
    )
    parser.add_argument(
        "--full-reasoning",
        action="store_true",
        help="Print complete reasoning text instead of bounded previews",
    )
    parser.add_argument("--search", help="Search session content")
    parser.add_argument("--all", action="store_true", help="Show everything")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        sys.exit(1)

    store = SessionStore(path)

    try:
        show_all = args.all
        shown = False

        print_summary(store)

        if show_all or args.events is not None:
            agent = None if args.events == "__all__" else args.events
            print_events(store, agent)
            shown = True

        if show_all or args.channels:
            print_channels(store)
            shown = True

        if show_all or args.subagents:
            print_subagents(store)
            shown = True

        if show_all or args.state:
            print_state(store)
            shown = True

        show_reasoning = args.reasoning or args.full_reasoning
        if show_all or args.conversations or show_reasoning:
            print_conversations(
                store,
                show_reasoning=show_reasoning or show_all,
                full_reasoning=args.full_reasoning,
            )
            shown = True

        if args.search:
            print_search(store, args.search)
            shown = True

        if show_all:
            print_meta(store)

        if not shown and not show_all:
            print(
                "Use --events, --channels, --subagents, --state, --conversations, "
                "--reasoning, --search, or --all"
            )

    finally:
        store.close()


if __name__ == "__main__":
    main()
