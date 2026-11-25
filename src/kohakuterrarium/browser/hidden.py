"""Find visually hidden page text and password values, and apply them to a snapshot.

``PROBE_JS`` runs in each frame (``frame.evaluate(PROBE_JS)``) and reports
the text the user cannot see but the ARIA snapshot still contains, plus the
whole current value of every password field (and of a field whose
``autocomplete`` names a password, as one toggled to show its text is). The
Python half drops that text from a parsed snapshot, never touching
interactive elements, and redacts passwords. Chromium's ARIA snapshot
carries password values in plain text, so where the probe did not run every
text-field value is withheld instead.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from kohakuterrarium.browser.snapshot import Node, is_interactive, normalize, walk

PROBE_JS = r"""
() => {
  const MAX_NODES = 20000, MAX_ITEMS = 300, MAX_LEN = 1000, MAX_PASSWORDS = 20;
  const BUDGET_MS = 300;
  const start = performance.now();
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const clip = (s) => (s.length > MAX_LEN ? s.slice(0, MAX_LEN) : s);
  const hidden = new Set();
  const visible = new Set();
  const passwords = [];
  const counts = new Map();
  let truncated = false;
  let visited = 0;
  const root = document.documentElement;
  if (!root) return { hidden: [], visible: [], passwords: [], truncated: false };
  const docW = Math.max(root.scrollWidth, innerWidth);
  const docH = Math.max(root.scrollHeight, innerHeight);
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "IFRAME", "OBJECT"]);
  const up = (el) => el.parentElement || (el.parentNode && el.parentNode.host) || null;
  const rgba = (c) => {
    const m = /^rgba?\(([^)]+)\)$/.exec(c || "");
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
    return p.length >= 3 ? [p[0], p[1], p[2], p.length > 3 ? p[3] : 1] : null;
  };
  const bgCache = new Map();
  const background = (el) => {
    const chain = [];
    let result = [255, 255, 255, 1];
    for (let cur = el; cur; cur = up(cur)) {
      if (bgCache.has(cur)) { result = bgCache.get(cur); break; }
      chain.push(cur);
      const st = getComputedStyle(cur);
      if (st.backgroundImage && st.backgroundImage !== "none") { result = null; break; }
      const c = rgba(st.backgroundColor);
      if (c === null) { result = null; break; }
      if (c[3] >= 0.99) { result = c; break; }
      if (c[3] > 0.01) { result = null; break; }
    }
    for (const e of chain) bgCache.set(e, result);
    return result;
  };
  const clipsText = (el) => {
    for (let cur = el, i = 0; cur && i < 4; cur = up(cur), i++) {
      const st = getComputedStyle(cur);
      if (st.backgroundClip === "text" || st.webkitBackgroundClip === "text") return true;
    }
    return false;
  };
  const boxHides = (st, el) => {
    if (parseFloat(st.opacity) < 0.05) return true;
    const ov = (st.overflowX !== "visible") || (st.overflowY !== "visible");
    if (ov && (el.clientWidth === 0 || el.clientHeight === 0)) return true;
    if (ov && el.offsetWidth <= 1 && el.offsetHeight <= 1) return true;
    const rect = /^rect\(([^)]*)\)$/.exec(st.clip || "");
    if (rect && (st.position === "absolute" || st.position === "fixed")) {
      const v = rect[1].split(/[\s,]+/).map(parseFloat);
      if (v.length === 4 && (v[1] - v[3] <= 1 || v[2] - v[0] <= 1)) return true;
    }
    const inset = /^inset\(\s*([\d.]+)%/.exec(st.clipPath || "");
    if (inset && parseFloat(inset[1]) >= 50) return true;
    return false;
  };
  const textHides = (text, el, st) => {
    if (parseFloat(st.fontSize) < 2) return true;
    if (parseFloat(st.textIndent) < -999) return true;
    const fill = rgba(st.webkitTextFillColor) || rgba(st.color);
    if (fill && fill[3] < 0.05 && !clipsText(el)) return true;
    if (fill && fill[3] >= 0.05) {
      const bg = background(el);
      if (bg && Math.abs(bg[0] - fill[0]) + Math.abs(bg[1] - fill[1]) + Math.abs(bg[2] - fill[2]) <= 24) return true;
    }
    const range = document.createRange();
    range.selectNodeContents(text);
    const r = range.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return true;
    const x = r.left + scrollX, y = r.top + scrollY;
    return r.right + scrollX <= 0 || r.bottom + scrollY <= 0 || x >= docW || y >= docH;
  };
  const tally = (el, isHidden) => {
    for (let cur = el; cur; cur = up(cur)) {
      let c = counts.get(cur);
      if (!c) { c = [0, 0]; counts.set(cur, c); }
      c[isHidden ? 0 : 1] += 1;
    }
  };
  const stack = [[root, false]];
  while (stack.length) {
    if (++visited > MAX_NODES || performance.now() - start > BUDGET_MS) { truncated = true; break; }
    const [node, inherited] = stack.pop();
    try {
      if (node.nodeType === Node.TEXT_NODE) {
        const value = norm(node.data);
        const el = node.parentElement || (node.parentNode && node.parentNode.host);
        if (!value || !el) continue;
        const st = getComputedStyle(el);
        if (st.visibility !== "visible") continue;
        const isHidden = inherited || textHides(node, el, st);
        (isHidden ? hidden : visible).add(clip(value));
        tally(el, isHidden);
        continue;
      }
      if (node.nodeType !== Node.ELEMENT_NODE || SKIP.has(node.tagName)) continue;
      const st = getComputedStyle(node);
      if (st.display === "none") continue;
      if (node.tagName === "INPUT" && node.value && passwords.length < MAX_PASSWORDS
          && (node.type === "password" || /password/i.test(node.getAttribute("autocomplete") || "")))
        passwords.push(node.value);
      const flag = inherited || boxHides(st, node);
      const kids = node.shadowRoot ? [...node.shadowRoot.childNodes, ...node.childNodes] : [...node.childNodes];
      for (let i = kids.length - 1; i >= 0; i--) stack.push([kids[i], flag]);
    } catch (e) {
      continue;
    }
  }
  for (const [el, c] of counts) {
    if (c[0] === 0 || c[1] !== 0) continue;
    const parent = up(el);
    const pc = parent && counts.get(parent);
    if (pc && pc[1] === 0) continue;
    const block = norm(el.textContent);
    if (block) hidden.add(clip(block));
  }
  const hiddenList = [...hidden].slice(0, MAX_ITEMS);
  if (hidden.size > MAX_ITEMS) truncated = true;
  const overlap = hiddenList.filter((s) => visible.has(s));
  return { hidden: hiddenList, visible: overlap, passwords, truncated };
}
"""

REDACTED = "(redacted)"
WITHHELD = "(withheld)"
TEXT_FIELD_ROLE = "textbox"
MAX_STRINGS = 1000
PROBE_CLIP_CHARS = 1000
MAX_STRING_CHARS = PROBE_CLIP_CHARS
MAX_PASSWORDS = 50
MIN_EXCISE_CHARS = 16
MIN_SUBSTRING_PASSWORD = 4


def _strings(value: object, limit: int | None = MAX_STRING_CHARS) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s[:limit] for s in value if isinstance(s, str)]


@dataclass(frozen=True)
class ProbeResult:
    """The probe output of every frame, normalised and merged.

    ``candidates`` is the hidden text that does not also appear as visible
    text; only candidates are ever removed from a snapshot.
    """

    hidden: frozenset[str] = frozenset()
    visible: frozenset[str] = frozenset()
    passwords: tuple[str, ...] = ()
    truncated: bool = False

    @property
    def candidates(self) -> frozenset[str]:
        return self.hidden - self.visible

    @classmethod
    def merge(cls, frames: Iterable[object]) -> "ProbeResult":
        """Merge raw per-frame probe dicts; malformed entries are skipped."""
        hidden: set[str] = set()
        visible: set[str] = set()
        passwords: list[str] = []
        truncated = False
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            for target, key in ((hidden, "hidden"), (visible, "visible")):
                for text in _strings(frame.get(key)):
                    if len(target) < MAX_STRINGS and (norm := normalize(text)):
                        target.add(norm)
            for value in _strings(frame.get("passwords"), None):
                if (
                    value.strip()
                    and value not in passwords
                    and len(passwords) < MAX_PASSWORDS
                ):
                    passwords.append(value)
            truncated = truncated or frame.get("truncated") is True
        return cls(frozenset(hidden), frozenset(visible), tuple(passwords), truncated)


@dataclass(frozen=True)
class HiddenOutcome:
    """A snapshot tree with hidden text removed, the count removed, and passwords."""

    nodes: list[Node]
    dropped: int
    passwords: tuple[str, ...] = ()


def _has_interactive(node: Node) -> bool:
    return any(is_interactive(n) for n, _, _ in walk(node.children))


def _excise(value: str, runs: list[str]) -> tuple[str, int]:
    text = normalize(value)
    cuts = 0
    for run in runs:
        if run not in text:
            continue
        if len(run) >= PROBE_CLIP_CHARS:
            text = text[: text.index(run)]
            cuts += 1
        else:
            cuts += text.count(run)
            text = text.replace(run, " ")
    return (normalize(text), cuts) if cuts else (value, 0)


def _is_hidden(text: str, candidates: frozenset[str], runs: list[str]) -> bool:
    norm = normalize(text)
    if norm in candidates:
        return True
    return any(len(r) >= PROBE_CLIP_CHARS and norm.startswith(r) for r in runs)


def _drop(
    nodes: list[Node], candidates: frozenset[str], runs: list[str]
) -> tuple[list[Node], int]:
    out: list[Node] = []
    dropped = 0
    for node in nodes:
        if node.kind in ("prop", "opaque") or is_interactive(node):
            out.append(node)
            continue
        if _has_interactive(node):
            children, count = _drop(node.children, candidates, runs)
            out.append(replace(node, children=children))
            dropped += count
            continue
        own = node.value if node.value is not None else node.name
        if own is not None and _is_hidden(own, candidates, runs):
            dropped += 1
            continue
        current = node
        if node.value is not None:
            value, cuts = _excise(node.value, runs)
            dropped += cuts
            if not value:
                continue
            current = replace(node, value=value)
        children, count = _drop(node.children, candidates, runs)
        dropped += count
        if (
            node.children
            and not children
            and current.name is None
            and current.value is None
        ):
            continue
        out.append(replace(current, children=children))
    return out, dropped


def drop_hidden(nodes: list[Node], probe: ProbeResult) -> HiddenOutcome:
    """Remove non-interactive snapshot text the probe found visually hidden.

    A node goes when its own text (value, else name) equals a candidate after
    whitespace normalisation; a candidate of at least ``MIN_EXCISE_CHARS`` is
    also cut out of longer text, and one the probe clipped at
    ``PROBE_CLIP_CHARS`` removes everything from where it starts. Interactive
    nodes, their subtrees, props and opaque lines are never changed.
    ``dropped`` counts removed blocks.
    """
    candidates = probe.candidates
    if not candidates:
        return HiddenOutcome(nodes, 0)
    runs = sorted(
        (c for c in candidates if len(c) >= MIN_EXCISE_CHARS), key=len, reverse=True
    )
    out, dropped = _drop(nodes, candidates, runs)
    return HiddenOutcome(out, dropped)


def _redact_text(text: str | None, secrets: list[str], exact: set[str]) -> str | None:
    if text is None:
        return None
    if text.strip() in exact:
        return REDACTED
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


def redact_passwords(nodes: list[Node], passwords: Iterable[str]) -> list[Node]:
    """Replace password values in page-derived text with ``REDACTED``.

    Only names, values (which covers text and prop lines) and opaque lines
    change; roles and attributes such as ``ref`` never do. Values shorter than ``MIN_SUBSTRING_PASSWORD`` are replaced only where a
    whole field equals them; longer ones wherever they occur. Matching is
    literal, never a pattern.
    """
    values = {p for p in passwords if p.strip()}
    if not values:
        return nodes
    long_values = {p for p in values if len(p) >= MIN_SUBSTRING_PASSWORD}
    long_values |= {normalize(p) for p in long_values}
    secrets = sorted(long_values, key=len, reverse=True)
    exact = values | {normalize(p) for p in values}
    return _redact(nodes, secrets, exact)


def _redact(nodes: list[Node], secrets: list[str], exact: set[str]) -> list[Node]:
    return [
        replace(
            node,
            name=_redact_text(node.name, secrets, exact),
            value=_redact_text(node.value, secrets, exact),
            raw=_redact_text(node.raw, secrets, exact) or "",
            children=_redact(node.children, secrets, exact),
        )
        for node in nodes
    ]


def withhold_field_values(nodes: list[Node]) -> list[Node]:
    """Replace the value of every text field (``textbox``) with ``WITHHELD``."""
    return [
        replace(
            node,
            value=(
                WITHHELD
                if node.role == TEXT_FIELD_ROLE and node.value is not None
                else node.value
            ),
            children=withhold_field_values(node.children),
        )
        for node in nodes
    ]


def apply_probe(nodes: list[Node], probe: ProbeResult) -> HiddenOutcome:
    """Drop hidden text, then redact the probe's password values."""
    outcome = drop_hidden(nodes, probe)
    return HiddenOutcome(
        redact_passwords(outcome.nodes, probe.passwords),
        outcome.dropped,
        probe.passwords,
    )
