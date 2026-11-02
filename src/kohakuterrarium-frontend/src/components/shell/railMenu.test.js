import { describe, expect, it, vi } from "vitest"

vi.mock("@/ext/actions", () => ({
  actionMenuItems: (target) =>
    target === "rail-item" ? [{ id: "action:pkg/x", label: "Ext", icon: "i" }] : [],
}))

import { railMenuItems } from "./railMenu"

const t = (key) => key
const instance = { id: "i1", config_name: "alpha" }

function items(overrides = {}) {
  return railMenuItems({
    instance,
    chatOpen: false,
    inspectorOpen: false,
    canPopOut: true,
    t,
    ...overrides,
  })
}

const byId = (rows, id) => rows.find((row) => row.id === id)

describe("railMenuItems", () => {
  it("toggles the wording of the chat and inspector rows with their state", () => {
    expect(byId(items(), "toggle-chat").label).toBe("shell.rail.openChat")
    expect(byId(items({ chatOpen: true }), "toggle-chat").label).toBe("shell.rail.closeChat")
    expect(byId(items({ inspectorOpen: true }), "toggle-inspector").label).toBe(
      "shell.rail.closeInspector",
    )
  })

  it("offers a window only where windows are supported", () => {
    expect(byId(items(), "open-in-window")).toBeTruthy()
    expect(byId(items({ canPopOut: false }), "open-in-window")).toBeUndefined()
  })

  it("enables detach only while a surface is open and always offers end", () => {
    expect(byId(items(), "detach").enabled).toBe(false)
    expect(byId(items({ chatOpen: true }), "detach").enabled).toBe(true)
    expect(byId(items({ inspectorOpen: true }), "detach").enabled).toBe(true)
    expect(byId(items(), "end")).toBeTruthy()
  })

  it("appends extension actions after a separator, as plain rows", () => {
    const rows = items()
    const last = rows.at(-1)

    expect(last).toEqual({ id: "action:pkg/x", label: "Ext" })
    expect(rows.at(-2)).toEqual({ separator: true })
  })
})
