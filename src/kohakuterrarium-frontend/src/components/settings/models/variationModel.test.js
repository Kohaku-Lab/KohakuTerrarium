import { describe, expect, it } from "vitest"

import {
  addScale,
  deserializeGroups,
  describePatch,
  diffOf,
  isBlocking,
  isSimple,
  resolvePreview,
  selectorOf,
  serializeGroups,
  setOptionValue,
  setTarget,
  validateGroups,
  VARIATION_TEMPLATES,
} from "./variationModel"

const WIRE = {
  reasoning: {
    low: { "extra_body.reasoning.effort": "low" },
    high: { "extra_body.reasoning.effort": "high" },
  },
  speed: { normal: {}, fast: { service_tier: "priority" } },
  context: { "1m": { max_context: 1000000 } },
  thinking: { off: { "extra_body.reasoning.enabled": false }, custom: { extra_body: { a: 1 } } },
}

describe("variation groups", () => {
  it("round-trips every value type through the form shape and keeps the wire types", () => {
    const form = deserializeGroups(WIRE)

    expect(form.find((g) => g.name === "context").options[0].patches[0]).toEqual({
      path: "max_context",
      type: "number",
      value: "1000000",
    })
    expect(form.find((g) => g.name === "thinking").options[0].patches[0]).toMatchObject({
      type: "bool",
      value: "false",
    })
    expect(form.find((g) => g.name === "thinking").options[1].patches[0]).toMatchObject({
      type: "json",
      value: '{"a":1}',
    })
    expect(serializeGroups(form)).toEqual(WIRE)
  })

  it("drops nameless groups, options and targetless patches, and keeps a no-op option", () => {
    const form = [
      { name: "", options: [{ name: "x", patches: [] }] },
      {
        name: "speed",
        options: [
          { name: "", patches: [] },
          { name: "normal", patches: [{ path: "", type: "text", value: "z" }] },
        ],
      },
    ]
    expect(serializeGroups(form)).toEqual({ speed: { normal: {} } })
  })

  it("flags what would be lost or rejected, and tells blocking issues from warnings", () => {
    const form = [
      {
        name: "a",
        options: [
          { name: "o", patches: [{ path: "service_tier", type: "text", value: "x" }] },
          { name: "o", patches: [{ path: "model", type: "text", value: "x" }] },
          { name: "", patches: [] },
        ],
      },
      {
        name: "a",
        options: [{ name: "o", patches: [{ path: "service_tier", type: "number", value: "abc" }] }],
      },
    ]
    const codes = validateGroups(form).map((issue) => issue.code)

    expect(codes).toEqual(
      expect.arrayContaining([
        "optionDuplicate",
        "patchRoot",
        "optionName",
        "groupDuplicate",
        "patchValue",
        "pathShared",
        "optionShared",
      ]),
    )
    expect(validateGroups(deserializeGroups(WIRE))).toEqual([])
    expect(isBlocking({ code: "pathShared" })).toBe(false)
    expect(isBlocking({ code: "patchValue" })).toBe(true)
  })

  it("builds the request a selection resolves to, leaving the base untouched", () => {
    const base = { model: "m", extra_body: { keep: true } }
    const groups = deserializeGroups(WIRE)

    const resolved = resolvePreview(base, groups, {
      reasoning: "high",
      speed: "fast",
      context: "nope",
    })

    expect(resolved).toEqual({
      model: "m",
      service_tier: "priority",
      extra_body: { keep: true, reasoning: { effort: "high" } },
    })
    expect(base).toEqual({ model: "m", extra_body: { keep: true } })
  })

  it("writes the selector users type", () => {
    expect(selectorOf("openai", "gpt", { reasoning: "high", speed: "" })).toBe(
      "openai/gpt@reasoning=high",
    )
    expect(selectorOf("", "gpt", {})).toBe("gpt")
  })

  it("describes a patch on one line", () => {
    expect(
      describePatch({ path: "extra_body.reasoning.effort", type: "text", value: "high" }),
    ).toBe('reasoning.effort = "high"')
    expect(describePatch({ path: "max_context", type: "number", value: "5" })).toBe(
      "max_context = 5",
    )
  })

  it("tells a one-target knob from one whose options change different fields", () => {
    const form = deserializeGroups(WIRE)
    const byName = (name) => form.find((g) => g.name === name)

    expect(isSimple(byName("reasoning"))).toBe(true)
    expect(byName("speed")).toMatchObject({ target: "service_tier", type: "text" })
    expect(isSimple(byName("speed"))).toBe(true)
    expect(isSimple(byName("thinking"))).toBe(false)
    expect(
      isSimple({
        options: [
          { name: "a", patches: [{ path: "x", type: "text", value: "1" }] },
          { name: "b", patches: [{ path: "y", type: "text", value: "1" }] },
        ],
      }),
    ).toBe(false)
  })

  it("edits a simple knob through its target, type and per-option values", () => {
    const group = deserializeGroups({
      speed: { normal: {}, fast: { service_tier: "priority" } },
    })[0]

    setOptionValue(group, group.options[0], "flex")
    expect(group.options[0].patches).toEqual([
      { path: "service_tier", type: "text", value: "flex" },
    ])
    setOptionValue(group, group.options[0], "")
    expect(group.options[0].patches).toEqual([])
    setTarget(group, "extra_body.service_tier")
    expect(group.options[1].patches[0].path).toBe("extra_body.service_tier")
    expect(serializeGroups([group])).toEqual({
      speed: { normal: {}, fast: { "extra_body.service_tier": "priority" } },
    })
  })

  it("adds a scale of options valued by their own names, skipping ones already there", () => {
    const group = { name: "r", target: "reasoning_effort", type: "text", options: [] }

    addScale(group, "low, medium  high low")
    addScale(group, "medium xhigh")

    expect(group.options.map((o) => o.name)).toEqual(["low", "medium", "high", "xhigh"])
    expect(serializeGroups([group]).r.xhigh).toEqual({ reasoning_effort: "xhigh" })
  })

  it("lists only the fields a selection changes, including nested ones", () => {
    const base = { temperature: 1, extra_body: { reasoning: { effort: "low" }, keep: 1 } }
    const after = {
      temperature: 1,
      extra_body: { reasoning: { effort: "high" }, keep: 1 },
      service_tier: "p",
    }

    expect(diffOf(base, after)).toEqual([
      { path: "extra_body.reasoning.effort", before: "low", after: "high" },
      { path: "service_tier", before: undefined, after: "p" },
    ])
  })

  it("offers templates that are valid as built", () => {
    for (const template of VARIATION_TEMPLATES) {
      expect(validateGroups([template.build()])).toEqual([])
    }
  })
})
