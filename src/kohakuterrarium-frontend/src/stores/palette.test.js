import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { usePaletteStore } from "./palette.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("palette store — registry", () => {
  it("registers and runs a command", () => {
    const store = usePaletteStore()
    const handler = vi.fn()
    store.register({
      id: "test:foo",
      label: "Test Foo",
      handler,
    })
    expect(store.commands).toHaveLength(1)
    store.run("test:foo")
    expect(handler).toHaveBeenCalledOnce()
  })

  it("dedupes commands by id", () => {
    const store = usePaletteStore()
    store.register({ id: "x", label: "v1", handler: () => {} })
    store.register({ id: "x", label: "v2", handler: () => {} })
    expect(store.commands).toHaveLength(1)
    expect(store.commands[0].label).toBe("v2")
  })

  it("filters by prefix + fuzzy matches by label", () => {
    const store = usePaletteStore()
    store.register({
      id: "a",
      label: "Open Activity Panel",
      handler: () => {},
    })
    store.register({ id: "b", label: "Open State Panel", handler: () => {} })
    store.register({ id: "c", label: "Mode: Workspace", handler: () => {} })
    store.query = "act"
    expect(store.results.map((r) => r.id)).toContain("a")
    expect(store.results.map((r) => r.id)).not.toContain("c")
  })

  it("honors the > @ # / prefix switch", () => {
    const store = usePaletteStore()
    store.register({
      id: "a",
      label: "Command A",
      prefix: ">",
      handler: () => {},
    })
    store.register({
      id: "s",
      label: "session",
      prefix: "#",
      handler: () => {},
    })
    store.query = "#ses"
    const ids = store.results.map((r) => r.id)
    expect(ids).toContain("s")
    expect(ids).not.toContain("a")
  })

  it("closePalette resets query", () => {
    const store = usePaletteStore()
    store.openPalette("hello")
    expect(store.open).toBe(true)
    expect(store.query).toBe("hello")
    store.closePalette()
    expect(store.open).toBe(false)
    expect(store.query).toBe("")
  })
})
