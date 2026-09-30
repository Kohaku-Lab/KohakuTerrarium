/**
 * Ctrl+1..9 is both "switch to preset N" (global) and "focus chat group N"
 * (ChatPanelContainer). One key press must do one of them, never both.
 */

import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, h } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import ChatPanelContainer from "./ChatPanelContainer.vue"
import { useKeyboardShortcuts } from "@/composables/useKeyboardShortcuts"
import { useChatStore } from "@/stores/chat"
import { useLayoutStore } from "@/stores/layout"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"

vi.mock("@/components/chat/ChatGroupNode.vue", () => ({
  default: { name: "ChatGroupNode", render: () => null },
}))

const PRESETS = ["chat-focus", "workspace", "multi-creature", "canvas", "debug", "settings"]

const Host = defineComponent({
  setup() {
    useKeyboardShortcuts()
    return () => h(ChatPanelContainer, { instance: { id: "graph_1" } })
  },
})

function press(key) {
  const event = new KeyboardEvent("keydown", {
    key,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  })
  window.dispatchEvent(event)
  return event
}

describe("Ctrl+digit between chat groups and layout presets", () => {
  let wrapper
  let layout
  let chat

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
    wrapper = mount(Host)
    chat = useChatStore("graph_1")
  })

  afterEach(() => {
    wrapper.unmount()
    vi.unstubAllGlobals()
  })

  it("with a single chat group the digit switches the preset, including Ctrl+1", () => {
    layout.switchPreset("workspace")

    press("1")
    expect(layout.activePresetId).toBe("chat-focus")
    press("2")
    expect(layout.activePresetId).toBe("workspace")
  })

  it("with two chat groups the digit focuses that group and leaves the preset alone", () => {
    const first = chat.focusedGroupId
    chat.splitGroup(first, "horizontal", "after", null)
    const [, second] = Object.keys(chat.groups)
    chat.setFocusedGroup(first)
    expect(chat.focusedGroupId).toBe(first)

    press("2")

    expect(chat.focusedGroupId).toBe(second)
    expect(layout.activePresetId).toBe("chat-focus")
  })

  it("a digit beyond the group count still switches the preset", () => {
    chat.splitGroup(chat.focusedGroupId, "horizontal", "after", null)

    press("4")

    expect(layout.activePresetId).toBe("canvas")
  })
})
