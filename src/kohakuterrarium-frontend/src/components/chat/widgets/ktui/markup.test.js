import { readFileSync } from "node:fs"

import { describe, expect, it } from "vitest"

import { MarkupError, convertMarkup, dedent, lex, splitMarkup, templateText } from "./markup.js"
import { validateSpec } from "./validate.js"

// The same file the Python converter runs; vitest runs from the frontend root.
const DATA = JSON.parse(readFileSync("../../tests/fixtures/kt_ui_markup.json", "utf-8"))

describe("kt.ui markup conformance: split (shared with Python)", () => {
  for (const testCase of DATA.split) {
    it(testCase.name, () => {
      expect(splitMarkup(testCase.text)).toEqual(testCase.segments)
    })
  }
})

describe("kt.ui markup conformance: convert (shared with Python)", () => {
  for (const testCase of DATA.convert) {
    it(testCase.name, () => {
      if ("error" in testCase) {
        expect(() => convertMarkup(testCase.source)).toThrow(MarkupError)
        expect(() => convertMarkup(testCase.source)).toThrow(testCase.error)
      } else {
        expect(convertMarkup(testCase.source)).toEqual(testCase.spec)
      }
    })
  }

  it("every converted fixture spec passes the browser validator", () => {
    for (const testCase of DATA.convert) {
      if (!("spec" in testCase)) continue
      expect(validateSpec(testCase.spec), testCase.name).toEqual([])
    }
  })
})

describe("kt.ui markup edge cases", () => {
  it("never splits inside a tilde fence or an unclosed fence", () => {
    expect(splitMarkup("~~~\n<ui><text>a</text></ui>\n~~~\nafter")).toEqual([
      { kind: "markdown", text: "~~~\n<ui><text>a</text></ui>\n~~~\nafter" },
    ])
    expect(splitMarkup("```\n<ui>")).toEqual([{ kind: "markdown", text: "```\n<ui>" }])
  })

  it("leaves four-space-indented ui as markdown code", () => {
    expect(splitMarkup("    <ui><text>a</text></ui>")).toEqual([
      { kind: "markdown", text: "    <ui><text>a</text></ui>" },
    ])
  })

  it("holds back a last line that may grow into <ui while streaming", () => {
    for (const tail of ["<", "<u", "  <u"]) {
      expect(splitMarkup(`Intro\n${tail}`, { holdPartial: true })).toEqual([
        { kind: "markdown", text: "Intro\n" },
      ])
      expect(splitMarkup(`Intro\n${tail}`)).toEqual([{ kind: "markdown", text: `Intro\n${tail}` }])
    }
    expect(splitMarkup("a <u", { holdPartial: true })).toEqual([{ kind: "markdown", text: "a <u" }])
    expect(splitMarkup("Intro\n<ui", { holdPartial: true })[1]).toEqual({
      kind: "ui",
      source: "<ui",
      closed: false,
    })
    expect(convertMarkup("<ui")).toEqual({})
  })

  it("a </ui> missing its inner close still ends the block", () => {
    const segments = splitMarkup("<ui><card><text>x</text></ui>\nafter")
    expect(segments[0]).toEqual({
      kind: "ui",
      source: "<ui><card><text>x</text></ui>",
      closed: true,
    })
    expect(segments[1]).toEqual({ kind: "markdown", text: "\nafter" })
    expect(() => convertMarkup(segments[0].source)).toThrow(
      /mismatched <\/ui>: <card> is still open/,
    )
  })

  it("raw diagram content may hold < and </ui>-free arrows", () => {
    const spec = convertMarkup("<ui><diagram>graph TD; A<B --> C</diagram></ui>")
    expect(spec.root).toEqual({ type: "diagram", source: "graph TD; A<B --> C" })
  })

  it("button set keeps each key's expression, unparsed as the backend does; send interpolates", () => {
    const spec = convertMarkup(
      '<ui><state n={0} m={1}/><button set={{n: n + 1, "m": max(n, 2)}} send="n is {n}">Add</button></ui>',
    )
    expect(spec.root).toEqual({
      type: "button",
      set: { n: "(n + 1)", m: "max(n, 2)" },
      message: "n is {{ n }}",
      label: "Add",
    })
    const ok = convertMarkup("<ui><state n={0}/><button set={{n: n + 1}}>Add</button></ui>")
    expect(ok.root).toEqual({ type: "button", set: { n: "(n + 1)" }, label: "Add" })
    expect(validateSpec(ok)).toEqual([])
  })

  it("streaming can drop an unfinished diagram; code stops before a partial close tag", () => {
    const open = "<ui><text>a</text><diagram>graph LR\n  A --> B\n</diagram"
    expect(convertMarkup(open, { dropOpenDiagram: true }).root).toEqual({
      type: "text",
      value: "a",
    })
    expect(convertMarkup(open).root.children[1]).toEqual({
      type: "diagram",
      source: "graph LR\n  A --> B",
    })
    expect(
      convertMarkup("<ui><text>a</text><diagram>graph LR", { dropOpenDiagram: true }).root,
    ).toEqual({
      type: "text",
      value: "a",
    })
    expect(convertMarkup("<ui><code>x = 1\n</co").root).toEqual({ type: "code", value: "x = 1" })
    expect(convertMarkup("<ui><code>a < b</code></ui>").root).toEqual({
      type: "code",
      value: "a < b",
    })
  })

  it("streaming drops an unterminated expression in trailing text", () => {
    expect(convertMarkup("<ui><text>Total: {fmt(tot").root).toEqual({
      type: "text",
      value: "Total:",
    })
    expect(() => convertMarkup("<ui><text>Total: {fmt(tot</text></ui>")).toThrow(/unterminated/)
  })

  it("reports misplaced state, nested ui, children of leaves and a missing <ui>", () => {
    expect(() => convertMarkup("<ui><card><state a={1}/></card></ui>")).toThrow(
      /directly inside <ui>/,
    )
    expect(() => convertMarkup("<ui><ui></ui></ui>")).toThrow(/cannot be nested/)
    expect(() => convertMarkup('<ui><slider bind="a" min={0} max={1}>x</slider></ui>')).toThrow(
      /takes no children/,
    )
    expect(() => convertMarkup("<text>x</text>")).toThrow(/a block starts with <ui>/)
    expect(() => convertMarkup("<ui><card>x</ui>")).toThrow(/mismatched/)
    expect(() => convertMarkup("<ui><text>x</text></ui> more")).toThrow(/content after <\/ui>/)
    expect(() => convertMarkup("<ui><stat value=x/></ui>")).toThrow(/needs a quoted value/)
    expect(() => convertMarkup("<ui><stat label={1 +}/></ui>")).toThrow(/bad expression/)
  })

  it("text-holding elements read their content raw up to their own close tag", () => {
    expect(convertMarkup("<ui><text>a <b>x</b> and </ui> text</text></ui>").root).toEqual({
      type: "text",
      value: "a <b>x</b> and </ui> text",
    })
    expect(convertMarkup('<ui><let a="1"/><text>{a}</text></ui>')).toEqual({
      computed: { a: "1" },
      root: { type: "text", value: "{{ a }}" },
    })
    const tokens = lex('<ui>a<b c="d">e</b></ui>').tokens.map((t) => [t.kind, t.name || t.text])
    expect(tokens).toEqual([
      ["open", "ui"],
      ["text", "a"],
      ["open", "b"],
      ["text", "e"],
      ["close", "b"],
      ["close", "ui"],
    ])
  })

  it("an empty block has no root, which the validator reports", () => {
    expect(convertMarkup("<ui>")).toEqual({})
    expect(validateSpec({})).toEqual([["root", "is required"]])
  })

  it("dedent removes only the common margin, as textwrap.dedent does", () => {
    expect(dedent("first\n    second")).toBe("first\n    second")
    expect(dedent("\n\n  a\n    b\n\n")).toBe("a\n  b")
  })

  it("templates escape doubled braces, nest objects and skip braces in strings", () => {
    expect(templateText("{{ }} {a.b} {{x}}")).toBe("{ } {{ a.b }} {x}")
    expect(templateText("{ {k: 1}.k }")).toBe("{{ {k: 1}.k }}")
    expect(templateText('{"}"}')).toBe('{{ "}" }}')
    expect(templateText("a {b", false)).toBe("a")
    expect(() => templateText("a {b")).toThrow(/unterminated expression/)
  })
})

describe("kt.ui browser validator", () => {
  it("reports unknown components and props, missing required props and bad binds", () => {
    const errors = validateSpec({
      state: { a: 1 },
      root: {
        type: "stack",
        children: [
          { type: "blink" },
          { type: "slider", bind: "zz", min: 0 },
          { type: "text", value: "x", gap: "md" },
          { type: "stack", gap: 12, children: [] },
        ],
      },
    })
    const text = errors.map(([path, message]) => `${path}: ${message}`)
    expect(text).toContain("root.children[0].type: unknown component 'blink'")
    expect(text).toContain("root.children[1].bind: 'zz' is not a key of state")
    expect(text).toContain("root.children[1].max: is required")
    expect(text).toContain("root.children[2].gap: unknown prop for text")
    expect(text).toContain("root.children[3].gap: must be one of none, sm, md, lg")
  })

  it("checks names in templates, computed and each scopes", () => {
    expect(validateSpec({ root: { type: "text", value: "{{ ghost }}" } })).toEqual([
      ["root.value", "unknown name 'ghost'"],
    ])
    const each = {
      state: { rows: [1] },
      root: {
        type: "each",
        items: "rows",
        as: "r",
        child: { type: "text", value: "{{ r }} {{ index }}" },
      },
    }
    expect(validateSpec(each)).toEqual([])
    expect(
      validateSpec({ state: { a: 1 }, computed: { a: "1" }, root: { type: "divider" } })[0],
    ).toEqual(["computed.a", "is already a key of state"])
  })

  it("a form needs send or message", () => {
    expect(validateSpec({ root: { type: "form", children: [] } })).toEqual([
      ["root.send", "is required"],
    ])
    expect(validateSpec({ root: { type: "form", message: "hi", children: [] } })).toEqual([])
  })
})
