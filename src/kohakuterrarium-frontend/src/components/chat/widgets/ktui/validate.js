/**
 * Structural check of a kt.ui spec in the browser, used before an inline
 * ``<ui>`` block is drawn.
 *
 * A subset of ``kohakuterrarium/builtins/kt_ui/spec.py``: known components and
 * props, required props, enum choices, ``bind`` targets and expression syntax
 * with known names. Returns ``[path, message]`` pairs; empty means drawable.
 */

import { freeNames, parse } from "./expr.js"

const GAP = ["none", "sm", "md", "lg"]
const TONE = ["neutral", "info", "success", "warning", "danger"]
const INPUT = { bind: "var!", label: "text" }
export const ICONS = [
  "check",
  "close",
  "warning",
  "info",
  "error",
  "help",
  "star",
  "heart",
  "time",
  "calendar",
  "user",
  "users",
  "settings",
  "search",
  "link",
  "download",
  "upload",
  "document",
  "folder",
  "code",
  "chart",
  "lightning",
  "rocket",
  "idea",
  "lock",
  "flag",
  "location",
  "email",
  "chat",
  "money",
]
export const MAX_HTML_BYTES = 48 * 1024
const WHOLE_TEMPLATE = /^\s*\{\{(?:(?!\}\})[\s\S])*\}\}\s*$/
const OUTWARD_URL = /^https?:\/\/[^\s<>"']+$/i
const SAFE_SRC = (v) =>
  typeof v === "string" &&
  (v.startsWith("data:image/") || (v.startsWith("/") && !v.startsWith("//")))

/** type → {prop: kind}; a ``!`` suffix marks a required prop, ``|a|b`` lists enum choices. */
const COMPONENTS = {
  stack: {
    direction: "enum|column|row",
    gap: `enum|${GAP.join("|")}`,
    wrap: "bool",
    align: "enum|start|center|end|stretch",
    children: "children!",
  },
  grid: { columns: "num", gap: `enum|${GAP.join("|")}`, children: "children!" },
  card: { title: "text", subtitle: "text", children: "children!" },
  section: { title: "text", children: "children!" },
  tabs: { children: "tabs!" },
  tab: { label: "text!", children: "children" },
  divider: {},
  accordion: { title: "text!", open: "bool", children: "children!" },
  text: { value: "text!", size: "enum|sm|md|lg", muted: "bool" },
  heading: { value: "text!", level: "num" },
  badge: { value: "text!", tone: `enum|${TONE.join("|")}` },
  stat: {
    label: "text",
    value: "text!",
    hint: "text",
    tone: `enum|${TONE.join("|")}`,
    trend: "value",
  },
  callout: { value: "text!", title: "text", tone: "enum|info|tip|success|warning|danger" },
  quote: { value: "text!", cite: "text" },
  steps: { items: "value!", current: "num" },
  timeline: { items: "value!" },
  progress: {
    value: "num!",
    max: "num",
    label: "text",
    tone: `enum|${TONE.join("|")}`,
    style: "enum|bar|ring",
  },
  tree: { items: "value!", open: "bool" },
  kv: { fields: "value!" },
  list: { items: "value!", ordered: "bool" },
  table: { columns: "value!", rows: "value!", sortable: "bool", filter: "bool" },
  image: { src: "text!", alt: "text", width: "num" },
  code: { value: "text!", lang: "text" },
  chart: {
    kind: "enum!|bar|stacked|line|area|pie|scatter|radar|heatmap|gauge",
    labels: "value",
    series: "value!",
    title: "text",
    height: "num",
    min: "num",
    max: "num",
  },
  diagram: { source: "text!", title: "text" },
  html: { source: "literal!", title: "text", height: "num" },
  math: { value: "literal!", display: "bool" },
  link: { href: "url!", label: "text", style: "enum|link|button" },
  icon: {
    name: `enum!|${ICONS.join("|")}`,
    label: "text",
    size: "enum|sm|md|lg",
    tone: `enum|${TONE.join("|")}`,
  },
  gallery: { items: "value!", columns: "num" },
  compare: { items: "value!" },
  copy: { value: "text!", label: "text" },
  timer: { mode: "enum|countdown|stopwatch", seconds: "num", label: "text", autostart: "bool" },
  quiz: { question: "text!", options: "value!", answer: "literal!", explain: "text" },
  number: { ...INPUT, min: "num", max: "num", step: "num" },
  slider: { ...INPUT, min: "num!", max: "num!", step: "num" },
  text_input: { ...INPUT, placeholder: "text", multiline: "bool" },
  select: { ...INPUT, options: "value!" },
  radio: { ...INPUT, options: "value!" },
  toggle: { ...INPUT },
  checkbox: { ...INPUT },
  date: { ...INPUT },
  rating: { ...INPUT, max: "num" },
  button: {
    label: "text!",
    style: "enum|primary|secondary|danger",
    set: "setmap",
    send: "text",
    message: "text",
    data: "value",
    disabled: "bool",
  },
  form: { children: "children!", send: "text", message: "text", submit: "text", data: "value" },
  each: { items: "expr!", as: "text", child: "child!" },
}
const COMMON = { type: "text", visible: "bool", alt: "text" }
const TOP_LEVEL = new Set(["version", "title", "layout", "state", "computed", "root"])
const TEMPLATE = /\{\{([\s\S]*?)\}\}/g
const MAX_ERRORS = 20

function kindOf(spec) {
  const [head, ...choices] = spec.split("|")
  return { kind: head.replace(/!$/, ""), required: head.endsWith("!"), choices }
}

class Checker {
  constructor(state, computed) {
    this.errors = []
    this.state = new Set(Object.keys(state))
    this.scope = new Set([...this.state, ...Object.keys(computed)])
  }
  error(path, message) {
    if (this.errors.length < MAX_ERRORS) this.errors.push([path, message])
  }
  expression(path, text, scope) {
    try {
      for (const name of freeNames(parse(text)))
        if (!scope.has(name)) this.error(path, `unknown name '${name}'`)
    } catch (err) {
      this.error(path, `bad expression ${JSON.stringify(text)}: ${err.message}`)
    }
  }
  templates(path, value, scope) {
    if (typeof value === "string") {
      for (const match of value.matchAll(TEMPLATE)) this.expression(path, match[1], scope)
    } else if (Array.isArray(value))
      value.forEach((v, i) => this.templates(`${path}[${i}]`, v, scope))
    else if (value && typeof value === "object")
      for (const [k, v] of Object.entries(value)) this.templates(`${path}.${k}`, v, scope)
  }
  node(path, node, scope, depth = 0) {
    if (depth > 24) return this.error(path, "nested too deeply")
    if (!node || typeof node !== "object" || Array.isArray(node))
      return this.error(path, "must be a component")
    const props = COMPONENTS[node.type]
    if (!props) return this.error(`${path}.type`, `unknown component '${node.type}'`)
    const all = { ...COMMON, ...props }
    for (const key of Object.keys(node))
      if (!(key in all)) this.error(`${path}.${key}`, `unknown prop for ${node.type}`)
    for (const [key, spec] of Object.entries(all)) {
      const { kind, required, choices } = kindOf(spec)
      if (!(key in node)) {
        if (required) this.error(`${path}.${key}`, "is required")
        continue
      }
      if (key === "type") continue
      this.prop(`${path}.${key}`, node, key, kind, choices, scope, depth)
    }
    for (const [key, message] of nodeProblems(node)) this.error(`${path}.${key}`, message)
  }
  prop(path, node, key, kind, choices, scope, depth) {
    const value = node[key]
    switch (kind) {
      case "children":
      case "tabs":
        if (!Array.isArray(value)) return this.error(path, "must be a list of components")
        value.forEach((child, i) => {
          if (kind === "tabs" && child?.type !== "tab")
            this.error(`${path}[${i}]`, "tabs hold only tab nodes")
          else this.node(`${path}[${i}]`, child, scope, depth + 1)
        })
        return
      case "child": {
        const name = typeof node.as === "string" ? node.as : "item"
        return this.node(path, value, new Set([...scope, name, "index"]), depth + 1)
      }
      case "enum":
        if (!choices.includes(value)) this.error(path, `must be one of ${choices.join(", ")}`)
        return
      case "var":
        if (typeof value !== "string" || !this.state.has(value))
          this.error(path, `'${value}' is not a key of state`)
        return
      case "expr":
        if (typeof value !== "string") return this.error(path, "must be an expression")
        return this.expression(path, value, scope)
      case "setmap":
        if (!value || typeof value !== "object")
          return this.error(path, "must map state keys to expressions")
        for (const [k, expr] of Object.entries(value)) {
          if (!this.state.has(k)) this.error(`${path}.${k}`, `'${k}' is not a key of state`)
          if (typeof expr === "string") this.expression(`${path}.${k}`, expr, scope)
          else this.error(`${path}.${k}`, "must be an expression")
        }
        return
      case "text":
        if (typeof value !== "string") return this.error(path, "must be a string")
        return this.templates(path, value, scope)
      case "literal":
        if (typeof value !== "string") this.error(path, "must be a string")
        return
      case "url": {
        if (typeof value !== "string") return this.error(path, "must be a string")
        this.templates(path, value, scope)
        if (WHOLE_TEMPLATE.test(value)) return
        const fixed = value.split("{{", 1)[0]
        const ok = fixed === value ? OUTWARD_URL.test(value) : /^https?:\/\//i.test(fixed)
        if (!ok) this.error(path, "must be an http:// or https:// link")
        return
      }
      case "bool":
        if (typeof value !== "boolean" && typeof value !== "string")
          this.error(path, "must be true, false or a template")
        return this.templates(path, value, scope)
      case "num":
        if (typeof value !== "number" && typeof value !== "string")
          this.error(path, "must be a number or a template")
        return this.templates(path, value, scope)
      default:
        return this.templates(path, value, scope)
    }
  }
}

function optionValues(options) {
  return Array.isArray(options)
    ? options.map((o) => (o && typeof o === "object" ? o.value : o))
    : null
}

/** ``[prop, message]`` problems that span several props of one node (``shapes.py``). */
function nodeProblems(node) {
  const out = []
  switch (node.type) {
    case "form":
      if (!("send" in node) && !("message" in node)) out.push(["send", "is required"])
      break
    case "html":
      if (
        typeof node.source === "string" &&
        new TextEncoder().encode(node.source).length > MAX_HTML_BYTES
      )
        out.push(["source", "is too large"])
      break
    case "quiz": {
      const values = optionValues(node.options)
      if (values && typeof node.answer === "string" && !values.map(String).includes(node.answer))
        out.push(["answer", "must be the value of one of the options"])
      break
    }
    case "timer":
      if ((node.mode ?? "countdown") === "countdown" && !("seconds" in node))
        out.push(["seconds", "a countdown needs 'seconds'"])
      break
    case "gallery":
      if (Array.isArray(node.items))
        node.items.forEach((item, i) => {
          const src = item && typeof item === "object" ? item.src : item
          if (typeof src !== "string" || (!src.includes("{{") && !SAFE_SRC(src)))
            out.push([`items[${i}]`, "a gallery src is a data:image/ URL or a same-origin path"])
        })
      break
    case "compare":
      if (Array.isArray(node.items) && (node.items.length < 1 || node.items.length > 6))
        out.push(["items", "compare holds 1-6 items"])
      break
  }
  return out
}

/** ``[path, message]`` problems of a spec; an empty list means it can be drawn. */
export function validateSpec(spec) {
  if (!spec || typeof spec !== "object") return [["spec", "must be an object"]]
  const errors = []
  for (const key of Object.keys(spec))
    if (!TOP_LEVEL.has(key)) errors.push([key, "unknown top-level key"])
  if (spec.layout !== undefined && spec.layout !== "inline" && spec.layout !== "page")
    errors.push(["layout", "must be 'inline' or 'page'"])
  const state = spec.state && typeof spec.state === "object" ? spec.state : {}
  const computed = spec.computed && typeof spec.computed === "object" ? spec.computed : {}
  for (const name of Object.keys(state))
    if (name.startsWith("__"))
      errors.push([`state.${name}`, "names starting with '__' are reserved"])
  const checker = new Checker(state, computed)
  for (const [name, expr] of Object.entries(computed)) {
    if (name in state) checker.error(`computed.${name}`, "is already a key of state")
    if (typeof expr === "string") checker.expression(`computed.${name}`, expr, checker.scope)
    else checker.error(`computed.${name}`, "must be an expression")
  }
  if (typeof spec.title === "string") checker.templates("title", spec.title, checker.scope)
  if (spec.root === undefined) checker.error("root", "is required")
  else checker.node("root", spec.root, checker.scope)
  return [...errors, ...checker.errors].slice(0, MAX_ERRORS)
}
