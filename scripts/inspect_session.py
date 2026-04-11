#!/usr/bin/env python3
"""Inspect a .kohakutr session file.

Usage:
    python scripts/inspect_session.py path/to/session.kohakutr [--events AGENT] [--channels] [--search QUERY]
"""

import argparse
import json
import sys
from pathlib import Path

# Ensure project is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kohakuterrarium.session.store import SessionStore


def print_meta(store: SessionStore) -> None:
    meta = store.load_meta()
    print("=== Session Metadata ===")
    for k, v in sorted(meta.items()):
        if isinstance(v, (dict, list)):
            print(f"  {k}: {json.dumps(v, indent=4, default=str)[:200]}")
        else:
            print(f"  {k}: {v}")
    print()


def print_events(store: SessionStore, agent: str | None = None) -> None:
    if agent:
        events = store.get_events(agent)
        print(f"=== Events for '{agent}' ({len(events)} total) ===")
    else:
        all_evts = store.get_all_events()
        events = [evt for _, evt in all_evts]
        print(f"=== All Events ({len(events)} total) ===")

    for i, evt in enumerate(events):
        etype = evt.get("type", "?")

        # Format based on type
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
    print("=== Channels ===")
    # Scan for all channel prefixes
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

        # Check if conversation exists
        has_conv = store.subagents.get(f"{prefix}:conversation") is not None
        if has_conv:
            print(f"    (conversation saved)")
    print()


def print_state(store: SessionStore) -> None:
    print("=== Agent State ===")
    for key_bytes in sorted(store.state.keys()):
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        val = store.state[key_bytes]
        if isinstance(val, dict):
            print(f"  {key}: {json.dumps(val, default=str)[:100]}")
        else:
            print(f"  {key}: {val}")
    print()


def print_conversations(store: SessionStore) -> None:
    print("=== Conversation Snapshots ===")
    for key_bytes in sorted(store.conversation.keys()):
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        messages = store.load_conversation(key)
        if messages:
            print(f"  {key}: {len(messages)} messages")
            for msg in messages[:3]:
                role = msg.get("role", "?")
                content = str(msg.get("content", ""))[:60]
                tc = " [+tool_calls]" if msg.get("tool_calls") else ""
                print(f"    [{role}]{tc} {content}")
            if len(messages) > 3:
                print(f"    ... ({len(messages) - 3} more)")
        else:
            print(f"  {key}: (empty)")
    print()


def print_search(store: SessionStore, query: str) -> None:
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
    """Print a quick summary of the session."""
    meta = store.load_meta()
    print(f"Session: {meta.get('session_id', '?')}")
    print(f"Type: {meta.get('config_type', '?')}")
    print(f"Config: {meta.get('config_path', '?')}")
    print(f"Status: {meta.get('status', '?')}")
    print(f"Created: {meta.get('created_at', '?')}")
    print(f"Last active: {meta.get('last_active', '?')}")
    print(f"Agents: {meta.get('agents', [])}")

    # Count events per agent
    agents = meta.get("agents", [])
    for agent in agents:
        events = store.get_events(agent)
        print(f"  {agent}: {len(events)} events")

    # Count channels
    seen_channels = set()
    for key_bytes in store.channels.keys():
        key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        parts = key.rsplit(":m", 1)
        if len(parts) == 2:
            seen_channels.add(parts[0])
    total_msgs = sum(len(store.get_channel_messages(ch)) for ch in seen_channels)
    print(f"Channels: {len(seen_channels)} ({total_msgs} messages)")

    # Count sub-agent runs
    sa_count = sum(
        1
        for k in store.subagents.keys()
        if (k.decode() if isinstance(k, bytes) else k).endswith(":meta")
    )
    print(f"Sub-agent runs: {sa_count}")
    print()


def main():
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

        if show_all or args.conversations:
            print_conversations(store)
            shown = True

        if args.search:
            print_search(store, args.search)
            shown = True

        if show_all:
            print_meta(store)

        if not shown and not show_all:
            print(
                "Use --events, --channels, --subagents, --state, --conversations, --search, or --all"
            )

    finally:
        store.close()


if __name__ == "__main__":
    main()
