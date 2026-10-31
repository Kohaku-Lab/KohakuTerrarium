import { describe, expect, it } from "vitest"

import { acceleratorFromEvent, displayAccelerator } from "@/utils/accelerator"

const press = (code, mods = {}) => ({
  code,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  shiftKey: false,
  ...mods,
})

describe("acceleratorFromEvent", () => {
  it("writes the modifiers and key the way the shell parses them", () => {
    expect(acceleratorFromEvent(press("Space", { ctrlKey: true, shiftKey: true }), false)).toBe(
      "CommandOrControl+Shift+Space",
    )
    expect(acceleratorFromEvent(press("KeyK", { altKey: true }), false)).toBe("Alt+K")
    expect(acceleratorFromEvent(press("Digit1", { metaKey: true }), false)).toBe("Super+1")
    expect(acceleratorFromEvent(press("F12", { ctrlKey: true }), false)).toBe(
      "CommandOrControl+F12",
    )
    expect(acceleratorFromEvent(press("ArrowLeft", { altKey: true }), false)).toBe("Alt+Left")
  })

  it("maps Cmd to the primary key and Ctrl to Control on a Mac", () => {
    expect(acceleratorFromEvent(press("KeyJ", { metaKey: true, shiftKey: true }), true)).toBe(
      "CommandOrControl+Shift+J",
    )
    expect(acceleratorFromEvent(press("KeyJ", { ctrlKey: true }), true)).toBe("Control+J")
  })

  it("refuses presses that cannot be a system-wide shortcut", () => {
    expect(acceleratorFromEvent(press("KeyK"), false)).toBe("")
    expect(acceleratorFromEvent(press("ControlLeft", { ctrlKey: true }), false)).toBe("")
    expect(acceleratorFromEvent(press("Escape", { ctrlKey: true }), false)).toBe("")
    expect(acceleratorFromEvent(press("Backquote", { ctrlKey: true }), false)).toBe("")
    expect(acceleratorFromEvent({}, false)).toBe("")
  })
})

describe("displayAccelerator", () => {
  it("reads naturally on each platform", () => {
    expect(displayAccelerator("CommandOrControl+Shift+Space", false)).toBe("Ctrl+Shift+Space")
    expect(displayAccelerator("CommandOrControl+Shift+Space", true)).toBe("⌘+Shift+Space")
    expect(displayAccelerator("Super+1", false)).toBe("Win+1")
    expect(displayAccelerator("", false)).toBe("")
  })
})
