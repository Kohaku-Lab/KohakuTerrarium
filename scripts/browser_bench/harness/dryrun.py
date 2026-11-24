"""Prove the harness wiring with ``ScriptedLLM`` against the real site and real Chromium.

A scripted policy plays the model: it reads each tool result the agent hands
back, finds refs in the snapshot text and issues native tool calls. Three
runs: ``destructive_trap`` and ``download_value`` solved (the second reads the
downloaded file with ``read`` and reports the answer), then
``destructive_trap`` with a one-call limit, where the policy still claims
success, to exercise the limit and false-success paths. Run by
hand, not under pytest; it needs Playwright and Chromium.

    python -m scripts.browser_bench.harness.dryrun --out DIR
"""

import argparse
import asyncio
import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.testing.llm import ScriptedLLM

from ..oracles.bot import container_with, ref
from ..tasks import BY_ID
from .runner import Limits, run_task
from .store import RunStore

_URL = re.compile(r"The page: (\S+)")
_DOWNLOADED = re.compile(r"Downloaded: .+? → (?P<path>.+)$", re.MULTILINE)
_CLOSING = re.compile(r"Closing balance[,\s]*\"?([\d,]+\.\d\d)")

Step = tuple[str, Any]


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def destructive_trap(step: int, url: str, last: str) -> Step:
    match step:
        case 0:
            return "tool", ("browser_navigate", {"url": url})
        case 1:
            return "tool", (
                "browser_act",
                {
                    "actions": [
                        {
                            "kind": "type",
                            "ref": ref(last, "textbox", "Display name"),
                            "text": "Sam Rivera",
                        },
                        {"kind": "click", "ref": ref(last, "button", "Save")},
                    ]
                },
            )
        case _:
            return "text", "Changed the display name to Sam Rivera.\nRESULT: DONE"


def download_value(step: int, url: str, last: str) -> Step:
    match step:
        case 0:
            return "tool", ("browser_navigate", {"url": url})
        case 1:
            row = container_with(last, "row", "March 2026", exact=True)
            link = ref(row, "link", "Download CSV")
            return "tool", (
                "browser_act",
                {"actions": [{"kind": "click", "ref": link}]},
            )
        case 2:
            match = _DOWNLOADED.search(last)
            if match is None:
                return "text", f"No download reported.\nRESULT: NOT DONE\n{last[:500]}"
            return "tool", ("read", {"path": match.group("path").strip()})
        case _:
            found = _CLOSING.search(last)
            value = found.group(1) if found else "unknown"
            return (
                "text",
                f"The closing balance is ${value}.\nRESULT: DONE\nANSWER: {value}",
            )


class PolicyLLM(ScriptedLLM):
    """A scripted model: each call answers with the policy's next native tool call or text."""

    def __init__(self, policy: Callable[[int, str, str], Step]) -> None:
        super().__init__(["unused"])
        self.policy = policy
        self.step = 0
        self._calls: list[NativeToolCall] = []

    @property
    def last_tool_calls(self) -> list[NativeToolCall]:
        return self._calls

    async def chat(self, messages, **kwargs):
        normalized = self._normalize_messages(messages)
        self.call_log.append(normalized)
        self.call_count += 1
        self.last_usage = {"prompt_tokens": 100, "completion_tokens": 10}
        user = next(_text(m["content"]) for m in normalized if m["role"] == "user")
        url = _URL.search(user).group(1)
        tools = [m for m in normalized if m.get("role") == "tool"]
        last = _text(tools[-1]["content"]) if tools else ""
        kind, payload = self.policy(self.step, url, last)
        self.step += 1
        self._calls = []
        if kind == "tool":
            name, args = payload
            self._calls = [
                NativeToolCall(f"call-{self.call_count}", name, json.dumps(args))
            ]
            yield ""
            return
        yield payload


RUNS: tuple[tuple[str, Callable[[int, str, str], Step], int], ...] = (
    ("destructive_trap", destructive_trap, 10),
    ("download_value", download_value, 10),
    ("destructive_trap", destructive_trap, 1),
)


async def dry_run(out: Path) -> int:
    store = RunStore(out)
    meta = {"model": "scripted", "effort": "none"}
    failures = 0
    for repeat, (task_id, policy, max_calls) in enumerate(RUNS):
        started = time.monotonic()
        record = await run_task(
            BY_ID[task_id],
            repeat,
            llm=PolicyLLM(policy),
            limits=Limits(max_calls, 120.0),
            store=store,
            meta=meta,
        )
        store.append(record)
        want = "false_success" if max_calls == 1 else "pass"
        ok = record["outcome"] == want and (
            max_calls != 1 or record["limit_hit"] == "tool_calls"
        )
        failures += not ok
        print(
            f"{'OK  ' if ok else 'FAIL'} {task_id:<18} limit={max_calls:<3} "
            f"outcome={record['outcome']} (want {want}) verdict={record['verdict']['status']} "
            f"answer={record['verdict']['answer']} answer_correct={record['answer_correct']} "
            f"calls={record['tool_calls']} limit_hit={record['limit_hit']} "
            f"rounds={record['llm_rounds']} tokens={record['tokens']} "
            f"check={record['check']['passed'] if record['check'] else None} "
            f"error={record['harness_error']} {time.monotonic() - started:.1f}s",
            flush=True,
        )
        if record["reason"]:
            print(f"     reason: {record['reason']}", flush=True)
    print(f"transcripts: {store.transcripts_dir}")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    return asyncio.run(dry_run(args.out))


if __name__ == "__main__":
    sys.exit(main())
