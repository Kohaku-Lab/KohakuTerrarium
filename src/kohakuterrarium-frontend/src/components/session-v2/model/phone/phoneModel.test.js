import { describe, expect, it } from "vitest"

import { conversationRows, creatureOfTab, modelOfCreature, phoneTools } from "./phoneModel"

const INSTANCE = {
  creatures: [
    { name: "worker", llm_name: "codex/a", send_channels: ["tasks"] },
    { name: "lead", is_privileged: true, llm_name: "codex/b", listen_channels: ["tasks"] },
    { name: "idle", running: false, model: "bare" },
  ],
  channels: [{ name: "tasks", description: "Work items" }, { name: "worker" }],
}
const CHAT = {
  _rootSourceName: "lead",
  activeTab: "root",
  modelByTab: { root: { llmName: "codex/live" } },
  processingByTab: { worker: true },
  unreadCounts: { root: 4, worker: 2, "ch:tasks": 1 },
}

describe("phone conversations", () => {
  it("lists the privileged node first, then creatures, then designed channels, with what a row shows", () => {
    expect(conversationRows(INSTANCE, CHAT)).toEqual([
      {
        key: "root",
        kind: "creature",
        name: "lead",
        status: "running",
        privileged: true,
        busy: false,
        unread: 0,
        active: true,
        detail: "codex/live",
      },
      {
        key: "worker",
        kind: "creature",
        name: "worker",
        status: "running",
        privileged: false,
        busy: true,
        unread: 2,
        active: false,
        detail: "codex/a",
      },
      {
        key: "idle",
        kind: "creature",
        name: "idle",
        status: "stopped",
        privileged: false,
        busy: false,
        unread: 0,
        active: false,
        detail: "bare",
      },
      {
        key: "ch:tasks",
        kind: "channel",
        name: "tasks",
        status: null,
        privileged: false,
        busy: false,
        unread: 1,
        active: false,
        detail: "Work items",
      },
    ])
    expect(conversationRows(null, {})).toEqual([])
  })

  it("reads a creature's model live from its chat key first", () => {
    expect(modelOfCreature(INSTANCE, CHAT, "lead")).toBe("codex/live")
    expect(modelOfCreature(INSTANCE, CHAT, "worker")).toBe("codex/a")
    expect(modelOfCreature(INSTANCE, { modelByTab: { worker: { model: "m" } } }, "worker")).toBe(
      "m",
    )
    expect(modelOfCreature(INSTANCE, CHAT, "")).toBe("")
    expect(modelOfCreature(INSTANCE, CHAT, "ghost")).toBe("")
  })

  it("names the creature behind a tab, never a channel or an unknown key", () => {
    expect(creatureOfTab(INSTANCE, "root", "lead")).toBe("lead")
    expect(creatureOfTab(INSTANCE, "worker", "lead")).toBe("worker")
    expect(creatureOfTab(INSTANCE, "ch:tasks", "lead")).toBe("")
    expect(creatureOfTab(INSTANCE, "ghost", "lead")).toBe("")
    expect(creatureOfTab(INSTANCE, null, "lead")).toBe("")
  })
})

describe("phone tools", () => {
  it("puts counted live tools first and never lists the graph (it is a tab)", () => {
    const ids = (facts) => phoneTools(facts).map((t) => `${t.id}:${t.count}`)
    expect(ids({ drives: 2, jobs: 0, artifacts: 1 })).toEqual([
      "drives:2",
      "canvas:1",
      "usage:0",
      "agents:0",
      "channels:0",
      "scratchpad:0",
      "plugins:0",
      "search:0",
      "terminal:0",
    ])
    expect(ids()).not.toContain("graph:0")
    expect(phoneTools().find((t) => t.id === "terminal").target).toBe("side")
  })
})
