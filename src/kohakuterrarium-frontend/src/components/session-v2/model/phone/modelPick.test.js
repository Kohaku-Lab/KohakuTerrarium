import { describe, expect, it } from "vitest"

import {
  buildSelector,
  findEntry,
  keepOffered,
  parseSelector,
  presetsFor,
  providerList,
  providerOf,
  variationGroups,
} from "./modelPick"

const MODELS = [
  {
    provider: "codex",
    name: "gpt-6",
    model: "gpt-6",
    available: true,
    variation_groups: { reasoning: { low: {}, high: {} }, speed: { fast: {} } },
  },
  { provider: "codex", name: "gpt-5", model: "gpt-5", available: true },
  { provider: "openai", name: "gpt-6", model: "gpt-6-2026", available: false },
  { login_provider: "anthropic", name: "opus", model: "claude-opus", available: false },
  { provider: "openai", name: "mini", model: "gpt-mini", available: true },
]

describe("selectors", () => {
  it("parses provider, name and variations, and bare names", () => {
    expect(parseSelector("codex/gpt-6@reasoning=high,speed=fast")).toEqual({
      provider: "codex",
      name: "gpt-6",
      selections: { reasoning: "high", speed: "fast" },
    })
    expect(parseSelector("gpt-6")).toEqual({ provider: "", name: "gpt-6", selections: {} })
    expect(parseSelector("a/b@broken,=x,y=")).toEqual({ provider: "a", name: "b", selections: {} })
    expect(parseSelector("")).toEqual({ provider: "", name: "", selections: {} })
  })

  it("builds a selector with sorted groups and drops empty options", () => {
    expect(buildSelector("codex", "gpt-6", { speed: "fast", reasoning: "high", empty: "" })).toBe(
      "codex/gpt-6@reasoning=high,speed=fast",
    )
    expect(buildSelector("", "gpt-6")).toBe("gpt-6")
    expect(buildSelector("codex", "")).toBe("")
    expect(buildSelector(...Object.values(parseSelector("x/y@b=2,a=1")))).toBe("x/y@a=1,b=2")
  })
})

describe("inventory", () => {
  it("groups providers, available first, counting what the query keeps", () => {
    expect(providerList(MODELS)).toEqual([
      { name: "codex", count: 2, available: true },
      { name: "openai", count: 2, available: true },
      { name: "anthropic", count: 1, available: false },
    ])
    expect(providerList(MODELS, "OPUS")).toEqual([
      { name: "anthropic", count: 1, available: false },
    ])
    expect(providerList(MODELS, "codex").map((p) => p.name)).toEqual(["codex"])
    expect(providerOf({})).toBe("unknown")
  })

  it("lists one provider's presets, available first then by name", () => {
    expect(presetsFor(MODELS, "openai").map((m) => m.name)).toEqual(["mini", "gpt-6"])
    expect(presetsFor(MODELS, "codex", "gpt-5").map((m) => m.name)).toEqual(["gpt-5"])
    expect(presetsFor(MODELS, "nobody")).toEqual([])
  })

  it("finds the exact provider entry before a bare-name or model-id match", () => {
    expect(findEntry(MODELS, "openai/gpt-6").model).toBe("gpt-6-2026")
    expect(findEntry(MODELS, "gpt-6").provider).toBe("codex")
    expect(findEntry(MODELS, "claude-opus").name).toBe("opus")
    expect(findEntry(MODELS, "nope")).toBe(null)
    expect(findEntry(MODELS, "")).toBe(null)
  })

  it("lists variation groups and keeps only offered options", () => {
    expect(variationGroups(MODELS[0])).toEqual([
      { name: "reasoning", options: ["low", "high"] },
      { name: "speed", options: ["fast"] },
    ])
    expect(variationGroups(MODELS[1])).toEqual([])
    expect(keepOffered(MODELS[0], { reasoning: "high", speed: "slow", other: "x" })).toEqual({
      reasoning: "high",
    })
    expect(keepOffered(null, { a: "b" })).toEqual({})
  })
})
