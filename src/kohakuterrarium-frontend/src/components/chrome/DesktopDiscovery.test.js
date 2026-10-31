import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key) => key }),
}))

import DesktopFirstRun from "@/components/chrome/DesktopFirstRun.vue"
import DesktopStatusChip from "@/components/chrome/DesktopStatusChip.vue"
import { resetShellSettings } from "@/composables/useShellSettings"
import { useTabsStore } from "@/stores/tabs"
import { openDesktopSettings } from "@/utils/desktopSettings"

const ElSwitch = {
  props: ["modelValue"],
  emits: ["change"],
  template:
    '<button class="switch" :data-on="modelValue" @click="$emit(\'change\', !modelValue)" />',
}
const ElButton = {
  emits: ["click"],
  template: "<button @click=\"$emit('click')\"><slot /></button>",
}
const stubs = { ElSwitch, "el-switch": ElSwitch, ElButton, "el-button": ElButton }

function shell(initial = {}) {
  let stored = {
    closeToTray: false,
    restorePopouts: true,
    summonShortcut: null,
    firstRunDismissed: false,
    ...initial,
  }
  const invoke = vi.fn(async (name, payload) => {
    if (name === "get_shell_settings") return stored
    if (name === "patch_shell_settings") {
      stored = { ...stored, ...payload.args[0] }
      return { ok: true, settings: stored }
    }
    if (name === "get_autostart") return false
    if (name === "set_autostart") return true
    return null
  })
  window.__TAURI_INTERNALS__ = { invoke }
  return invoke
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  resetShellSettings()
})

afterEach(() => {
  delete window.__TAURI_INTERNALS__
  resetShellSettings()
})

describe("DesktopStatusChip", () => {
  it("does not exist in a browser", async () => {
    const wrapper = mount(DesktopStatusChip)
    await flushPromises()
    expect(wrapper.find('[data-test="desktop-chip"]').exists()).toBe(false)
  })

  it("lists the desktop settings in its tooltip and marks a setup that was never finished", async () => {
    shell({ closeToTray: true, summonShortcut: "Ctrl+Shift+K" })
    const wrapper = mount(DesktopStatusChip)
    await flushPromises()

    const chip = wrapper.get('[data-test="desktop-chip"]')
    const lines = chip.attributes("title").split("\n")
    expect(lines[0]).toBe("desktop.chip.tooltip")
    expect(lines[1]).toBe("settings.desktop.trayTitle: desktop.chip.on")
    expect(lines[2]).toBe("settings.desktop.restoreTitle: desktop.chip.on")
    expect(lines[3]).toContain("Ctrl")
    expect(wrapper.find('[data-test="desktop-chip-dot"]').exists()).toBe(true)
  })

  it("loses the dot once the first-run card was dismissed, and says what is off or unset", async () => {
    shell({ firstRunDismissed: true, restorePopouts: false })
    const wrapper = mount(DesktopStatusChip)
    await flushPromises()
    expect(wrapper.find('[data-test="desktop-chip-dot"]').exists()).toBe(false)
    const lines = wrapper.get('[data-test="desktop-chip"]').attributes("title").split("\n")
    expect(lines[1]).toContain("desktop.chip.off")
    expect(lines[2]).toContain("desktop.chip.off")
    expect(lines[3]).toContain("desktop.chip.notSet")
  })

  it("opens the desktop section of the settings", async () => {
    shell()
    const wrapper = mount(DesktopStatusChip)
    await flushPromises()
    const target = document.createElement("div")
    target.id = "desktop-app-settings"
    target.scrollIntoView = vi.fn()
    document.body.append(target)

    await wrapper.get('[data-test="desktop-chip"]').trigger("click")
    const tabs = useTabsStore()
    expect(tabs.byId.settings).toMatchObject({ kind: "settings" })
    expect(tabs.focusedGroup.activeId).toBe("settings")
    await vi.waitFor(() => expect(target.scrollIntoView).toHaveBeenCalled())
    target.remove()
  })
})

describe("openDesktopSettings", () => {
  it("gives up quietly when the section never renders", async () => {
    vi.useFakeTimers()
    const frames = []
    vi.stubGlobal("requestAnimationFrame", (callback) => frames.push(callback))
    openDesktopSettings()
    let count = 0
    while (frames.length && count < 200) {
      frames.shift()()
      count += 1
    }
    expect(count).toBeGreaterThan(1)
    expect(count).toBeLessThan(200)
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })
})

describe("DesktopFirstRun", () => {
  it("is absent in a browser and after it was dismissed", async () => {
    const browser = mount(DesktopFirstRun, { global: { stubs } })
    await flushPromises()
    expect(browser.find('[data-test="desktop-first-run"]').exists()).toBe(false)

    shell({ firstRunDismissed: true })
    const dismissed = mount(DesktopFirstRun, { global: { stubs } })
    await flushPromises()
    expect(dismissed.find('[data-test="desktop-first-run"]').exists()).toBe(false)
  })

  it("changes settings in place and remembers that it was dismissed", async () => {
    const invoke = shell()
    const wrapper = mount(DesktopFirstRun, { global: { stubs } })
    await flushPromises()
    expect(wrapper.find('[data-test="desktop-first-run"]').exists()).toBe(true)

    await wrapper.get('[data-first-run="tray"] .switch').trigger("click")
    expect(invoke).toHaveBeenCalledWith("patch_shell_settings", { args: [{ closeToTray: true }] })
    await flushPromises()
    expect(wrapper.get('[data-first-run="tray"] .switch').attributes("data-on")).toBe("true")

    await wrapper.get('[data-first-run="autostart"] .switch').trigger("click")
    expect(invoke).toHaveBeenCalledWith("set_autostart", { args: [true] })

    await wrapper.get('[data-first-run="done"]').trigger("click")
    await flushPromises()
    expect(invoke).toHaveBeenCalledWith("patch_shell_settings", {
      args: [{ firstRunDismissed: true }],
    })
    expect(wrapper.find('[data-test="desktop-first-run"]').exists()).toBe(false)
  })

  it("opens the settings and counts as dismissed", async () => {
    const invoke = shell()
    const wrapper = mount(DesktopFirstRun, { global: { stubs } })
    await flushPromises()
    await wrapper.get('[data-first-run="open-settings"]').trigger("click")
    await flushPromises()
    expect(invoke).toHaveBeenCalledWith("patch_shell_settings", {
      args: [{ firstRunDismissed: true }],
    })
    expect(useTabsStore().byId.settings).toBeTruthy()
  })
})
