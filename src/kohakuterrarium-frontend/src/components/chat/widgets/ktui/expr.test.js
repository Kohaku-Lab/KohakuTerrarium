import { readFileSync } from "node:fs"

import { describe, expect, it } from "vitest"

import { ExprError, evaluate, freeNames, interpolate, parse, toDisplay } from "./expr.js"

// The same file the Python evaluator runs; vitest runs from the frontend root.
const DATA = JSON.parse(readFileSync("../../tests/fixtures/kt_ui_expressions.json", "utf-8"))

function run(testCase) {
  return "expr" in testCase
    ? evaluate(testCase.expr, DATA.scope)
    : interpolate(testCase.template, DATA.scope)
}

describe("kt.ui expression conformance (shared with Python)", () => {
  for (const testCase of DATA.cases) {
    const label = testCase.expr ?? testCase.template
    it(label, () => {
      if ("error" in testCase) {
        let caught = null
        try {
          run(testCase)
        } catch (err) {
          caught = err
        }
        expect(caught).toBeInstanceOf(ExprError)
        expect(caught.code).toBe(testCase.error)
      } else {
        expect(run(testCase)).toEqual(testCase.value)
      }
    })
  }
})

describe("kt.ui expression limits and helpers", () => {
  it("refuses long and deeply nested expressions", () => {
    expect(() => parse("1+".repeat(1100) + "1")).toThrow(/longer than/)
    expect(() => parse("(".repeat(70) + "1" + ")".repeat(70))).toThrow(/nests too deeply/)
  })

  it("stops runaway evaluation by step count", () => {
    expect(() => evaluate("sum(range(1000)) + sum(range(1000))", {}, 1500)).toThrow(
      /too many steps/,
    )
  })

  it("never reaches JavaScript globals or prototypes", () => {
    expect(() => evaluate("window")).toThrow(ExprError)
    expect(() => evaluate("globalThis")).toThrow(ExprError)
    expect(evaluate("o.toString", { o: {} })).toBe(null)
    expect(evaluate("o['__proto__']", { o: {} })).toBe(null)
    expect(() => evaluate("hasOwnProperty('x')")).toThrow(/unknown function/)
  })

  it("lists free names and formats display text", () => {
    expect([...freeNames(parse("a + round(b.c, d[0]) + e ? f : 'g'"))].sort()).toEqual([
      "a",
      "b",
      "d",
      "e",
      "f",
    ])
    expect(toDisplay(-0)).toBe("0")
    expect(toDisplay(undefined)).toBe("")
  })
})
