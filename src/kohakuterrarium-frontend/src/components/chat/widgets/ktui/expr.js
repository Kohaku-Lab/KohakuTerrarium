/**
 * The kt.ui expression language: a small pure language, parsed without eval.
 *
 * Twin of ``kohakuterrarium/builtins/kt_ui/expr.py``; both run the shared
 * conformance file ``tests/fixtures/kt_ui_expressions.json``. Values are JSON
 * values; errors are ``ExprError`` with a ``code`` shared with the backend
 * (syntax, unknown_name, unknown_function, type, zero_division, domain,
 * arity, limit).
 */

export const MAX_EXPR_CHARS = 2048
export const MAX_STEPS = 10000
export const MAX_DEPTH = 64
export const MAX_RANGE = 1000

export const KEYWORDS = new Set(["true", "false", "null", "and", "or", "not"])
const TWO_CHAR_OPS = new Set(["**", "==", "!=", "<=", ">="])
const ONE_CHAR_OPS = "+-*/%<>?:()[],.{}"
const ESCAPES = { n: "\n", t: "\t", r: "\r", "\\": "\\", '"': '"', "'": "'" }
const CMP_OPS = new Set(["==", "!=", "<", "<=", ">", ">="])
const NUMBER_TEXT = /^\s*-?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*$/

export class ExprError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}

const isDigit = (c) => c >= "0" && c <= "9"
const isAlpha = (c) => (c >= "a" && c <= "z") || (c >= "A" && c <= "Z")

function tokenize(text) {
  const tokens = []
  let i = 0
  const n = text.length
  while (i < n) {
    const ch = text[i]
    if (ch === " " || ch === "\t" || ch === "\r" || ch === "\n") {
      i += 1
    } else if (isDigit(ch) || (ch === "." && i + 1 < n && isDigit(text[i + 1]))) {
      let j = i
      while (j < n && isDigit(text[j])) j += 1
      if (j < n && text[j] === "." && j + 1 < n && isDigit(text[j + 1])) {
        j += 1
        while (j < n && isDigit(text[j])) j += 1
      }
      if (j < n && (text[j] === "e" || text[j] === "E")) {
        let k = j + 1
        if (k < n && (text[k] === "+" || text[k] === "-")) k += 1
        if (k < n && isDigit(text[k])) {
          j = k
          while (j < n && isDigit(text[j])) j += 1
        }
      }
      tokens.push(["num", Number(text.slice(i, j))])
      i = j
    } else if (ch === '"' || ch === "'") {
      let j = i + 1
      let out = ""
      for (;;) {
        if (j >= n) throw new ExprError("syntax", "unterminated string")
        const c = text[j]
        if (c === ch) break
        if (c === "\\") {
          if (j + 1 >= n || !(text[j + 1] in ESCAPES))
            throw new ExprError("syntax", "bad escape in string")
          out += ESCAPES[text[j + 1]]
          j += 2
          continue
        }
        out += c
        j += 1
      }
      tokens.push(["str", out])
      i = j + 1
    } else if (isAlpha(ch) || ch === "_") {
      let j = i
      while (j < n && (isAlpha(text[j]) || isDigit(text[j]) || text[j] === "_")) j += 1
      const word = text.slice(i, j)
      tokens.push([KEYWORDS.has(word) ? "kw" : "name", word])
      i = j
    } else if (TWO_CHAR_OPS.has(text.slice(i, i + 2))) {
      tokens.push(["op", text.slice(i, i + 2)])
      i += 2
    } else if (ONE_CHAR_OPS.includes(ch)) {
      tokens.push(["op", ch])
      i += 1
    } else {
      throw new ExprError("syntax", `unexpected character ${JSON.stringify(ch)}`)
    }
  }
  tokens.push(["end", null])
  return tokens
}

class Parser {
  constructor(tokens) {
    this.tokens = tokens
    this.pos = 0
    this.depth = 0
  }
  peek() {
    return this.tokens[this.pos]
  }
  take() {
    return this.tokens[this.pos++]
  }
  at(kind, value) {
    const [tk, tv] = this.peek()
    return tk === kind && (value === undefined || tv === value)
  }
  expect(kind, value) {
    if (!this.at(kind, value)) throw new ExprError("syntax", `expected ${value || kind}`)
    return this.take()
  }
  enter() {
    this.depth += 1
    if (this.depth > MAX_DEPTH) throw new ExprError("limit", "expression nests too deeply")
  }
  ternary() {
    this.enter()
    let cond = this.or()
    if (this.at("op", "?")) {
      this.take()
      const yes = this.ternary()
      this.expect("op", ":")
      const no = this.ternary()
      cond = ["cond", cond, yes, no]
    }
    this.depth -= 1
    return cond
  }
  or() {
    let left = this.and()
    while (this.at("kw", "or")) {
      this.take()
      left = ["or", left, this.and()]
    }
    return left
  }
  and() {
    let left = this.not()
    while (this.at("kw", "and")) {
      this.take()
      left = ["and", left, this.not()]
    }
    return left
  }
  not() {
    if (this.at("kw", "not")) {
      this.take()
      this.enter()
      const node = ["not", this.not()]
      this.depth -= 1
      return node
    }
    return this.cmp()
  }
  cmp() {
    let left = this.add()
    const [tk, tv] = this.peek()
    if (tk === "op" && CMP_OPS.has(tv)) {
      this.take()
      left = ["bin", tv, left, this.add()]
    }
    return left
  }
  add() {
    let left = this.mul()
    while (this.peek()[0] === "op" && (this.peek()[1] === "+" || this.peek()[1] === "-")) {
      const op = this.take()[1]
      left = ["bin", op, left, this.mul()]
    }
    return left
  }
  mul() {
    let left = this.unary()
    while (this.peek()[0] === "op" && ["*", "/", "%"].includes(this.peek()[1])) {
      const op = this.take()[1]
      left = ["bin", op, left, this.unary()]
    }
    return left
  }
  unary() {
    if (this.peek()[0] === "op" && (this.peek()[1] === "-" || this.peek()[1] === "+")) {
      const op = this.take()[1]
      this.enter()
      const node = [op === "-" ? "neg" : "pos", this.unary()]
      this.depth -= 1
      return node
    }
    return this.power()
  }
  power() {
    let base = this.postfix()
    if (this.at("op", "**")) {
      this.take()
      this.enter()
      base = ["bin", "**", base, this.unary()]
      this.depth -= 1
    }
    return base
  }
  postfix() {
    let node = this.primary()
    for (;;) {
      if (this.at("op", ".")) {
        this.take()
        const [tk, tv] = this.take()
        if (tk !== "name" && tk !== "kw") throw new ExprError("syntax", "expected a key after '.'")
        node = ["member", node, tv]
      } else if (this.at("op", "[")) {
        this.take()
        const index = this.ternary()
        this.expect("op", "]")
        node = ["index", node, index]
      } else {
        return node
      }
    }
  }
  primary() {
    const [tk, tv] = this.take()
    if (tk === "num" || tk === "str") return ["lit", tv]
    if (tk === "kw" && (tv === "true" || tv === "false" || tv === "null")) {
      return ["lit", tv === "true" ? true : tv === "false" ? false : null]
    }
    if (tk === "name") {
      if (this.at("op", "(")) {
        if (!Object.hasOwn(FUNCTIONS, tv))
          throw new ExprError("unknown_function", `unknown function ${tv}`)
        this.take()
        return ["call", tv, this.items(")")]
      }
      return ["name", tv]
    }
    if (tk === "op" && tv === "(") {
      const inner = this.ternary()
      this.expect("op", ")")
      return inner
    }
    if (tk === "op" && tv === "[") return ["list", this.items("]")]
    if (tk === "op" && tv === "{") return ["obj", this.entries()]
    throw new ExprError("syntax", "unexpected token")
  }
  entries() {
    const entries = []
    const seen = new Set()
    while (!this.at("op", "}")) {
      const [tk, tv] = this.take()
      if (tk !== "name" && tk !== "str") throw new ExprError("syntax", "expected an object key")
      if (seen.has(tv)) throw new ExprError("syntax", `duplicate key ${JSON.stringify(tv)}`)
      seen.add(tv)
      this.expect("op", ":")
      entries.push([tv, this.ternary()])
      if (this.at("op", ",")) this.take()
      else if (!this.at("op", "}")) throw new ExprError("syntax", "expected ',' or '}'")
    }
    this.take()
    return entries
  }
  items(close) {
    const items = []
    while (!this.at("op", close)) {
      items.push(this.ternary())
      if (this.at("op", ",")) this.take()
      else if (!this.at("op", close)) throw new ExprError("syntax", `expected ',' or '${close}'`)
    }
    this.take()
    return items
  }
}

/** Parse an expression into an AST; throws ``ExprError`` on bad input. */
export function parse(text) {
  if (typeof text !== "string") throw new ExprError("syntax", "expression must be a string")
  if (text.length > MAX_EXPR_CHARS)
    throw new ExprError("limit", `expression longer than ${MAX_EXPR_CHARS} chars`)
  const parser = new Parser(tokenize(text))
  const node = parser.ternary()
  if (!parser.at("end")) throw new ExprError("syntax", "unexpected token after the expression")
  return node
}

// ── values ──────────────────────────────────────────────────────────────

const isNum = (v) => typeof v === "number"
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v)

function typeName(v) {
  if (v === null || v === undefined) return "null"
  if (typeof v === "boolean") return "boolean"
  if (typeof v === "number") return "number"
  if (typeof v === "string") return "string"
  if (Array.isArray(v)) return "list"
  return "object"
}

function finite(v) {
  if (!Number.isFinite(v)) throw new ExprError("domain", "result is not a finite number")
  return v
}

function jsonText(value) {
  if (isNum(value)) return String(value)
  if (Array.isArray(value)) return "[" + value.map(jsonText).join(",") + "]"
  if (isObj(value)) {
    return (
      "{" +
      Object.entries(value)
        .map(([k, v]) => JSON.stringify(k) + ":" + jsonText(v))
        .join(",") +
      "}"
    )
  }
  return JSON.stringify(value === undefined ? null : value)
}

/** Text shown for a value inside a template. */
export function toDisplay(value) {
  if (value === null || value === undefined) return ""
  if (typeof value === "boolean") return value ? "true" : "false"
  if (isNum(value)) return Object.is(value, -0) ? "0" : String(value)
  if (typeof value === "string") return value
  return jsonText(value)
}

/** Falsy values: false, null, 0, "", [] and {}. */
export function truthy(value) {
  if (value === null || value === undefined || value === false) return false
  if (isNum(value)) return value !== 0
  if (typeof value === "string" || Array.isArray(value)) return value.length > 0
  if (isObj(value)) return Object.keys(value).length > 0
  return true
}

function equal(a, b) {
  if (typeName(a) !== typeName(b)) return false
  if (Array.isArray(a)) return a.length === b.length && a.every((x, i) => equal(x, b[i]))
  if (isObj(a)) {
    const ka = Object.keys(a)
    const kb = Object.keys(b)
    if (ka.length !== kb.length || !ka.every((k) => Object.hasOwn(b, k))) return false
    return ka.every((k) => equal(a[k], b[k]))
  }
  return (a ?? null) === (b ?? null)
}

// ── evaluation ──────────────────────────────────────────────────────────

class Evaluator {
  constructor(scope, maxSteps) {
    this.scope = scope
    this.steps = 0
    this.maxSteps = maxSteps
  }
  step(count = 1) {
    this.steps += count
    if (this.steps > this.maxSteps) throw new ExprError("limit", "expression takes too many steps")
  }
  run(node) {
    this.step()
    switch (node[0]) {
      case "lit":
        return node[1]
      case "name":
        if (!Object.hasOwn(this.scope, node[1]))
          throw new ExprError("unknown_name", `unknown name ${node[1]}`)
        return this.scope[node[1]] ?? null
      case "list":
        return node[1].map((item) => this.run(item))
      case "obj": {
        const out = {}
        for (const [key, item] of node[1]) {
          Object.defineProperty(out, key, {
            value: this.run(item),
            enumerable: true,
            writable: true,
            configurable: true,
          })
        }
        return out
      }
      case "neg":
      case "pos": {
        const value = this.run(node[1])
        if (!isNum(value)) throw new ExprError("type", `expected number, got ${typeName(value)}`)
        return node[0] === "neg" ? -value : value
      }
      case "not":
        return !truthy(this.run(node[1]))
      case "and": {
        const left = this.run(node[1])
        return truthy(left) ? this.run(node[2]) : left
      }
      case "or": {
        const left = this.run(node[1])
        return truthy(left) ? left : this.run(node[2])
      }
      case "cond":
        return truthy(this.run(node[1])) ? this.run(node[2]) : this.run(node[3])
      case "member":
        return member(this.run(node[1]), node[2])
      case "index":
        return index(this.run(node[1]), this.run(node[2]))
      case "call": {
        const args = node[2].map((arg) => this.run(arg))
        return FUNCTIONS[node[1]](this, ...args)
      }
      case "bin":
        return binary(node[1], this.run(node[2]), this.run(node[3]))
    }
    throw new ExprError("syntax", "bad expression node")
  }
}

function member(obj, key) {
  if (obj === null || obj === undefined) return null
  if (isObj(obj)) return Object.hasOwn(obj, key) ? obj[key] : null
  throw new ExprError("type", `cannot read .${key} of ${typeName(obj)}`)
}

function index(obj, idx) {
  if (obj === null || obj === undefined) return null
  if (Array.isArray(obj) || typeof obj === "string") {
    if (!isNum(idx) || idx !== Math.floor(idx))
      throw new ExprError("type", "list index must be a whole number")
    let i = idx
    if (i < 0) i += obj.length
    return i >= 0 && i < obj.length ? obj[i] : null
  }
  if (isObj(obj)) {
    if (typeof idx !== "string") throw new ExprError("type", "object key must be a string")
    return Object.hasOwn(obj, idx) ? obj[idx] : null
  }
  throw new ExprError("type", `cannot index ${typeName(obj)}`)
}

function power(a, b) {
  if (a === 0 && b < 0) throw new ExprError("zero_division", "zero to a negative power")
  if (a < 0 && b !== Math.floor(b))
    throw new ExprError("domain", "negative number to a fractional power")
  return finite(Math.pow(a, b))
}

function binary(op, a, b) {
  switch (op) {
    case "==":
      return equal(a, b)
    case "!=":
      return !equal(a, b)
    case "<":
    case "<=":
    case ">":
    case ">=": {
      const ok = (isNum(a) && isNum(b)) || (typeof a === "string" && typeof b === "string")
      if (!ok) throw new ExprError("type", `cannot compare ${typeName(a)} and ${typeName(b)}`)
      if (op === "<") return a < b
      if (op === "<=") return a <= b
      if (op === ">") return a > b
      return a >= b
    }
    case "+": {
      if (isNum(a) && isNum(b)) return finite(a + b)
      if (Array.isArray(a) && Array.isArray(b)) return [...a, ...b]
      if (
        (typeof a === "string" || typeof b === "string") &&
        [a, b].every((v) => typeof v === "string" || isNum(v))
      ) {
        return toDisplay(a) + toDisplay(b)
      }
      throw new ExprError("type", `cannot add ${typeName(a)} and ${typeName(b)}`)
    }
  }
  if (!(isNum(a) && isNum(b))) throw new ExprError("type", `'${op}' needs numbers`)
  switch (op) {
    case "-":
      return finite(a - b)
    case "*":
      return finite(a * b)
    case "/":
      if (b === 0) throw new ExprError("zero_division", "division by zero")
      return finite(a / b)
    case "%":
      if (b === 0) throw new ExprError("zero_division", "modulo by zero")
      return finite(a - b * Math.floor(a / b))
  }
  return power(a, b)
}

// ── functions ───────────────────────────────────────────────────────────

function numArg(name, v) {
  if (!isNum(v)) throw new ExprError("type", `${name}() expects a number, got ${typeName(v)}`)
  return v
}

function intArg(name, v) {
  numArg(name, v)
  if (v !== Math.floor(v)) throw new ExprError("type", `${name}() expects a whole number`)
  return v
}

function listArg(name, v) {
  if (!Array.isArray(v)) throw new ExprError("type", `${name}() expects a list, got ${typeName(v)}`)
  return v
}

function arity(name, args, low, high) {
  if (args.length < low || args.length > high)
    throw new ExprError("arity", `${name}() takes ${low}-${high} arguments`)
}

function numbers(name, ev, args) {
  const values = args.length === 1 && Array.isArray(args[0]) ? args[0] : args
  ev.step(values.length)
  return values.map((v) => numArg(name, v))
}

function fsum(values) {
  // Neumaier compensated summation, close to Python's math.fsum for UI-sized lists.
  let sum = 0
  let comp = 0
  for (const v of values) {
    const t = sum + v
    comp += Math.abs(sum) >= Math.abs(v) ? sum - t + v : v - t + sum
    sum = t
  }
  return sum + comp
}

function groupThousands(text) {
  const sign = text.startsWith("-") ? "-" : ""
  const body = sign ? text.slice(1) : text
  const [whole, frac] = body.split(".")
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
  return sign + grouped + (frac !== undefined ? "." + frac : "")
}

function fmt(ev, ...args) {
  arity("fmt", args, 1, 2)
  const x = numArg("fmt", args[0])
  const digits = args.length > 1 ? intArg("fmt", args[1]) : 0
  if (digits < 0 || digits > 10) throw new ExprError("domain", "fmt() digits must be 0-10")
  if (Math.abs(x) >= 1e21) return String(x)
  return groupThousands((x === 0 ? 0 : x).toFixed(digits))
}

const unary =
  (name, fn) =>
  (ev, ...args) => {
    arity(name, args, 1, 1)
    return finite(fn(numArg(name, args[0])))
  }

const minMax =
  (name, pick) =>
  (ev, ...args) => {
    if (!args.length) throw new ExprError("arity", `${name}() needs at least one argument`)
    const values = numbers(name, ev, args)
    if (!values.length) throw new ExprError("domain", `${name}() of an empty list`)
    return pick(...values)
  }

export const FUNCTIONS = {
  abs: unary("abs", Math.abs),
  avg(ev, ...args) {
    arity("avg", args, 1, 1)
    const values = numbers("avg", ev, [listArg("avg", args[0])])
    return values.length ? finite(fsum(values) / values.length) : null
  },
  ceil: unary("ceil", Math.ceil),
  clamp(ev, ...args) {
    arity("clamp", args, 3, 3)
    const [x, low, high] = args.map((a) => numArg("clamp", a))
    return Math.min(Math.max(x, low), high)
  },
  floor: unary("floor", Math.floor),
  fmt,
  join(ev, ...args) {
    arity("join", args, 1, 2)
    const items = listArg("join", args[0])
    const sep = args.length > 1 ? args[1] : ", "
    if (typeof sep !== "string") throw new ExprError("type", "join() separator must be a string")
    ev.step(items.length)
    return items.map(toDisplay).join(sep)
  },
  keys(ev, ...args) {
    arity("keys", args, 1, 1)
    if (!isObj(args[0])) throw new ExprError("type", `keys() of ${typeName(args[0])}`)
    return Object.keys(args[0])
  },
  len(ev, ...args) {
    arity("len", args, 1, 1)
    const v = args[0]
    if (typeof v === "string" || Array.isArray(v)) return v.length
    if (isObj(v)) return Object.keys(v).length
    throw new ExprError("type", `len() of ${typeName(v)}`)
  },
  max: minMax("max", Math.max),
  min: minMax("min", Math.min),
  num(ev, ...args) {
    arity("num", args, 1, 1)
    const v = args[0]
    if (isNum(v)) return v
    if (typeof v === "string" && NUMBER_TEXT.test(v)) return finite(Number(v.trim()))
    return null
  },
  pct(ev, ...args) {
    arity("pct", args, 1, 2)
    const scaled = finite(numArg("pct", args[0]) * 100)
    return fmt(ev, scaled, ...args.slice(1)) + "%"
  },
  pow(ev, ...args) {
    arity("pow", args, 2, 2)
    return power(numArg("pow", args[0]), numArg("pow", args[1]))
  },
  range(ev, ...args) {
    arity("range", args, 1, 3)
    const ints = args.map((a) => intArg("range", a))
    let [start, stop, step] = ints.length === 1 ? [0, ints[0], 1] : [ints[0], ints[1], 1]
    if (ints.length === 3) step = ints[2]
    if (step === 0) throw new ExprError("domain", "range() step must not be 0")
    const count = Math.max(0, Math.ceil((stop - start) / step))
    if (count > MAX_RANGE) throw new ExprError("limit", `range() longer than ${MAX_RANGE}`)
    ev.step(count)
    return Array.from({ length: count }, (_, i) => start + i * step)
  },
  round(ev, ...args) {
    arity("round", args, 1, 2)
    const digits = args.length > 1 ? intArg("round", args[1]) : 0
    if (digits < 0 || digits > 10) throw new ExprError("domain", "round() digits must be 0-10")
    const x = numArg("round", args[0])
    if (!x) return 0
    const m = Math.pow(10, digits)
    return Math.sign(x) * (Math.floor(Math.abs(x) * m + 0.5) / m)
  },
  sqrt(ev, ...args) {
    arity("sqrt", args, 1, 1)
    const x = numArg("sqrt", args[0])
    if (x < 0) throw new ExprError("domain", "sqrt() of a negative number")
    return Math.sqrt(x)
  },
  str(ev, ...args) {
    arity("str", args, 1, 1)
    return toDisplay(args[0])
  },
  sum(ev, ...args) {
    arity("sum", args, 1, 1)
    const values = numbers("sum", ev, [listArg("sum", args[0])])
    return values.length ? finite(fsum(values)) : 0
  },
  values(ev, ...args) {
    arity("values", args, 1, 1)
    if (!isObj(args[0])) throw new ExprError("type", `values() of ${typeName(args[0])}`)
    return Object.values(args[0])
  },
}

/** Names an AST reads from its scope (function names excluded). */
export function freeNames(node, out = new Set()) {
  switch (node[0]) {
    case "name":
      out.add(node[1])
      break
    case "lit":
      break
    case "call":
      node[2].forEach((a) => freeNames(a, out))
      break
    case "list":
      node[1].forEach((a) => freeNames(a, out))
      break
    case "obj":
      node[1].forEach(([, a]) => freeNames(a, out))
      break
    case "member":
      freeNames(node[1], out)
      break
    default:
      for (const child of node.slice(1)) if (Array.isArray(child)) freeNames(child, out)
  }
  return out
}

const astCache = new Map()

function cachedParse(text) {
  let node = astCache.get(text)
  if (!node) {
    node = parse(text)
    if (astCache.size > 500) astCache.clear()
    astCache.set(text, node)
  }
  return node
}

/** Evaluate an expression (text or AST) against ``scope``. */
export function evaluate(expr, scope = {}, maxSteps = MAX_STEPS) {
  const node = typeof expr === "string" ? cachedParse(expr) : expr
  return new Evaluator(scope || {}, maxSteps).run(node)
}

const TEMPLATE_WHOLE = /^\s*\{\{([\s\S]*?)\}\}\s*$/
const TEMPLATE_PART = /\{\{([\s\S]*?)\}\}/g

/** The inner expression when ``text`` is exactly one ``{{ expr }}``, else null. */
export function templateExpression(text) {
  if (typeof text !== "string") return null
  const match = TEMPLATE_WHOLE.exec(text)
  if (!match || match[1].includes("}}") || match[1].includes("{{")) return null
  return match[1]
}

/**
 * Resolve ``{{ expr }}`` in ``text``. A string that is exactly one template
 * keeps the value's type; otherwise each part becomes display text. Errors throw.
 */
export function interpolate(text, scope = {}) {
  if (typeof text !== "string") return text
  const whole = templateExpression(text)
  if (whole !== null) return evaluate(whole, scope)
  if (!text.includes("{{")) return text
  return text.replace(TEMPLATE_PART, (_, inner) => toDisplay(evaluate(inner, scope)))
}
