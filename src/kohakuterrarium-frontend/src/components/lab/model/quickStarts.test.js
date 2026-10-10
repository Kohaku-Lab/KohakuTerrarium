import { describe, expect, it } from "vitest"

import { quickStarts } from "./quickStarts"

describe("quick starts", () => {
  it("offers each config once, newest first, recipes as recipes, up to the limit", () => {
    const sessions = [
      { config_path: "@kt-biome/creatures/general", config_type: "agent" },
      { config_path: "" },
      { config_path: "/home/u/recipes/deep_research/", config_type: "terrarium" },
      { config_path: "@kt-biome/creatures/general", config_type: "agent" },
      { config_path: "@kt-biome/creatures/swe" },
      { config_path: "@kt-biome/creatures/extra" },
    ]
    expect(quickStarts(sessions)).toEqual([
      { configPath: "@kt-biome/creatures/general", kind: "creature", label: "general" },
      { configPath: "/home/u/recipes/deep_research/", kind: "terrarium", label: "deep_research" },
      { configPath: "@kt-biome/creatures/swe", kind: "creature", label: "swe" },
    ])
    expect(quickStarts(sessions, 1)).toHaveLength(1)
    expect(quickStarts(null)).toEqual([])
  })
})
