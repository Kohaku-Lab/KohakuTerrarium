import { describe, expect, it } from "vitest"

import {
  MORE_ITEMS,
  SESSION_TABS,
  channelMembers,
  conversationOptions,
  dockItems,
} from "./sessionModel"
import { SIDE_KINDS, TAB_IDS, WIDGET_IDS } from "./registry"

describe("conversationOptions", () => {
  it("puts the privileged node first, then creatures, then channels, without direct-message aliases", () => {
    const instance = {
      creatures: [
        { name: "planner", status: "running" },
        { name: "root", status: "running", is_privileged: true },
        { name: "critic", running: false },
      ],
      channels: [{ name: "tasks" }, { name: "critic" }, { name: "team_chat" }],
    }
    expect(conversationOptions(instance).map((o) => o.key)).toEqual([
      "root",
      "planner",
      "critic",
      "ch:tasks",
      "ch:team_chat",
    ])
    expect(conversationOptions(instance).find((o) => o.key === "critic").status).toBe("stopped")
  })

  it("lists an aliased privileged node once, keyed root, under its real name and status", () => {
    const instance = {
      creatures: [
        { name: "worker", running: true },
        { name: "coordinator", is_root: true, is_privileged: true, running: false },
      ],
    }
    expect(conversationOptions(instance, "coordinator")).toEqual([
      { key: "root", kind: "creature", name: "coordinator", status: "stopped", privileged: true },
      { key: "worker", kind: "creature", name: "worker", status: "running", privileged: false },
    ])
    expect(conversationOptions(instance, null).map((o) => o.key)).toEqual(["coordinator", "worker"])
  })

  it("does not treat a creature named root as privileged by name alone", () => {
    expect(conversationOptions({ creatures: [{ name: "root" }] })[0].privileged).toBe(false)
  })
})

describe("channelMembers", () => {
  it("lists senders and listeners of one channel with their roles, privileged first", () => {
    const instance = {
      creatures: [
        { name: "worker", running: false, listen_channels: ["tasks"], send_channels: ["report"] },
        { name: "root", is_privileged: true, status: "running", send_channels: ["tasks"] },
        { name: "alpha", status: "running", send_channels: ["tasks"], listen_channels: ["tasks"] },
        { name: "idle", status: "running", listen_channels: ["other"] },
      ],
    }
    expect(channelMembers(instance, "tasks", "root")).toEqual([
      {
        name: "root",
        key: "root",
        status: "running",
        privileged: true,
        sends: true,
        listens: false,
      },
      {
        name: "alpha",
        key: "alpha",
        status: "running",
        privileged: false,
        sends: true,
        listens: true,
      },
      {
        name: "worker",
        key: "worker",
        status: "stopped",
        privileged: false,
        sends: false,
        listens: true,
      },
    ])
    const aliased = channelMembers(
      { creatures: [{ name: "lead", is_privileged: true, send_channels: ["x"] }] },
      "x",
      "lead",
    )
    expect(aliased[0].key).toBe("root")
    expect(channelMembers(instance, "missing")).toEqual([])
  })
})

describe("dockItems", () => {
  it("shows only what has content, and never agents or channels (the rail lists them)", () => {
    expect(dockItems({ drives: 0, jobs: 0, artifacts: 0 })).toEqual([])
    const busy = { creatures: 5, channels: 3, drives: 0, jobs: 2, artifacts: 1 }
    expect(dockItems(busy).map((i) => [i.id, i.count])).toEqual([
      ["jobs", 2],
      ["canvas", 1],
    ])
  })

  it("keeps agent and channel controls reachable from More", () => {
    const widgets = MORE_ITEMS.filter((i) => i.target === "widget").map((i) => i.id)
    expect(widgets).toEqual(expect.arrayContaining(["usage", "agents", "channels"]))
  })

  it("shows usage first once tokens are spent, labelled and uncounted", () => {
    const idle = { drives: 0, jobs: 0, artifacts: 0 }
    expect(dockItems({ ...idle, usage: { tokens: 0, label: "0%" } })).toEqual([])
    const items = dockItems({ ...idle, jobs: 1, usage: { tokens: 1200, label: "37%" } })
    expect(items.map((i) => [i.id, i.count, i.label])).toEqual([
      ["usage", undefined, "37%"],
      ["jobs", 1, undefined],
    ])
  })
})

describe("registries", () => {
  it("has a widget for every dock item and widget entry of More, and a side view for every side entry", () => {
    const busy = dockItems({ drives: 1, jobs: 1, artifacts: 1, usage: { tokens: 1, label: "1" } })
    expect(busy.length).toBe(4)
    for (const item of busy) expect(WIDGET_IDS, item.id).toContain(item.id)
    for (const item of MORE_ITEMS)
      expect(item.target === "widget" ? WIDGET_IDS : SIDE_KINDS, item.id).toContain(item.id)
    for (const tab of SESSION_TABS.filter((s) => s.id !== "chat")) expect(TAB_IDS).toContain(tab.id)
  })
})
