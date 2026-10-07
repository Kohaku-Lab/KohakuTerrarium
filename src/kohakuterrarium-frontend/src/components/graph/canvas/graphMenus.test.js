import { describe, expect, it } from "vitest"

import { menuItemsFor } from "./graphMenus"

const t = (key) => key
const ids = (items) => items.filter((i) => !i.divider).map((i) => i.id)
const view = (extra = {}) => ({ isSample: false, model: { sessions: [{ id: "g" }] }, ...extra })

describe("graph context menus", () => {
  it("offers lifecycle actions that match the creature's state", () => {
    expect(ids(menuItemsFor({ kind: "creature", item: { status: "busy" } }, view(), t))).toEqual(
      expect.arrayContaining(["interrupt", "stop"]),
    )
    const stopped = ids(menuItemsFor({ kind: "creature", item: { status: "stopped" } }, view(), t))
    expect(stopped).toContain("start")
    expect(stopped).not.toContain("stop")
    expect(stopped).not.toContain("interrupt")
    expect(
      menuItemsFor({ kind: "creature", item: { status: "idle" } }, view(), t).at(-1),
    ).toMatchObject({ id: "remove", danger: true })
  })

  it("disables disconnect on a bundled edge but still opens its details", () => {
    const bundle = menuItemsFor({ kind: "edge", item: { kind: "channel", count: 3 } }, view(), t)
    expect(bundle.find((i) => i.id === "remove").disabled).toBe(true)
    expect(bundle.find((i) => i.id === "details").disabled).toBeFalsy()
    const single = menuItemsFor({ kind: "edge", item: { kind: "wire", count: 1 } }, view(), t)
    expect(single.some((i) => i.disabled)).toBe(false)
  })

  it("offers payload and direction changes on a single wire", () => {
    const content = menuItemsFor({ kind: "edge", item: { kind: "wire", count: 1 } }, view(), t)
    expect(ids(content)).toEqual(["details", "wire-payload", "wire-reverse", "remove"])
    expect(content.find((i) => i.id === "wire-payload").label).toBe("graph.edge.ping")
    const ping = menuItemsFor(
      { kind: "edge", item: { kind: "wire", count: 1, withContent: false } },
      view(),
      t,
    )
    expect(ping.find((i) => i.id === "wire-payload").label).toBe("graph.edge.content")
    const channel = menuItemsFor({ kind: "edge", item: { kind: "channel", count: 1 } }, view(), t)
    expect(ids(channel)).toEqual(["details", "remove"])
  })

  it("only offers session actions on session groups and disables adding without a session", () => {
    expect(
      ids(menuItemsFor({ kind: "group", item: { kind: "host", collapsed: false } }, view(), t)),
    ).toEqual(["toggle-collapse"])
    expect(
      ids(menuItemsFor({ kind: "group", item: { kind: "session", collapsed: true } }, view(), t)),
    ).toContain("stop-session")
    const pane = menuItemsFor({ kind: "pane" }, view({ model: { sessions: [] } }), t)
    expect(pane.find((i) => i.id === "add-creature").disabled).toBe(true)
    expect(menuItemsFor({ kind: "unknown" }, view(), t)).toEqual([])
  })
})
