import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

const { firePaletteOpen, popOutFocusedSession } = vi.hoisted(() => ({
  firePaletteOpen: vi.fn(),
  popOutFocusedSession: vi.fn(async () => true),
}))
vi.mock("@/utils/layoutEvents", () => ({ firePaletteOpen }))
vi.mock("@/composables/useDesktopPopout", () => ({ popOutFocusedSession }))

import { OPEN_WORKSPACE_EVENT } from "@/composables/useDesktopOpen"
import { MENU_EVENT, runMenuAction, useDesktopMenu } from "@/composables/useDesktopMenu"
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  firePaletteOpen.mockClear()
  popOutFocusedSession.mockClear()
})

describe("runMenuAction", () => {
  it("opens settings, the palette, a new session and the session window", async () => {
    expect(await runMenuAction("settings")).toBe(true)
    expect(useTabsStore().byId.settings).toMatchObject({ kind: "settings" })

    expect(await runMenuAction("palette")).toBe(true)
    expect(firePaletteOpen).toHaveBeenCalledTimes(1)

    const seen = []
    const handler = (event) => seen.push(event.detail)
    window.addEventListener(OPEN_WORKSPACE_EVENT, handler)
    expect(await runMenuAction("new-session")).toBe(true)
    window.removeEventListener(OPEN_WORKSPACE_EVENT, handler)
    expect(seen).toEqual([{ path: "" }])

    expect(await runMenuAction("popout")).toBe(true)
    expect(popOutFocusedSession).toHaveBeenCalledTimes(1)
  })

  it("ignores actions it does not know", async () => {
    expect(await runMenuAction("format-disk")).toBe(false)
    expect(await runMenuAction(undefined)).toBe(false)
  })
})

describe("useDesktopMenu", () => {
  it("runs the action the shell names until unmounted", async () => {
    const wrapper = mount(
      defineComponent({
        setup() {
          useDesktopMenu()
          return () => null
        },
      }),
    )
    window.dispatchEvent(new CustomEvent(MENU_EVENT, { detail: { action: "palette" } }))
    await Promise.resolve()
    expect(firePaletteOpen).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    window.dispatchEvent(new CustomEvent(MENU_EVENT, { detail: { action: "palette" } }))
    await Promise.resolve()
    expect(firePaletteOpen).toHaveBeenCalledTimes(1)
  })
})
