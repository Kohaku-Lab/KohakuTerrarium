import { describe, expect, it } from "vitest"

import {
  artifactFileName,
  artifactViewer,
  channelRows,
  creatureModel,
  elapsedLabel,
  focusedCreature,
  moduleGroups,
  scratchpadRows,
} from "./widgetData"

const instance = {
  creatures: [
    { name: "planner", listen_channels: ["tasks"], send_channels: ["plan", "planner"] },
    { name: "critic", listen_channels: ["plan"], send_channels: ["tasks"], llm_name: "gpt" },
  ],
  channels: [{ name: "tasks" }, { name: "plan" }, { name: "planner" }],
}

describe("focusedCreature", () => {
  it("follows the open creature conversation, not a channel", () => {
    expect(focusedCreature(instance, "critic")).toBe("critic")
    expect(focusedCreature(instance, "ch:tasks")).toBe(null)
  })

  it("falls back to a solo session's only creature", () => {
    expect(focusedCreature({ creatures: [{ name: "solo" }] }, "ch:x")).toBe("solo")
    expect(focusedCreature({ creatures: [] }, "root")).toBe(null)
  })

  it("names the privileged node behind the root tab by its real name", () => {
    expect(focusedCreature(instance, "root", "planner")).toBe("planner")
  })
})

describe("channelRows", () => {
  it("counts members once across listen and send, hides aliases, carries unread", () => {
    const rows = channelRows(instance, { "ch:tasks": 3 })
    expect(rows.map((r) => [r.name, r.members, r.unread])).toEqual([
      ["tasks", 2, 3],
      ["plan", 2, 0],
    ])
    expect(rows[0].listeners).toEqual(["planner"])
    expect(rows[0].senders).toEqual(["critic"])
  })
})

describe("elapsedLabel", () => {
  it("labels seconds, minutes and hours", () => {
    expect(elapsedLabel(0)).toBe("")
    expect(elapsedLabel(1000, 6000)).toBe("5s")
    expect(elapsedLabel(0 + 1, 125001)).toBe("2m 5s")
    expect(elapsedLabel(1, 3600001 + 60000)).toBe("1h 1m")
  })
})

describe("artifacts", () => {
  it("names downloads by type and picks a viewer", () => {
    expect(artifactFileName({ name: "my plot!", type: "image", lang: "webp" })).toBe(
      "my_plot_.webp",
    )
    expect(artifactFileName({ name: "a", type: "code", lang: "py" })).toBe("a.py")
    expect(artifactViewer({ type: "svg" })).toBe("code")
    expect(artifactViewer({ type: "markdown" })).toBe("markdown")
  })
})

describe("moduleGroups", () => {
  it("puts plugins first and orders by priority then name", () => {
    const groups = moduleGroups([
      { type: "tool", name: "b" },
      { type: "plugin", name: "z", priority: 5 },
      { type: "plugin", name: "a", priority: 10 },
      { type: "custom", name: "c" },
    ])
    expect(groups.map((g) => g.type)).toEqual(["plugin", "tool", "custom"])
    expect(groups[0].items.map((m) => m.name)).toEqual(["z", "a"])
  })
})

describe("misc", () => {
  it("reads models and scratchpad rows", () => {
    expect(creatureModel(instance.creatures[1])).toBe("gpt")
    expect(creatureModel({})).toBe("")
    expect(scratchpadRows({ b: "2", a: { x: 1 } })).toEqual([
      ["a", '{"x":1}'],
      ["b", "2"],
    ])
  })
})
