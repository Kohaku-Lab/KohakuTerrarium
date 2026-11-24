"""Where page content in the history costs tokens: superseded views, wrappers, unused refs.

Every figure is a re-sent cost: a tool result's characters times the number
of later LLM calls whose context still carries it. A result is *superseded*
for a call when a later view that replaces it (a navigation of the same tab,
by ``browser_navigate`` or by an action, or an unnarrowed snapshot) is also
in that call's context. An in-page change shown as a delta or as the
interactive elements does not supersede an earlier view; one shown as the
whole page (``Page now:``) does.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .accounting import load_transcript, split_runs
from .store import RunStore

STRUCTURAL = re.compile(
    r"^\s*- (generic|group|rowgroup|list|listitem|paragraph|cell|row|region|main|"
    r"banner|navigation|contentinfo|article|section|form|table|iframe)"
    r"( \[[^\]]*\])*:?\s*$"
)
REF = re.compile(r" \[ref=([\w]+)\]")
TAB = re.compile(r"^\[(t\d+)\]")
NAVIGATION_MARKERS = ("Navigated:", "Reloaded:", "Page now:")
NARROWING = ("ref", "query", "depth", "interactive")


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def page_inner(text: str) -> list[str]:
    """The lines between the ``page_content`` tags, with line endings."""
    lines = text.splitlines(keepends=True)
    inside, out = False, []
    for line in lines:
        if line.startswith("<page_content"):
            inside = True
        elif line.strip() == "</page_content>":
            inside = False
        elif inside:
            out.append(line)
    return out


def _calls(messages: list[Mapping[str, Any]]) -> dict[str, tuple[str, dict]]:
    out = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", call)
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            out[call.get("id", "")] = (
                fn.get("name", "?"),
                args if isinstance(args, dict) else {},
            )
    return out


def used_refs(messages: Iterable[Mapping[str, Any]]) -> set[str]:
    """Every ref an action, snapshot or eval of the run named."""
    refs: set[str] = set()
    for _name, args in _calls(list(messages)).values():
        if args.get("ref"):
            refs.add(str(args["ref"]))
        actions = args.get("actions")
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except ValueError:
                actions = []
        for action in actions if isinstance(actions, list) else []:
            if isinstance(action, dict) and action.get("ref"):
                refs.add(str(action["ref"]))
    return refs


def replaces_view(tool: str, args: Mapping[str, Any], text: str) -> bool:
    """Whether a result makes earlier page content of its tab stale."""
    if "<page_content" not in text:
        return False
    if tool == "browser_navigate":
        return True
    if tool == "browser_snapshot":
        return not any(args.get(k) for k in NARROWING)
    if tool == "browser_act":
        return any(marker in text for marker in NAVIGATION_MARKERS)
    return False


def page_costs(messages: list[Mapping[str, Any]]) -> dict[str, int]:
    """Re-sent characters of page content, by kind, over one conversation."""
    calls = _calls(messages)
    assistant_at = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    refs_used = used_refs(messages)
    results = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        text = _text(msg.get("content"))
        tool, args = calls.get(msg.get("tool_call_id", ""), (msg.get("name", "?"), {}))
        inner = page_inner(text)
        tab = TAB.match(text)
        results.append(
            {
                "index": i,
                "tab": tab.group(1) if tab else "?",
                "full": replaces_view(tool, args, text),
                "body": sum(len(l) for l in inner),
                "wrapper": sum(
                    len(l)
                    for l in text.splitlines(keepends=True)
                    if l.startswith("<page_content") or l.strip() == "</page_content>"
                ),
                "structural": sum(len(l) for l in inner if STRUCTURAL.match(l)),
                "ref_marks": sum(
                    len(m.group(0)) for l in inner for m in REF.finditer(l)
                ),
                "unused_ref_marks": sum(
                    len(m.group(0))
                    for l in inner
                    for m in REF.finditer(l)
                    if m.group(1) not in refs_used
                ),
            }
        )
    out: Counter[str] = Counter()
    for call_at in assistant_at:
        visible = [r for r in results if r["index"] < call_at]
        last_full: dict[str, int] = {}
        for r in visible:
            if r["full"]:
                last_full[r["tab"]] = r["index"]
        for r in visible:
            out["page_body"] += r["body"]
            out["wrapper"] += r["wrapper"]
            out["structural_lines"] += r["structural"]
            out["ref_marks"] += r["ref_marks"]
            out["unused_ref_marks"] += r["unused_ref_marks"]
            if r["index"] < last_full.get(r["tab"], -1):
                out["superseded_body"] += r["body"]
    return dict(out)


def dir_costs(out: Path) -> dict[str, int]:
    """``page_costs`` summed over the judged runs of a run directory."""
    store = RunStore(out)
    judged, _excluded = split_runs(store.load())
    total: Counter[str] = Counter()
    for record in judged:
        transcript = load_transcript(store, record)
        if transcript:
            total.update(page_costs(transcript.get("messages") or []))
    return dict(total)
