"""Parse, clean, filter and render Playwright's AI-mode ARIA snapshot.

The snapshot is the indented ``- role "name" [attr] [ref=e12]: value`` text
that ``page.aria_snapshot(mode="ai")`` returns. ``parse`` turns it into a tree
of ``Node`` that ``to_text`` renders back byte for byte; lines it does not
recognise become opaque nodes that render verbatim. The cleaning passes and
the ``render`` filters each return a new tree and never mutate their input.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from kohakuterrarium.errors import NotFoundError

INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "gridcell",
        "link",
        "listbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "scrollbar",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
    }
)
CONTAINER_ROLES = frozenset(
    {
        "alertdialog",
        "application",
        "article",
        "banner",
        "complementary",
        "contentinfo",
        "dialog",
        "document",
        "feed",
        "form",
        "grid",
        "group",
        "iframe",
        "list",
        "main",
        "menu",
        "menubar",
        "navigation",
        "radiogroup",
        "region",
        "row",
        "search",
        "table",
        "tablist",
        "tabpanel",
        "toolbar",
        "tree",
        "treegrid",
    }
)
STATE_ATTRS = frozenset({"checked", "expanded", "pressed", "selected"})
BOUNDARY_ROLES = frozenset({"alertdialog", "dialog", "document", "iframe"})
SUMMARY_ROLES = frozenset({"article", "listitem", "row"})
MAX_DEPTH = 200
SUMMARY_CHARS = 60

_ROLE = re.compile(r"[^\s\"'\[\]:]+")
_ATTR = re.compile(r" \[([^\]=\s]+)(?:=([^\]]*))?\]")
_DQ_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)")
_DQ_SIMPLE = {
    "\\": "\\",
    '"': '"',
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_DQ_NEEDS = re.compile(r'[\\"\x00-\x1f\x7f-\x9f]')
_DQ_ENCODE = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}
_JS_NUMBER = re.compile(
    r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?|0[xX][0-9a-fA-F]+|0[oO][0-7]+"
    r"|0[bB][01]+|[+-]?Infinity"
)
_YAML_WORDS = frozenset({"y", "n", "yes", "no", "true", "false", "on", "off", "null"})
_WS = re.compile(r"\s+")


class UnknownRefError(NotFoundError):
    """A ``ref`` filter named a ref that is not in the snapshot."""

    def __init__(self, ref: str):
        super().__init__(f"ref {ref} is not in the current snapshot")
        self.ref = ref


@dataclass
class Node:
    """One snapshot line and the lines indented under it.

    ``kind`` is ``element``, ``text`` (``- text: ...``), ``prop`` (``- /url:
    ...``) or ``opaque`` (an unrecognised line kept verbatim in ``raw``).
    ``attrs`` keeps the bracketed attributes in their original order.
    ``uid`` is the ref the element had when parsed; it is never rendered and
    survives ``prune_refs``, so it identifies the element across snapshots.
    """

    kind: str = "element"
    role: str = ""
    name: str | None = None
    attrs: tuple[tuple[str, str | None], ...] = ()
    value: str | None = None
    children: list["Node"] = field(default_factory=list)
    raw: str = ""
    uid: str | None = field(default=None, compare=False, repr=False)

    def has_attr(self, key: str) -> bool:
        return any(k == key for k, _ in self.attrs)

    def attr(self, key: str) -> str | None:
        for k, v in self.attrs:
            if k == key:
                return v
        return None

    @property
    def ref(self) -> str | None:
        return self.attr("ref")

    @property
    def fingerprint(self) -> tuple[str, str]:
        return (self.role, self.name or "")


@dataclass(frozen=True)
class Rendered:
    """Rendered snapshot text plus what the ``max_chars`` cap cut off.

    ``fingerprints`` maps each ref that appears in ``text`` to its
    ``(role, name)``, ready for ``RefBook.record``.
    """

    text: str
    total_lines: int
    cut_lines: int
    cut_refs: int
    fingerprints: dict[str, tuple[str, str]]


def normalize(text: str) -> str:
    """Collapse whitespace runs to one space and trim."""
    return _WS.sub(" ", text).strip()


def parse(text: str) -> list[Node]:
    """Parse snapshot text into top-level nodes; never raises on odd lines."""
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        node = _parse_line(line[indent:])
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if len(stack) >= MAX_DEPTH:
            stack = stack[: MAX_DEPTH - 1]
        (stack[-1][1].children if stack else roots).append(node)
        stack.append((indent, node))
    return roots


def _parse_line(content: str) -> Node:
    if not content.startswith("- "):
        return Node(kind="opaque", raw=content)
    parsed = _parse_body(content[2:])
    return parsed if parsed is not None else Node(kind="opaque", raw=content)


def _parse_body(body: str) -> Node | None:
    if body.startswith("'"):
        split = _split_single_quoted(body)
        if split is None:
            return None
        key, rest = split
        head = _parse_key(key)
        if head is None or head[3] != len(key):
            return None
    else:
        head = _parse_key(body)
        if head is None:
            return None
        rest = body[head[3] :]
    role, name, attrs, _ = head
    if rest in ("", ":"):
        value = None
    elif rest.startswith(": "):
        value = _parse_value(rest[2:])
        if value is None:
            return None
    else:
        return None
    if role.startswith("/"):
        kind = "prop"
    elif role == "text" and name is None and not attrs:
        kind = "text"
    else:
        kind = "element"
    uid = next((v for k, v in attrs if k == "ref"), None)
    return Node(kind=kind, role=role, name=name, attrs=attrs, value=value, uid=uid)


def _split_single_quoted(body: str) -> tuple[str, str] | None:
    i = 1
    out: list[str] = []
    while i < len(body):
        ch = body[i]
        if ch == "'":
            if body[i + 1 : i + 2] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), body[i + 1 :]
        out.append(ch)
        i += 1
    return None


def _parse_key(key: str) -> tuple[str, str | None, tuple, int] | None:
    match = _ROLE.match(key)
    if match is None:
        return None
    role, pos = match.group(0), match.end()
    name = None
    if key.startswith(' "', pos):
        try:
            name, end = json.JSONDecoder().raw_decode(key, pos + 1)
        except ValueError:
            return None
        pos = end
    attrs = []
    while (attr := _ATTR.match(key, pos)) is not None:
        attrs.append((attr.group(1), attr.group(2)))
        pos = attr.end()
    return role, name, tuple(attrs), pos


def _parse_value(text: str) -> str | None:
    if not text.startswith('"'):
        return text
    i = 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == '"':
            break
        else:
            i += 1
    if i != len(text) - 1:
        return None
    return _DQ_ESCAPE.sub(_unescape, text[1:-1])


def _unescape(match: re.Match) -> str:
    code = match.group(1)
    if code[0] in "xu" and len(code) > 1:
        return chr(int(code[1:], 16))
    return _DQ_SIMPLE.get(code, code)


def _needs_quotes(text: str) -> bool:
    return bool(
        not text
        or text[0].isspace()
        or text[-1].isspace()
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", text)
        or text.startswith(("-", "["))
        or re.search(r"[\n:](\s|$)", text)
        or re.search(r"\s#", text)
        or re.search(r"[\n\r]", text)
        or text[0] in "&*],?!>|@\"'#%"
        or re.search(r"[{}`]", text)
        or _JS_NUMBER.fullmatch(text)
        or text.lower() in _YAML_WORDS
    )


def _quote_value(text: str) -> str:
    if not _needs_quotes(text):
        return text
    return '"' + _DQ_NEEDS.sub(_escape_char, text) + '"'


def _escape_char(match: re.Match) -> str:
    ch = match.group(0)
    return _DQ_ENCODE.get(ch, f"\\x{ord(ch):02x}")


def _key_of(node: Node) -> str:
    key = node.role
    if node.name is not None:
        key += " " + json.dumps(node.name, ensure_ascii=False)
    for k, v in node.attrs:
        key += f" [{k}]" if v is None else f" [{k}={v}]"
    if node.kind == "element" and _needs_quotes(key):
        return "'" + key.replace("'", "''") + "'"
    return key


def line_of(node: Node) -> str:
    """The node's own line, without indentation or the leading ``- ``."""
    if node.kind == "opaque":
        return node.raw
    key = _key_of(node)
    if node.value is not None:
        return f"{key}: {_quote_value(node.value)}"
    return key + ":" if node.children else key


def _render_line(node: Node, level: int) -> str:
    indent = "  " * level
    if node.kind == "opaque":
        return indent + node.raw
    return f"{indent}- {line_of(node)}"


def _lines(nodes: list[Node]) -> list[tuple[str, Node]]:
    return [(_render_line(node, level), node) for node, level, _ in walk(nodes)]


def to_text(nodes: list[Node]) -> str:
    """Render a tree back to snapshot text."""
    return "\n".join(line for line, _ in _lines(nodes))


def walk(nodes: list[Node]) -> Iterator[tuple[Node, int, Node | None]]:
    """Pre-order ``(node, depth, parent)`` over a tree."""
    stack = [(node, 0, None) for node in reversed(nodes)]
    while stack:
        node, depth, parent = stack.pop()
        yield node, depth, parent
        stack.extend((child, depth + 1, node) for child in reversed(node.children))


def index_refs(nodes: list[Node]) -> dict[str, Node]:
    """Map every ref in the tree to its node."""
    return {node.ref: node for node, _, _ in walk(nodes) if node.ref}


def fingerprints(nodes: list[Node]) -> dict[str, tuple[str, str]]:
    """Map every ref in the tree to its ``(role, name)``."""
    return {ref: node.fingerprint for ref, node in index_refs(nodes).items()}


def is_interactive(node: Node) -> bool:
    """A control a model would act on, judged on a raw or cleaned tree."""
    if node.kind != "element":
        return False
    if node.role in INTERACTIVE_ROLES or node.has_attr("cursor"):
        return True
    return any(node.has_attr(state) for state in STATE_ATTRS)


def _actionable(node: Node) -> bool:
    if is_interactive(node):
        return True
    return bool(node.ref) and node.role not in CONTAINER_ROLES


def drop_aria_hidden(nodes: list[Node]) -> list[Node]:
    """Remove every subtree Playwright tagged ``[aria-hidden]``."""
    return [
        replace(node, children=drop_aria_hidden(node.children))
        for node in nodes
        if not node.has_attr("aria-hidden")
    ]


def prune_refs(nodes: list[Node]) -> list[Node]:
    """Keep refs only on interactive, clickable, state-bearing or container nodes."""
    out = []
    for node in nodes:
        keep = is_interactive(node) or node.role in CONTAINER_ROLES
        attrs = node.attrs if keep else tuple(a for a in node.attrs if a[0] != "ref")
        out.append(replace(node, attrs=attrs, children=prune_refs(node.children)))
    return out


def strip_noise(nodes: list[Node]) -> list[Node]:
    """Drop ``[cursor=...]`` everywhere and ``[active]`` on non-interactive nodes.

    Run after ``prune_refs``: the cursor attribute is what marks a clickable
    generic as worth keeping a ref on.
    """
    out = []
    for node in nodes:
        drop = {"cursor"} if node.role in INTERACTIVE_ROLES else {"cursor", "active"}
        attrs = tuple(a for a in node.attrs if a[0] not in drop)
        out.append(replace(node, attrs=attrs, children=strip_noise(node.children)))
    return out


def drop_label_echo(nodes: list[Node]) -> list[Node]:
    """Drop ``text`` siblings that repeat the name of an interactive sibling."""
    names = {normalize(n.name) for n in nodes if n.name and is_interactive(n)}
    return [
        replace(node, children=drop_label_echo(node.children))
        for node in nodes
        if not (node.kind == "text" and normalize(node.value or "") in names)
    ]


def _plain_wrapper(node: Node) -> bool:
    return (
        node.kind == "element"
        and node.role == "generic"
        and node.name is None
        and node.value is None
        and not node.attrs
        and bool(node.children)
    )


def collapse_wrappers(nodes: list[Node]) -> list[Node]:
    """Replace a plain unnamed ``generic`` by its children when that loses no grouping.

    That is when it has a single child, or is itself its parent's only child.
    """
    out: list[Node] = []
    for node in nodes:
        node = replace(node, children=collapse_wrappers(node.children))
        if _plain_wrapper(node) and (len(node.children) == 1 or len(nodes) == 1):
            out.extend(node.children)
        else:
            out.append(node)
    return out


def clean(nodes: list[Node]) -> list[Node]:
    """All cleaning passes in order; see each pass for what it removes."""
    nodes = drop_aria_hidden(nodes)
    nodes = prune_refs(nodes)
    nodes = strip_noise(nodes)
    nodes = drop_label_echo(nodes)
    return collapse_wrappers(nodes)


def split_boxes(nodes: list[Node]) -> tuple[list[Node], dict[str, str]]:
    """Take ``[box=x,y,w,h]`` off every line; return the tree and ``{uid: box}``."""
    boxes: dict[str, str] = {}

    def strip(level: list[Node]) -> list[Node]:
        out = []
        for node in level:
            box = node.attr("box") if node.has_attr("box") else None
            if box is not None and node.uid:
                boxes[node.uid] = box
            attrs = tuple(a for a in node.attrs if a[0] != "box")
            out.append(replace(node, attrs=attrs, children=strip(node.children)))
        return out

    stripped = strip(nodes)
    return (stripped, boxes) if boxes else (nodes, {})


def add_boxes(nodes: list[Node], boxes: dict[str, str]) -> list[Node]:
    """Put each element's box from ``split_boxes`` back as its last attribute."""
    if not boxes:
        return nodes
    out = []
    for node in nodes:
        box = boxes.get(node.uid) if node.uid else None
        attrs = node.attrs + ((("box", box),) if box is not None else ())
        out.append(replace(node, attrs=attrs, children=add_boxes(node.children, boxes)))
    return out


def select_ref(nodes: list[Node], ref: str) -> list[Node]:
    """The subtree rooted at ``ref``; raises ``UnknownRefError`` when absent."""
    node = index_refs(nodes).get(ref)
    if node is None:
        raise UnknownRefError(ref)
    return [node]


def limit_depth(nodes: list[Node], depth: int) -> list[Node]:
    """Keep ``depth`` levels; nodes on the last level keep only their props."""
    if depth <= 1:
        return [
            replace(node, children=[c for c in node.children if c.kind == "prop"])
            for node in nodes
        ]
    return [
        replace(node, children=limit_depth(node.children, depth - 1)) for node in nodes
    ]


def _matches(node: Node, needle: str) -> bool:
    fields = (node.role, node.name, node.value, node.raw)
    return any(f and needle in f.lower() for f in fields)


def filter_query(nodes: list[Node], query: str) -> list[Node]:
    """Matching nodes with their subtree and ancestor chain.

    Ancestors also keep their own ``text`` and prop children as context.
    """
    needle = query.lower()
    kept: list[Node] = []
    hit = False
    for node in nodes:
        if _matches(node, needle):
            kept.append(node)
            hit = True
            continue
        inner = filter_query(node.children, query)
        if inner:
            kept.append(replace(node, children=inner))
            hit = True
        elif node.kind in ("text", "prop"):
            kept.append(node)
    return kept if hit else []


def _summary(node: Node) -> str:
    parts: list[str] = []
    stack = list(reversed(node.children))
    while stack:
        child = stack.pop()
        if _actionable(child) or child.kind in ("prop", "opaque"):
            continue
        parts.extend(p for p in (child.name, child.value) if p)
        stack.extend(reversed(child.children))
    text = normalize(" ".join(parts))
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1] + "…"


def _summary_child(node: Node) -> list[Node]:
    summary = _summary(node)
    return [Node(kind="text", role="text", value=summary)] if summary else []


def filter_interactive(nodes: list[Node]) -> list[Node]:
    """Interactive nodes under their nearest named or boundary containers.

    Unnamed rows, list items and articles are kept as context when they hold
    text, which they carry as a ``text`` child; other containers flatten away.
    """
    out: list[Node] = []
    for node in nodes:
        if node.kind != "element":
            continue
        if _actionable(node):
            out.append(_as_actionable(node))
            continue
        inner = filter_interactive(node.children)
        if not inner:
            continue
        if node.role in SUMMARY_ROLES and not node.name:
            lead = _summary_child(node)
            if lead:
                out.append(replace(node, value=None, children=lead + inner))
                continue
        if node.role in BOUNDARY_ROLES or (node.role in CONTAINER_ROLES and node.name):
            out.append(replace(node, value=None, children=inner))
        else:
            out.extend(inner)
    return out


def _as_actionable(node: Node) -> Node:
    children: list[Node] = []
    for child in node.children:
        if child.kind in ("prop", "text"):
            children.append(child)
        elif child.kind == "element":
            children.extend(filter_interactive([child]))
    if (
        not node.name
        and node.value is None
        and not any(c.kind == "text" for c in children)
    ):
        children = _summary_child(node) + children
    return replace(node, children=children)


def render(
    nodes: list[Node],
    *,
    ref: str | None = None,
    depth: int | None = None,
    query: str | None = None,
    interactive: bool = False,
    max_chars: int | None = None,
) -> Rendered:
    """Apply ``ref`` → ``interactive`` → ``query`` → ``depth``, then cap at ``max_chars``.

    The cap cuts at a line boundary; ``cut_lines`` / ``cut_refs`` count what
    was left out so the caller can say how to narrow.
    """
    if depth is not None and depth < 1:
        raise ValueError("depth must be at least 1")
    if ref is not None:
        nodes = select_ref(nodes, ref)
    if interactive:
        nodes = filter_interactive(nodes)
    if query:
        nodes = filter_query(nodes, query)
    if depth is not None:
        nodes = limit_depth(nodes, depth)
    lines = _lines(nodes)
    kept = len(lines)
    if max_chars is not None:
        used = -1
        for i, (line, _) in enumerate(lines):
            used += len(line) + 1
            if used > max_chars:
                kept = i
                break
    shown, cut = lines[:kept], lines[kept:]
    return Rendered(
        text="\n".join(line for line, _ in shown),
        total_lines=len(lines),
        cut_lines=len(cut),
        cut_refs=sum(1 for _, node in cut if node.ref),
        fingerprints={node.ref: node.fingerprint for _, node in shown if node.ref},
    )
