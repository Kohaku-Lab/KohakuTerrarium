/**
 * Ctrl+1..9 is both "switch to preset N" (global) and "focus tab group N"
 * (TabGroupContainer). One key press must do one of them, never both.
 */

import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, h } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import TabGroupContainer from "./TabGroupContainer.vue"
import { useKeyboardShortcuts } from "@/composables/useKeyboardShortcuts"
import { useLayoutStore } from "@/stores/layout"
import { useTabsStore } from "@/stores/tabs"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"

vi.mock("@/components/common/SplitTreeNode.vue", () => ({
  default: { name: "SplitTreeNode", render: () => null },
}))
vi.mock("@/components/shell/TabGroup.vue", () => ({
  default: { name: "TabGroup", render: () => null },
}))

const PRESETS = ["chat-focus", "workspace", "multi-creature", "canvas", "debug", "settings"]

const Host = defineComponent({
  setup() {
    useKeyboardShortcuts()
    return () => h(TabGroupContainer)
  },
})

function press(key) {
  window.dispatchEvent(
    new KeyboardEvent("keydown", { key, ctrlKey: true, bubbles: true, cancelable: true }),
  )
}

describe("Ctrl+digit between tab groups and layout presets", () => {
  let wrapper
  let layout
  let tabs

  beforeEach(() => {
    _resetUIPrefsForTests()
    setActivePinia(createPinia())
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
    })
    layout = useLayoutStore()
    for (const id of PRESETS) {
      layout.registerBuiltinPreset({
        id,
        label: id,
        zones: { main: { visible: true, size: 100 } },
        slots: [{ zoneId: "main", panelId: "chat" }],
      })
    }
    layout.switchPreset("chat-focus")
    tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dash", name: "Dashboard" })
    wrapper = mount(Host)
  })

  afterEach(() => {
    wrapper.unmount()
    vi.unstubAllGlobals()
  })

  it("with one tab group the digit switches the preset, including Ctrl+1", () => {
    layout.switchPreset("workspace")

    press("1")
    expect(layout.activePresetId).toBe("chat-focus")
    press("2")
    expect(layout.activePresetId).toBe("workspace")
  })

  it("with two tab groups the digit focuses that group and leaves the preset alone", () => {
    tabs.openTab({ kind: "dashboard", id: "dash2", name: "Two" })
    tabs.splitTabGroup(tabs.focusedGroupId, "horizontal", "after", "dash2", tabs.focusedGroupId)
    const [first, second] = tabs.groupOrder()
    tabs.setFocusedGroup(first)

    press("2")

    expect(tabs.focusedGroupId).toBe(second)
    expect(layout.activePresetId).toBe("chat-focus")
  })
})
