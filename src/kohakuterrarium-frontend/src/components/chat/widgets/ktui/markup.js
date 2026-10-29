/**
 * kt.ui inline markup: split assistant text into Markdown and ``<ui>`` blocks,
 * and convert one block into a kt.ui v1 spec.
 *
 * Twin of ``kohakuterrarium/builtins/kt_ui/markup_lex.py`` and ``markup.py``;
 * both run ``tests/fixtures/kt_ui_markup.json``. The syntax is specified in
 * ``plans/chat-widgets/08-inline-ui.md``.
 */

import { evaluate, freeNames, parse, toDisplay } from "./expr.js"

export class MarkupError extends Error {}

class Incomplete extends Error {}

export const TEXT_CHILD = {
  text: "value",
  heading: "value",
  badge: "value",
  stat: "value",
  button: "label",
  callout: "value",
  quote: "value",
  link: "label",
  quiz: "question",
}
export const RAW_CHILD = {
  code: "value",
  diagram: "source",
  html: "source",
  math: "value",
  copy: "value",
}
/** Raw elements whose own tag may nest inside their content (a whole HTML document). */
const NESTED_RAW = new Set(["html"])

const INPUT = "bind:var label:text"
/** Prop kinds per component, as in ``components.py`` (``name:kind``). */
const COMPONENT_KINDS = {
  stack: "direction:enum gap:enum wrap:bool align:enum children:children",
  grid: "columns:int gap:enum children:children",
  card: "title:text subtitle:text children:children",
  section: "title:text children:children",
  tabs: "children:tabs",
  tab: "label:text children:children",
  divider: "",
  accordion: "title:text open:bool children:children",
  text: "value:text size:enum muted:bool",
  heading: "value:text level:int",
  badge: "value:text tone:enum",
  stat: "label:text value:text hint:text tone:enum trend:list",
  callout: "value:text title:text tone:enum",
  quote: "value:text cite:text",
  steps: "items:list current:int",
  timeline: "items:list",
  progress: "value:num max:num label:text tone:enum style:enum",
  tree: "items:list open:bool",
  kv: "fields:list",
  list: "items:list ordered:bool",
  table: "columns:list rows:list sortable:bool filter:bool",
  image: "src:src alt:text width:int",
  code: "value:text lang:literal",
  chart: "kind:enum labels:list series:list title:text height:int min:num max:num",
  diagram: "source:text title:text",
  html: "source:literal title:text height:int",
  math: "value:literal display:bool",
  link: "href:url label:text style:enum",
  icon: "name:enum label:text size:enum tone:enum",
  gallery: "items:list columns:int",
  compare: "items:list",
  copy: "value:text label:text",
  timer: "mode:enum seconds:num label:text autostart:bool",
  quiz: "question:text options:list answer:literal explain:text",
  number: `${INPUT} min:num max:num step:num`,
  slider: `${INPUT} min:num max:num step:num`,
  text_input: `${INPUT} placeholder:text multiline:bool`,
  select: `${INPUT} options:list`,
  radio: `${INPUT} options:list`,
  toggle: INPUT,
  checkbox: INPUT,
  date: INPUT,
  rating: `${INPUT} max:int`,
  button: "label:text style:enum set:setmap send:name message:text data:map disabled:bool",
  form: "children:children send:name message:text submit:text data:map",
  each: "items:expr as:literal child:child",
}
const COMMON_KINDS = { type: "literal", visible: "bool", alt: "text" }
const COMPONENTS = Object.fromEntries(
  Object.entries(COMPONENT_KINDS).map(([type, spec]) => [
    type,
    Object.fromEntries(spec ? spec.split(" ").map((pair) => pair.split(":")) : []),
  ]),
)
const SEND_TO_MESSAGE = new Set(["button", "form"])
const DECLARATIONS = new Set(["state", "let"])

const NAME = /[A-Za-z_][A-Za-z0-9_.-]*/y
const ATTR = /[A-Za-z_][A-Za-z0-9_-]*/y
const BLOCK_START = /^<ui(?=[\s>/]|$)/
const FENCE = /^(`{3,}|~{3,})/
const ANY_CLOSE = /<\/[A-Za-z_]/g
const PARTIAL_START = /^<(u(i)?)?$/
const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/
const LETTER = /^[A-Za-z_]$/

// ── lexing ──────────────────────────────────────────────────────────────

function readQuoted(text, i) {
  const end = text.indexOf(text[i], i + 1)
  if (end < 0) throw new Incomplete("unterminated string")
  return end + 1
}

/** Index just past the balanced ``{…}`` starting at ``text[i]``. */
export function readBraced(text, i) {
  let depth = 0
  let j = i
  while (j < text.length) {
    const ch = text[j]
    if (ch === '"' || ch === "'") {
      const end = text.indexOf(ch, j + 1)
      if (end < 0) throw new Incomplete("unterminated expression")
      j = end + 1
      continue
    }
    if (ch === "{") depth += 1
    else if (ch === "}") {
      depth -= 1
      if (depth === 0) return j + 1
    }
    j += 1
  }
  throw new Incomplete("unterminated expression")
}

function matchAt(re, text, i) {
  re.lastIndex = i
  const m = re.exec(text)
  return m ? m[0] : null
}

const isSpace = (ch, newlines = true) =>
  ch === " " || ch === "\t" || (newlines && (ch === "\r" || ch === "\n"))

function openTag(text, i) {
  const n = text.length
  const name = matchAt(NAME, text, i + 1)
  const token = { kind: "open", start: i, end: i, name, attrs: [], selfClosing: false }
  let j = i + 1 + name.length
  for (;;) {
    while (j < n && isSpace(text[j])) j += 1
    if (j >= n) throw new Incomplete("unfinished tag")
    if (text.startsWith("/>", j)) {
      token.selfClosing = true
      token.end = j + 2
      return token
    }
    if (text[j] === ">") {
      token.end = j + 1
      return token
    }
    const attr = matchAt(ATTR, text, j)
    if (attr === null) {
      if (text[j] === "/" && j + 1 >= n) throw new Incomplete("unfinished tag")
      throw new MarkupError(`bad character ${JSON.stringify(text[j])} in <${name}>`)
    }
    j += attr.length
    let k = j
    while (k < n && isSpace(text[k], false)) k += 1
    if (k >= n) throw new Incomplete("unfinished tag")
    if (text[k] !== "=") {
      token.attrs.push({ name: attr, kind: "bare", value: "" })
      continue
    }
    k += 1
    while (k < n && isSpace(text[k], false)) k += 1
    if (k >= n) throw new Incomplete("unfinished tag")
    let end
    if (text[k] === '"' || text[k] === "'") {
      end = readQuoted(text, k)
      token.attrs.push({ name: attr, kind: "str", value: text.slice(k + 1, end - 1) })
    } else if (text[k] === "{") {
      end = readBraced(text, k)
      token.attrs.push({ name: attr, kind: "expr", value: text.slice(k + 1, end - 1) })
    } else {
      throw new MarkupError(`attribute '${attr}' of <${name}> needs a quoted value or {expression}`)
    }
    j = end
  }
}

function closeTag(text, i) {
  const name = matchAt(NAME, text, i + 2)
  let j = i + 2 + name.length
  while (j < text.length && isSpace(text[j])) j += 1
  if (j >= text.length) throw new Incomplete("unfinished tag")
  if (text[j] !== ">") throw new MarkupError(`bad closing tag </${name}`)
  return { kind: "close", start: i, end: j + 1, name }
}

function escapeRe(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** The matching close tag of ``name``, counting nested opens; else the end. */
function nestedEnd(text, i, name) {
  const pattern = new RegExp(`<(/?)${escapeRe(name)}(?=[\\s>/])[^>]*>`, "g")
  pattern.lastIndex = i
  let depth = 1
  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    if (match[1]) {
      depth -= 1
      if (depth === 0) return match.index
    } else if (!match[0].endsWith("/>")) depth += 1
  }
  return text.length
}

/** Where the text of a text or raw element ends: its own close tag, else any. */
function contentEnd(text, i, name) {
  if (NESTED_RAW.has(name)) return nestedEnd(text, i, name)
  const own = new RegExp(`</${escapeRe(name)}\\s*>`, "g")
  own.lastIndex = i
  const mine = own.exec(text)
  if (mine) return mine.index
  ANY_CLOSE.lastIndex = i
  const other = ANY_CLOSE.exec(text)
  return other ? other.index : text.length
}

function isTagStart(text, i) {
  const next = text[i + 1] ?? ""
  if (LETTER.test(next)) return true
  return next === "/" && LETTER.test(text[i + 2] ?? "")
}

/**
 * Tokens of ``text`` from ``start``: ``{tokens, incomplete}``.
 *
 * With ``stopAtUiClose`` lexing stops after the close tag that empties the
 * element stack. Input that ends inside a tag or expression sets
 * ``incomplete``; malformed tags throw ``MarkupError``.
 */
export function lex(text, start = 0, { stopAtUiClose = false } = {}) {
  const tokens = []
  const stack = []
  let i = start
  const n = text.length
  try {
    while (i < n) {
      if (text[i] === "<" && isTagStart(text, i)) {
        if (text[i + 1] === "/") {
          const token = closeTag(text, i)
          tokens.push(token)
          i = token.end
          const at = stack.lastIndexOf(token.name)
          if (at >= 0) stack.length = at
          if (stopAtUiClose && !stack.length) break
          continue
        }
        const token = openTag(text, i)
        tokens.push(token)
        i = token.end
        if (token.selfClosing) {
          if (stopAtUiClose && !stack.length) break
          continue
        }
        stack.push(token.name)
        if (TEXT_CHILD[token.name] || RAW_CHILD[token.name]) {
          const end = contentEnd(text, i, token.name)
          if (end > i)
            tokens.push({
              kind: "text",
              start: i,
              end,
              text: text.slice(i, end),
              raw: !!RAW_CHILD[token.name],
            })
          i = end
        }
        continue
      }
      let next = text.indexOf("<", i + 1)
      while (next >= 0 && !isTagStart(text, next)) next = text.indexOf("<", next + 1)
      const end = next < 0 ? n : next
      tokens.push({ kind: "text", start: i, end, text: text.slice(i, end) })
      i = end
    }
  } catch (err) {
    if (err instanceof Incomplete) return { tokens, incomplete: err.message }
    throw err
  }
  return { tokens, incomplete: "" }
}

// ── splitting ───────────────────────────────────────────────────────────

function blockEnd(text, start) {
  let lexed
  try {
    lexed = lex(text, start, { stopAtUiClose: true })
  } catch (err) {
    if (!(err instanceof MarkupError)) throw err
    const close = text.indexOf("</ui>", start)
    return close >= 0 ? [close + 5, true] : [text.length, false]
  }
  if (lexed.incomplete || !lexed.tokens.length) return [text.length, false]
  const last = lexed.tokens[lexed.tokens.length - 1]
  const first = lexed.tokens[0]
  if (last.kind === "close" && last.name === "ui") return [last.end, true]
  if (first.selfClosing && lexed.tokens.length === 1) return [first.end, true]
  return [text.length, false]
}

/**
 * Split a reply into ``{kind: "markdown", text}`` and
 * ``{kind: "ui", source, closed}`` segments.
 *
 * With ``holdPartial`` (a reply still streaming), a last line that may grow
 * into ``<ui`` is left out, so the tag never flashes as text.
 */
export function splitMarkup(text, { holdPartial = false } = {}) {
  const source = typeof text === "string" ? text : ""
  const segments = []
  const n = source.length
  let mdStart = 0
  let i = 0
  let fence = ""
  let atLineStart = true
  while (i < n) {
    const nl = source.indexOf("\n", i)
    const lineEnd = nl < 0 ? n : nl
    const line = source.slice(i, lineEnd)
    const stripped = line.replace(/^ +/, "")
    const indent = line.length - stripped.length
    if (atLineStart && indent <= 3) {
      const marker = FENCE.exec(stripped)
      if (fence) {
        if (
          marker &&
          marker[1][0] === fence[0] &&
          marker[1].length >= fence.length &&
          !stripped.slice(marker[0].length).trim()
        )
          fence = ""
      } else if (marker) {
        fence = marker[1]
      } else if (BLOCK_START.test(stripped)) {
        const start = i + indent
        const [end, closed] = blockEnd(source, start)
        if (i > mdStart) segments.push({ kind: "markdown", text: source.slice(mdStart, i) })
        segments.push({ kind: "ui", source: source.slice(start, end), closed })
        mdStart = i = end
        atLineStart = false
        continue
      } else if (holdPartial && nl < 0 && PARTIAL_START.test(stripped)) {
        if (i > mdStart) segments.push({ kind: "markdown", text: source.slice(mdStart, i) })
        return segments
      }
    }
    i = lineEnd + 1
    atLineStart = true
  }
  if (mdStart < n) segments.push({ kind: "markdown", text: source.slice(mdStart) })
  return segments
}

/** True when ``text`` may contain a ``<ui>`` block (a cheap pre-check before splitting). */
export function mayHaveMarkup(text) {
  return typeof text === "string" && text.includes("<u")
}

// ── text helpers ────────────────────────────────────────────────────────

/** Drop blank edge lines, remove the common leading whitespace (as ``textwrap.dedent``) and trim. */
export function dedent(text) {
  const lines = String(text).split("\n")
  while (lines.length && !lines[0].trim()) lines.shift()
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop()
  let margin = null
  for (const line of lines) {
    if (!line.trim()) continue
    const lead = /^[ \t]*/.exec(line)[0]
    if (margin === null) margin = lead
    else {
      let k = 0
      while (k < margin.length && k < lead.length && margin[k] === lead[k]) k += 1
      margin = margin.slice(0, k)
    }
  }
  const cut = margin ? margin.length : 0
  return lines
    .map((line) => (line.trim() ? line.slice(cut) : ""))
    .join("\n")
    .trim()
}

/**
 * Markup text with ``{expr}`` to spec text with ``{{ expr }}``; ``{{`` and
 * ``}}`` are literal braces. In a streaming block (``closed`` false) an
 * unterminated ``{`` cuts the text there.
 */
export function templateText(text, closed = true) {
  let out = ""
  let i = 0
  while (i < text.length) {
    if (text.startsWith("{{", i)) {
      out += "{"
      i += 2
    } else if (text.startsWith("}}", i)) {
      out += "}"
      i += 2
    } else if (text[i] === "{") {
      let end
      try {
        end = readBraced(text, i)
      } catch (err) {
        if (!(err instanceof Incomplete)) throw err
        if (closed) throw new MarkupError(`${err.message} in text ${JSON.stringify(text)}`)
        return out.trimEnd()
      }
      out += `{{ ${text.slice(i + 1, end - 1).trim()} }}`
      i = end
    } else {
      out += text[i]
      i += 1
    }
  }
  return out
}

function parseExpr(source, where) {
  try {
    return parse(source)
  } catch (err) {
    throw new MarkupError(`bad expression ${JSON.stringify(source)} in ${where}: ${err.message}`)
  }
}

function constantOf(source, where) {
  const node = parseExpr(source, where)
  if (freeNames(node).size) return { ok: false }
  try {
    return { ok: true, value: evaluate(node, {}) }
  } catch (err) {
    throw new MarkupError(`bad expression ${JSON.stringify(source)} in ${where}: ${err.message}`)
  }
}

/** Expression source text for a parsed AST (fully parenthesized), as ``markup.unparse``. */
export function unparse(node) {
  switch (node[0]) {
    case "lit": {
      const value = node[1]
      if (typeof value === "number") return toDisplay(value)
      return JSON.stringify(value)
    }
    case "name":
      return node[1]
    case "list":
      return `[${node[1].map(unparse).join(", ")}]`
    case "obj":
      return `{${node[1].map(([key, value]) => `${IDENT.test(key) ? key : JSON.stringify(key)}: ${unparse(value)}`).join(", ")}}`
    case "call":
      return `${node[1]}(${node[2].map(unparse).join(", ")})`
    case "member":
      return `${unparse(node[1])}.${node[2]}`
    case "index":
      return `${unparse(node[1])}[${unparse(node[2])}]`
    case "neg":
      return `-(${unparse(node[1])})`
    case "pos":
      return `+(${unparse(node[1])})`
    case "not":
      return `not (${unparse(node[1])})`
    case "and":
    case "or":
      return `(${unparse(node[1])} ${node[0]} ${unparse(node[2])})`
    case "cond":
      return `(${unparse(node[1])} ? ${unparse(node[2])} : ${unparse(node[3])})`
    case "bin":
      return `(${unparse(node[2])} ${node[1]} ${unparse(node[3])})`
  }
  throw new MarkupError("bad expression node")
}

// ── conversion ──────────────────────────────────────────────────────────

const isText = (child) => child && typeof child.text === "string" && child.type === undefined
const isDecl = (child) => child && child.decl === true

class Converter {
  constructor(closed) {
    this.closed = closed
  }

  attr(type, attr) {
    let name = attr.name
    if (name === "send" && SEND_TO_MESSAGE.has(type)) name = "message"
    const props = COMPONENTS[type]
    const kind = Object.hasOwn(props, name)
      ? props[name]
      : Object.hasOwn(COMMON_KINDS, name)
        ? COMMON_KINDS[name]
        : "text"
    const where = `<${type} ${attr.name}>`
    if (kind === "children" || kind === "tabs" || kind === "child")
      throw new MarkupError(`${where} is set by child elements`)
    if (attr.kind === "bare") return [name, true]
    if (kind === "expr") return [name, attr.value.trim()]
    if (kind === "setmap") {
      if (attr.kind !== "expr") throw new MarkupError(`${where} needs {key: expression, …}`)
      const node = parseExpr(attr.value, where)
      if (node[0] !== "obj") throw new MarkupError(`${where} needs {key: expression, …}`)
      return [name, Object.fromEntries(node[1].map(([key, value]) => [key, unparse(value)]))]
    }
    if (attr.kind === "str")
      return [name, kind === "text" || kind === "url" ? templateText(attr.value, true) : attr.value]
    const constant = constantOf(attr.value, where)
    return [name, constant.ok ? constant.value : `{{ ${attr.value.trim()} }}`]
  }

  textNode(text) {
    const cleaned = dedent(text)
    return cleaned ? { type: "text", value: templateText(cleaned, this.closed) } : null
  }

  node(name, attrs, children) {
    if (DECLARATIONS.has(name)) throw new MarkupError(`<${name}> belongs directly inside <ui>`)
    if (name === "ui") throw new MarkupError("<ui> cannot be nested")
    const node = { type: name }
    const component = Object.hasOwn(COMPONENTS, name) ? COMPONENTS[name] : null
    for (const attr of attrs) {
      if (!component) {
        node[attr.name] = attr.kind !== "bare" ? attr.value : true
        continue
      }
      const [key, value] = this.attr(name, attr)
      node[key] = value
    }
    if (children.some(isDecl))
      throw new MarkupError("<state> and <let> belong directly inside <ui>")
    const texts = children.filter(isText)
    const nodes = children.filter((c) => !isText(c) && !isDecl(c))
    const target = TEXT_CHILD[name] || RAW_CHILD[name]
    if (target) {
      const content = dedent(texts.map((t) => t.text).join(""))
      if (content) node[target] = RAW_CHILD[name] ? content : templateText(content, this.closed)
      return node
    }
    const kinds = new Set(Object.values(component || {}))
    const items = this.items(children)
    if (kinds.has("children") || kinds.has("tabs") || !component) {
      if (items.length || component) node.children = items
    } else if (kinds.has("child")) {
      if (items.length === 1) node.child = items[0]
      else if (items.length) node.child = { type: "stack", gap: "sm", children: items }
    } else if (nodes.length || texts.some((t) => dedent(t.text))) {
      const lists = Object.keys(component).filter((key) => component[key] === "list")
      if (lists.length) {
        const attrs = lists.map((key) => `${key}={[…]}`).join(" and ")
        throw new MarkupError(`<${name}> takes no children; set ${attrs} as attributes`)
      }
      throw new MarkupError(`<${name}> takes no children`)
    }
    return node
  }

  items(children) {
    const out = []
    for (const child of children) {
      if (isText(child)) {
        const node = this.textNode(child.text)
        if (node) out.push(node)
      } else if (!isDecl(child)) out.push(child)
    }
    return out
  }

  root(attrs, children) {
    const spec = {}
    for (const attr of attrs) {
      if (attr.name === "title" && attr.kind === "str") spec.title = templateText(attr.value, true)
      else if (attr.kind === "str") spec[attr.name] = attr.value
      else if (attr.kind === "bare") spec[attr.name] = true
      else {
        const constant = constantOf(attr.value, `<ui ${attr.name}>`)
        spec[attr.name] = constant.ok ? constant.value : attr.value
      }
    }
    const state = {}
    const computed = {}
    const body = []
    for (const child of children) {
      if (isDecl(child)) this.declare(child.name, child.attrs, state, computed)
      else body.push(child)
    }
    if (Object.keys(state).length) spec.state = state
    if (Object.keys(computed).length) spec.computed = computed
    const items = this.items(body)
    if (items.length === 1) spec.root = items[0]
    else if (items.length) spec.root = { type: "stack", gap: "md", children: items }
    return spec
  }

  declare(name, attrs, state, computed) {
    for (const attr of attrs) {
      const where = `<${name} ${attr.name}>`
      if (name === "let") {
        computed[attr.name] = attr.kind !== "bare" ? attr.value.trim() : "true"
        continue
      }
      if (attr.kind === "str") state[attr.name] = attr.value
      else if (attr.kind === "bare") state[attr.name] = true
      else {
        const constant = constantOf(attr.value, where)
        if (!constant.ok) throw new MarkupError(`${where}: a state value must be a constant`)
        state[attr.name] = constant.value
      }
    }
  }
}

function build(converter, name, attrs, children) {
  if (DECLARATIONS.has(name)) return { decl: true, name, attrs }
  return converter.node(name, attrs, children)
}

/**
 * Convert one ``<ui>`` block to a kt.ui spec; throws ``MarkupError``.
 *
 * A block whose source does not end with ``</ui>`` is still streaming: open
 * elements are closed and an unfinished last tag is dropped. With
 * ``dropOpenDiagram`` an unclosed ``<diagram>`` is left out too, so a
 * half-written diagram is never drawn. With ``holdOpenHtml`` an unclosed
 * ``<html>`` becomes an empty one (drawn as a placeholder) for the same reason.
 */
export function convertMarkup(source, { dropOpenDiagram = false, holdOpenHtml = false } = {}) {
  const text = String(source ?? "")
  const closed = text.trimEnd().endsWith("</ui>")
  const lexed = lex(text)
  if (lexed.incomplete && closed) throw new MarkupError(lexed.incomplete)
  const converter = new Converter(closed)
  const stack = []
  let result = null
  for (const token of lexed.tokens) {
    if (result !== null) {
      if (token.kind === "text" && !token.text.trim()) continue
      throw new MarkupError("content after </ui>")
    }
    if (!stack.length) {
      if (token.kind === "text" && !token.text.trim()) continue
      if (token.kind !== "open" || token.name !== "ui")
        throw new MarkupError("a block starts with <ui>")
    }
    if (token.kind === "text") {
      stack[stack.length - 1].children.push({ text: token.text })
    } else if (token.kind === "open") {
      if (token.selfClosing) {
        if (!stack.length) result = converter.root(token.attrs, [])
        else stack[stack.length - 1].children.push(build(converter, token.name, token.attrs, []))
      } else stack.push({ name: token.name, attrs: token.attrs, children: [] })
    } else {
      const top = stack[stack.length - 1]
      if (top.name !== token.name)
        throw new MarkupError(`mismatched </${token.name}>: <${top.name}> is still open`)
      stack.pop()
      if (stack.length)
        stack[stack.length - 1].children.push(build(converter, top.name, top.attrs, top.children))
      else result = converter.root(top.attrs, top.children)
    }
  }
  if (result !== null) return result
  if (!stack.length) {
    if (!closed && !lexed.tokens.some((t) => t.kind !== "text" || t.text.trim())) return {}
    throw new MarkupError("a block starts with <ui>")
  }
  if (closed) throw new MarkupError(`<${stack[stack.length - 1].name}> is never closed`)
  while (stack.length > 1) {
    const top = stack.pop()
    if (dropOpenDiagram && top.name === "diagram") continue
    const children = holdOpenHtml && top.name === "html" ? [] : top.children
    const node = build(converter, top.name, top.attrs, children)
    if (holdOpenHtml && top.name === "html") node.source = ""
    stack[stack.length - 1].children.push(node)
  }
  return converter.root(stack[0].attrs, stack[0].children)
}
