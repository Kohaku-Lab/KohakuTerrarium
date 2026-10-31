import { mount } from "@vue/test-utils"
import { defineComponent } from "vue"
import { afterEach, describe, expect, it, vi } from "vitest"

const { firePaletteOpen } = vi.hoisted(() => ({ firePaletteOpen: vi.fn() }))
vi.mock("@/utils/layoutEvents", () => ({ firePaletteOpen }))

import {
  SUMMON_EVENT,
  focusComposerOrPalette,
  useDesktopSummon,
} from "@/composables/useDesktopSummon"

function composer({ visible = true } = {}) {
  const wrapper = document.createElement("div")
  wrapper.className = "kt-chat-composer"
  const box = document.createElement("textarea")
  wrapper.appendChild(box)
  document.body.appendChild(wrapper)
  Object.defineProperty(box, "offsetParent", {
    configurable: true,
    value: visible ? wrapper : null,
  })
  return box
}

afterEach(() => {
  document.body.innerHTML = ""
  firePaletteOpen.mockReset()
})

describe("focusComposerOrPalette", () => {
  it("focuses the visible message box and skips hidden ones", () => {
    const hidden = composer({ visible: false })
    const shown = composer()
    expect(focusComposerOrPalette()).toBe("composer")
    expect(document.activeElement).toBe(shown)
    expect(document.activeElement).not.toBe(hidden)
    expect(firePaletteOpen).not.toHaveBeenCalled()
  })

  it("opens the command palette when no chat is showing", () => {
    composer({ visible: false })
    expect(focusComposerOrPalette()).toBe("palette")
    expect(firePaletteOpen).toHaveBeenCalledTimes(1)
  })
})

describe("useDesktopSummon", () => {
  it("reacts to the shell's summon event until unmounted", () => {
    const box = composer()
    const wrapper = mount(
      defineComponent({
        setup() {
          useDesktopSummon()
          return () => null
        },
      }),
    )
    window.dispatchEvent(new Event(SUMMON_EVENT))
    expect(document.activeElement).toBe(box)

    box.blur()
    wrapper.unmount()
    window.dispatchEvent(new Event(SUMMON_EVENT))
    expect(document.activeElement).not.toBe(box)
  })
})
