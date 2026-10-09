import { describe, expect, it } from "vitest"

import {
  bareModelName,
  creatureNames,
  defaultTarget,
  errorText,
  estimateCost,
  formatTokens,
  modelId,
  priceFor,
} from "./settingsModel"

const instance = {
  creatures: [
    { name: "planner" },
    { name: "lead", is_root: true, is_privileged: true },
    { name: "critic" },
  ],
}

describe("settings targets", () => {
  it("orders the privileged creature first and ignores the literal name root", () => {
    expect(creatureNames(instance)).toEqual(["lead", "planner", "critic"])
    expect(creatureNames({ creatures: [{ name: "a" }, { name: "root" }] })).toEqual(["a", "root"])
    expect(creatureNames(null)).toEqual([])
  })

  it("starts on the open creature conversation (root tab = privileged node), else the first creature", () => {
    expect(defaultTarget(instance, "critic")).toBe("critic")
    expect(defaultTarget(instance, "root", "lead")).toBe("lead")
    expect(defaultTarget(instance, "ch:tasks")).toBe("lead")
    expect(defaultTarget(instance, "ghost")).toBe("lead")
    expect(defaultTarget({ creatures: [{ name: "solo" }] }, "root")).toBe("solo")
    expect(defaultTarget({ creatures: [] }, "root")).toBe(null)
  })
})

describe("models and cost", () => {
  it("builds provider/name ids and strips provider and variations", () => {
    expect(modelId({ provider: "openai", name: "gpt-4o" })).toBe("openai/gpt-4o")
    expect(modelId({ login_provider: "codex", name: "x" })).toBe("codex/x")
    expect(bareModelName("anthropic/claude-opus-4-6@thinking=high")).toBe("claude-opus-4-6")
  })

  it("prices by the longest matching prefix and leaves unknown models unpriced", () => {
    expect(priceFor("openai/gpt-4o-mini-2024").prefix).toBe("gpt-4o-mini")
    expect(priceFor("o1-mini").prefix).toBe("o1-mini")
    expect(priceFor("codex/gpt-5.6-sol")).toBe(null)
    expect(priceFor("anthropic/claude-opus-4.6@thinking").prefix).toBe("claude-opus-4-6")
    expect(priceFor("anthropic/Claude-Haiku-4.5").prefix).toBe("claude-haiku-4-5")
    expect(estimateCost("openai/gpt-4o", { prompt: 1_000_000, completion: 100_000 })).toBeCloseTo(
      3.5,
    )
    expect(estimateCost("mystery", { prompt: 5 })).toBe(null)
  })

  it("bills cached input at the cache rate, never more cached than prompt", () => {
    // 1M prompt of which 600k cached on gpt-4o: 400k×2.5 + 600k×1.25 = 1.75
    expect(estimateCost("openai/gpt-4o", { prompt: 1_000_000, cached: 600_000 })).toBeCloseTo(1.75)
    expect(estimateCost("openai/gpt-4o", { prompt: 100, cached: 999 })).toBeCloseTo(
      (100 * 1.25) / 1e6,
    )
    expect(
      estimateCost("anthropic/claude-opus-4-6", { prompt: 1_000_000, completion: 1_000_000 }),
    ).toBeCloseTo(30)
  })

  it("formats tokens and errors", () => {
    expect(formatTokens(0)).toBe("0")
    expect(formatTokens(1500)).toBe("1.5K")
    expect(formatTokens(2_500_000)).toBe("2.5M")
    expect(errorText({ response: { data: { detail: "nope" } } })).toBe("nope")
    expect(errorText(new Error("boom"))).toBe("boom")
  })
})
