import { beforeEach, describe, expect, it } from "vitest"
import { createApp, h } from "vue"

import { DEFAULT_RAIL_WIDTH, useRailWidth } from "./useRailWidth"

function mountRail() {
  let api
  const el = document.createElement("div")
  const app = createApp({
    setup() {
      api = useRailWidth()
      return () => h("div")
    },
  })
  app.mount(el)
  return { api, unmount: () => app.unmount() }
}

describe("useRailWidth collapse", () => {
  beforeEach(() => localStorage.clear())

  it("starts expanded, persists a toggle, and restores it on the next mount", () => {
    const first = mountRail()
    expect(first.api.collapsed.value).toBe(false)
    expect(first.api.width.value).toBe(DEFAULT_RAIL_WIDTH)
    first.api.toggleCollapsed()
    expect(first.api.collapsed.value).toBe(true)
    expect(localStorage.getItem("kt.rail.collapsed")).toBe("1")
    first.unmount()

    const second = mountRail()
    expect(second.api.collapsed.value).toBe(true)
    expect(second.api.width.value).toBe(DEFAULT_RAIL_WIDTH)
    second.api.toggleCollapsed()
    expect(localStorage.getItem("kt.rail.collapsed")).toBe("0")
    second.unmount()
  })

  it("follows another window's toggle", () => {
    const { api, unmount } = mountRail()
    localStorage.setItem("kt.rail.collapsed", "1")
    window.dispatchEvent(new StorageEvent("storage", { key: "kt.rail.collapsed" }))
    expect(api.collapsed.value).toBe(true)
    unmount()
  })
})
