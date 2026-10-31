import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent } from "vue"

import { firstOpaqueColor, parseOpaqueColor, useWindowChrome } from "@/composables/useWindowChrome"

afterEach(() => {
  delete window.__TAURI_INTERNALS__
  document.documentElement.className = ""
  vi.restoreAllMocks()
})

describe("parseOpaqueColor", () => {
  it("reads computed rgb and rgba colours and skips see-through ones", () => {
    expect(parseOpaqueColor("rgb(227, 221, 213)")).toEqual([227, 221, 213])
    expect(parseOpaqueColor("rgba(42, 39, 36, 1)")).toEqual([42, 39, 36])
    expect(parseOpaqueColor("rgba(42, 39, 36, 0.6)")).toEqual([42, 39, 36])
    expect(parseOpaqueColor("rgba(0, 0, 0, 0)")).toBeNull()
    expect(parseOpaqueColor("rgba(10, 20, 30, 0.2)")).toBeNull()
    expect(parseOpaqueColor("transparent")).toBeNull()
    expect(parseOpaqueColor("rgb(300, 0, 0)")).toBeNull()
    expect(parseOpaqueColor("")).toBeNull()
  })
})

describe("firstOpaqueColor", () => {
  it("walks up to the first parent that paints a background", () => {
    const outer = document.createElement("div")
    const middle = document.createElement("div")
    const inner = document.createElement("div")
    outer.append(middle)
    middle.append(inner)
    const colors = new Map([
      [inner, "rgba(0, 0, 0, 0)"],
      [middle, "rgb(1, 2, 3)"],
      [outer, "rgb(9, 9, 9)"],
    ])
    const style = (node) => ({ backgroundColor: colors.get(node) })
    expect(firstOpaqueColor(inner, style)).toEqual([1, 2, 3])
    expect(firstOpaqueColor(null, style)).toBeNull()
    colors.set(middle, "rgba(0, 0, 0, 0)")
    colors.set(outer, "transparent")
    expect(firstOpaqueColor(inner, style)).toBeNull()
  })
})

describe("useWindowChrome", () => {
  beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", (callback) => setTimeout(callback, 0))
    vi.stubGlobal("cancelAnimationFrame", (id) => clearTimeout(id))
    document.body.style.backgroundColor = "rgb(227, 221, 213)"
  })
  afterEach(() => vi.unstubAllGlobals())

  const probe = defineComponent({
    setup() {
      useWindowChrome()
      return () => null
    },
  })

  it("does nothing outside the Tauri shell", async () => {
    const wrapper = mount(probe)
    document.documentElement.className = "dark"
    await Promise.resolve()
    wrapper.unmount()
  })

  it("paints the frame at start and again when the theme class changes", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mount(probe)
    await vi.waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("set_window_chrome", {
        args: [{ background: [227, 221, 213] }],
      }),
    )

    document.body.style.backgroundColor = "rgb(42, 39, 36)"
    document.documentElement.className = "dark"
    await vi.waitFor(() =>
      expect(invoke).toHaveBeenLastCalledWith("set_window_chrome", {
        args: [{ background: [42, 39, 36] }],
      }),
    )

    wrapper.unmount()
    const calls = invoke.mock.calls.length
    document.documentElement.className = ""
    await Promise.resolve()
    expect(invoke.mock.calls.length).toBe(calls)
  })
})
