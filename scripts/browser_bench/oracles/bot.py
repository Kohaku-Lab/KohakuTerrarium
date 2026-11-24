"""The oracle driver: ``BrowserSession`` public operations plus snapshot-text parsing.

Oracles find refs in snapshot text the way a model would read it, never by
CSS selectors; ``Bot.note`` records anything awkward through the session API
as a finding for the report.
"""

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kohakuterrarium.browser.session import BrowserResult, BrowserSession

from ..model import Task
from ..site import BenchSite

_LINE = re.compile(
    r'^(?P<indent>\s*)- (?P<role>[\w-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?'
    r"(?P<attrs>(?: \[[^\]]*\])*)(?::\s?(?P<text>.*))?$"
)
_ATTR = re.compile(r"\[([^\]=]+)(?:=([^\]]*))?\]")
_BLOCKED_ITEM = re.compile(r"^\s+(?P<host>\S+?):(?P<port>\d+) \((?P<reason>.*)\)$")
_BLOCKED_LANDING = re.compile(
    r"it led to (?P<host>\S+?):(?P<port>\d+), which the browser network policy "
    r"blocked \((?P<reason>.*)\); the page did not load"
)


class OracleError(AssertionError):
    """A scripted solution could not proceed; the message says where."""


@dataclass(frozen=True)
class Node:
    """One snapshot line: depth, role, accessible name, attributes, inline text."""

    index: int
    depth: int
    role: str
    name: str
    attrs: Mapping[str, str]
    text: str

    @property
    def ref(self) -> str | None:
        return self.attrs.get("ref")

    @property
    def label(self) -> str:
        return self.name or self.text


def parse(text: str) -> list[Node]:
    """Every ``- role "name" [attrs]: text`` line of a snapshot, in order."""
    nodes = []
    for line in text.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        attrs = {k: (v or "") for k, v in _ATTR.findall(match.group("attrs") or "")}
        name = (match.group("name") or "").replace('\\"', '"')
        nodes.append(
            Node(
                len(nodes),
                len(match.group("indent")) // 2,
                match.group("role"),
                name,
                attrs,
                (match.group("text") or "").strip(),
            )
        )
    return nodes


def subtree(nodes: Sequence[Node], root: Node) -> list[Node]:
    """``root`` and every line nested under it."""
    out = [root]
    for node in nodes[root.index + 1 :]:
        if node.depth <= root.depth:
            break
        out.append(node)
    return out


def find(
    text: str | Sequence[Node],
    role: str | None = None,
    name: str | None = None,
    *,
    contains: str | None = None,
    nth: int = 0,
) -> Node:
    """The ``nth`` line with ``role`` whose name (or text) equals ``name`` or contains ``contains``."""
    nodes = parse(text) if isinstance(text, str) else list(text)
    hits = [
        n
        for n in nodes
        if (role is None or n.role == role)
        and (name is None or n.label == name)
        and (contains is None or contains.lower() in n.label.lower())
    ]
    if len(hits) <= nth:
        want = f"{role or 'any'} {name or contains!r}"
        raise OracleError(f"no {want} (#{nth}) in snapshot:\n{_excerpt(nodes)}")
    return hits[nth]


def ref(
    text: str | Sequence[Node],
    role: str | None = None,
    name: str | None = None,
    **kw: Any,
) -> str:
    """The ref of :func:`find`'s line; an error when that line carries no ref."""
    node = find(text, role, name, **kw)
    if node.ref is None:
        raise OracleError(f"{node.role} {node.label!r} has no ref")
    return node.ref


def container_with(
    text: str, role: str, inner: str, *, exact: bool = False
) -> list[Node]:
    """The subtree of the first ``role`` line holding a line whose text matches ``inner``."""
    nodes = parse(text)
    for node in nodes:
        if node.role != role:
            continue
        tree = subtree(nodes, node)
        if any(
            (n.label == inner if exact else inner.lower() in n.label.lower())
            for n in tree[1:]
        ):
            return tree
    raise OracleError(f"no {role} containing {inner!r} in snapshot:\n{_excerpt(nodes)}")


def _excerpt(nodes: Sequence[Node], limit: int = 60) -> str:
    lines = [
        f"{'  ' * n.depth}- {n.role} {n.label!r} {dict(n.attrs)}" for n in nodes[:limit]
    ]
    return "\n".join(lines)


def page_body(result: BrowserResult) -> str:
    """The text inside the result's ``page_content`` wrapper (title line excluded)."""
    lines = result.text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("<page_content"))
        end = next(
            i for i in range(len(lines) - 1, -1, -1) if lines[i] == "</page_content>"
        )
    except StopIteration:
        return ""
    return "\n".join(lines[start + 1 : end])


def blocked_in(text: str) -> list[dict[str, Any]]:
    """``{"host", "port", "reason", "blocked"}`` per blocked request a result names.

    That is each entry of its ``Blocked N`` list and each action whose page
    the network policy blocked.
    """
    out = []
    listing = False
    for line in text.splitlines():
        if re.match(r"^Blocked \d+ requests?:$", line.strip()):
            listing = True
            continue
        match = _BLOCKED_ITEM.match(line) if listing else None
        match = match or _BLOCKED_LANDING.search(line)
        if match:
            host = match.group("host").strip("[]")
            out.append(
                {
                    "host": host,
                    "port": int(match.group("port")),
                    "reason": match.group("reason"),
                    "blocked": True,
                }
            )
        else:
            listing = False
    return out


class Bot:
    """One scripted run of one task through a ``BrowserSession``."""

    def __init__(
        self, session: BrowserSession, site: BenchSite, task: Task, workdir: Path
    ) -> None:
        self.session = session
        self.site = site
        self.task = task
        self.workdir = workdir
        self.egress: list[dict[str, Any]] = []
        self.findings: list[str] = []
        self.results: list[BrowserResult] = []
        self.files: dict[str, str] = {}
        self.downloads = workdir / "downloads"
        self.trace: list[tuple[str, float]] = []
        self._mark = time.monotonic()

    def url(self, path: str | None = None, listener: str = "main") -> str:
        return self.site.url(self.task, path, listener)

    def note(self, finding: str) -> None:
        """Record a product finding observed while driving the task."""
        if finding not in self.findings:
            self.findings.append(finding)

    def _seen(self, result: BrowserResult, what: str = "") -> BrowserResult:
        now = time.monotonic()
        self.trace.append((what or "call", now - self._mark))
        self._mark = now
        self.results.append(result)
        self.egress.extend(blocked_in(result.text))
        return result

    async def go(self, path: str | None = None, *, url: str | None = None) -> str:
        """Navigate to a task path (or ``url``) and return the full snapshot text."""
        result = self._seen(
            await self.session.navigate(url or self.url(path)), "navigate"
        )
        if result.is_error:
            raise OracleError(f"navigation failed:\n{result.text}")
        return await self.snap()

    async def navigate(self, url: str) -> BrowserResult:
        return self._seen(await self.session.navigate(url), "navigate")

    async def snap(self, **options: Any) -> str:
        result = self._seen(await self.session.snapshot(**options), "snapshot")
        if result.is_error:
            raise OracleError(f"snapshot failed:\n{result.text}")
        return result.text

    async def act(
        self, *actions: dict[str, Any], files: Sequence[str] = (), ok: bool = True
    ) -> BrowserResult:
        """Run one batch; with ``ok`` an incomplete batch is an :class:`OracleError`."""
        kinds = ",".join(a.get("kind", "?") for a in actions)
        result = self._seen(
            await self.session.act(list(actions), files=files), f"act {kinds}"
        )
        if ok and not result.meta.get("ok"):
            raise OracleError(f"act did not complete:\n{result.text}")
        return result

    async def tabs(self) -> str:
        """The ``tabs_list`` text: one line per tab, the active one marked."""
        return self._seen(await self.session.tabs_list(), "tabs_list").text

    async def js(self, expression: str, *, element: str | None = None) -> Any:
        """Evaluate JavaScript and decode the JSON the session returns."""
        result = self._seen(
            await self.session.evaluate(expression, ref=element), "evaluate"
        )
        if result.is_error:
            raise OracleError(f"evaluate failed:\n{result.text}")
        return json.loads(page_body(result))

    def state(self) -> dict[str, Any]:
        return self.site.state(self.task.id)
