import { describe, expect, it } from "vitest"

import { cloneOf, formatCount, freeName, groupPresets, presetKey } from "./presetModel"

const PRESETS = [
  { name: "b", model: "mb", provider: "openai", source: "preset", available: true },
  { name: "a", model: "ma", provider: "openai", source: "user", available: true },
  { name: "c", model: "mc", provider: "codex", source: "preset", available: false },
  { name: "d", model: "md", login_provider: "grok", source: "preset", available: true },
]

describe("preset model helpers", () => {
  it("groups by provider with ready providers first and the user's presets first inside", () => {
    const groups = groupPresets(PRESETS)

    expect(groups.map((g) => g.provider)).toEqual(["grok", "openai", "codex"])
    expect(groups.find((g) => g.provider === "openai").presets.map((p) => p.name)).toEqual([
      "a",
      "b",
    ])
  })

  it("filters by text and by ownership or readiness", () => {
    expect(groupPresets(PRESETS, "ma").flatMap((g) => g.presets.map((p) => p.name))).toEqual(["a"])
    expect(groupPresets(PRESETS, "", "mine").flatMap((g) => g.presets.map((p) => p.name))).toEqual([
      "a",
    ])
    expect(
      groupPresets(PRESETS, "", "builtin")
        .flatMap((g) => g.presets.map((p) => p.name))
        .sort(),
    ).toEqual(["b", "c", "d"])
    expect(
      groupPresets(PRESETS, "", "ready")
        .flatMap((g) => g.presets.map((p) => p.name))
        .sort(),
    ).toEqual(["a", "b", "d"])
  })

  it("names a copy after the first free slot of its provider only", () => {
    expect(freeName("a", "openai", PRESETS)).toBe("a-custom")
    const taken = [
      ...PRESETS,
      { name: "a-custom", provider: "openai" },
      { name: "a-custom-2", provider: "openai" },
    ]
    expect(freeName("a", "openai", taken)).toBe("a-custom-3")
    expect(freeName("a", "codex", taken)).toBe("a-custom")
  })

  it("copies a preset without sharing its nested data or its default flag", () => {
    const base = { ...PRESETS[1], is_default: true, extra_body: { x: { y: 1 } } }
    const copy = cloneOf(base, PRESETS)

    expect(copy).toMatchObject({ name: "a-custom", source: "user", is_default: false })
    copy.extra_body.x.y = 2
    expect(base.extra_body.x.y).toBe(1)
  })

  it("formats counts and keys", () => {
    expect(formatCount(128000)).toBe("128k")
    expect(formatCount(1000000)).toBe("1M")
    expect(formatCount(0)).toBe("")
    expect(presetKey({ login_provider: "x", name: "n" })).toBe("x/n")
  })
})
