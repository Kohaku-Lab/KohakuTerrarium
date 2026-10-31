import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, nextTick, watchEffect } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  POPOUT_COMMAND_ID,
  focusedSession,
  popOutFocusedSession,
  useDesktopPopout,
} from "@/composables/useDesktopPopout"
import { useInstancesStore } from "@/stores/instances"
import { usePaletteStore } from "@/stores/palette"
import { useTabsStore } from "@/stores/tabs"
import {
  POPOUT_LABEL_PREFIX,
  popOutSession,
  popoutLabel,
  popoutScopeFromSearch,
} from "@/utils/popout"

function tauri() {
  const invoke = vi.fn(async () => true)
  window.__TAURI_INTERNALS__ = { invoke }
  return invoke
}

function showSession(target = "graph-1") {
  const tabs = useTabsStore()
  tabs.openTab({ kind: "attach", id: `attach:${target}`, target })
  tabs.activateTab(`attach:${target}`)
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

afterEach(() => {
  delete window.__TAURI_INTERNALS__
})

describe("popout helpers", () => {
  it("name the window after the session and read the session from the address", () => {
    expect(popoutLabel("graph-1")).toMatch(/^popout-graph-1-[a-z0-9]{8}$/)
    expect(popoutLabel("graph-1")).toBe(popoutLabel("graph-1"))
    expect(popoutLabel("graph-2")).not.toBe(popoutLabel("graph-1"))
    expect(popoutLabel("x".repeat(200)).length - POPOUT_LABEL_PREFIX.length).toBeLessThanOrEqual(48)
    expect(popoutScopeFromSearch("?kt_popout=graph%201")).toBe("graph 1")
    expect(popoutScopeFromSearch("?kt_overlay=x")).toBeNull()
  })

  it("open through the shell only where it can", async () => {
    expect(await popOutSession("graph-1")).toBe(false)
    const invoke = tauri()
    expect(await popOutSession("")).toBe(false)
    expect(await popOutSession("graph-1", "My session")).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("open_popout_window", {
      args: [{ label: popoutLabel("graph-1"), scope: "graph-1", title: "My session" }],
    })
    await popOutSession("graph-1")
    expect(invoke.mock.calls.at(-1)[1].args[0].title).toBe("KohakuTerrarium")
  })
})

describe("the focused session", () => {
  it("is the attach tab in the focused group, titled from its instance", () => {
    expect(focusedSession()).toBeNull()
    showSession("graph-1")
    expect(focusedSession()).toEqual({ scope: "graph-1", title: "" })

    useInstancesStore().list = [{ id: "graph-1", graph_id: "graph-1", session_name: "Refactor" }]
    expect(focusedSession()).toEqual({ scope: "graph-1", title: "Refactor" })
  })

  it("is nothing while a non-session tab is focused", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "settings", id: "settings" })
    tabs.activateTab("settings")
    expect(focusedSession()).toBeNull()
  })

  it("pops out only when there is a session", async () => {
    const invoke = tauri()
    expect(await popOutFocusedSession()).toBe(false)
    expect(invoke).not.toHaveBeenCalled()
    showSession("graph-9")
    expect(await popOutFocusedSession()).toBe(true)
    expect(invoke.mock.calls[0][1].args[0].scope).toBe("graph-9")
  })
})

describe("useDesktopPopout", () => {
  const Probe = defineComponent({
    setup() {
      useDesktopPopout()
      return () => null
    },
  })

  it("adds a palette command in a shell that can open session windows", async () => {
    const invoke = tauri()
    const wrapper = mount(Probe)
    await nextTick()
    const entry = usePaletteStore().commands.find((c) => c.id === POPOUT_COMMAND_ID)
    expect(entry).toBeTruthy()

    showSession("graph-3")
    entry.handler()
    await nextTick()
    expect(invoke).toHaveBeenCalledWith("open_popout_window", expect.anything())
    wrapper.unmount()
  })

  it("registers once, so effects that re-register other commands cannot retrigger it", async () => {
    tauri()
    const palette = usePaletteStore()
    const register = vi.spyOn(palette, "register")
    const Other = defineComponent({
      setup() {
        watchEffect(() => {
          palette.register({ id: "other:a", label: "A", handler: () => {} })
        })
        useDesktopPopout()
        return () => null
      },
    })
    const wrapper = mount(Other)
    for (let i = 0; i < 3; i += 1) await nextTick()
    const settled = register.mock.calls.length
    for (let i = 0; i < 20; i += 1) await nextTick()

    expect(register.mock.calls.filter(([cmd]) => cmd.id === POPOUT_COMMAND_ID)).toHaveLength(1)
    expect(register.mock.calls).toHaveLength(settled)
    wrapper.unmount()
  })

  it("offers nothing in a browser", async () => {
    const wrapper = mount(Probe)
    await nextTick()
    expect(usePaletteStore().commands.some((c) => c.id === POPOUT_COMMAND_ID)).toBe(false)
    wrapper.unmount()
  })
})
