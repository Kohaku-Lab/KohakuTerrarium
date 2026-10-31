import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  commandShortcuts,
  installCommandShortcuts,
  normalizeShortcut,
  shortcutFromEvent,
} from "@/ext/commands"
import { createPackageStorage, hostInfo, notifyDesktop, openExternal } from "@/ext/hostServices"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { extState, runtime } from "@/ext/state"
import { usePaletteStore } from "@/stores/palette"

vi.mock("element-plus", () => ({ ElMessage: vi.fn(), ElMessageBox: { confirm: vi.fn() } }))

function declare(commands) {
  const rec = {
    package: "pets",
    enabled: true,
    status: "idle",
    contributes: commands.map((c) => ({
      kind: "command",
      owner: "package:pets",
      label: c.id,
      ...c,
    })),
  }
  extState.packages = [rec]
  registerPackageContributions(rec)
  return rec
}

describe("shortcut normalization", () => {
  it("folds Meta into Ctrl, orders modifiers and upper-cases the key", () => {
    expect(normalizeShortcut("Meta+Shift+p")).toBe("Ctrl+Shift+P")
    expect(normalizeShortcut("Alt+Ctrl+f5")).toBe("Ctrl+Alt+f5")
    expect(normalizeShortcut("")).toBe("")
  })

  it("reads layout-independent keys from an event", () => {
    const event = (init) => ({
      ctrlKey: false,
      metaKey: false,
      altKey: false,
      shiftKey: false,
      ...init,
    })
    expect(shortcutFromEvent(event({ ctrlKey: true, code: "KeyP", key: "p" }))).toBe("Ctrl+P")
    expect(
      shortcutFromEvent(event({ metaKey: true, altKey: true, code: "Digit3", key: "£" })),
    ).toBe("Ctrl+Alt+3")
    expect(shortcutFromEvent(event({ ctrlKey: true, code: "ControlLeft", key: "Control" }))).toBe(
      "",
    )
    expect(shortcutFromEvent(event({ code: "F5", key: "F5" }))).toBe("F5")
  })
})

describe("extension commands", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    commandShortcuts.clear()
    runtime.ensureLoaded = vi.fn(async () => {})
    installCommandShortcuts()
  })

  it("adds palette entries before the package loads and runs the handler after loading it", async () => {
    const rec = declare([
      { id: "toggle", label: "Toggle pet", shortcut: "Ctrl+Alt+P", keywords: "cat" },
      { id: "plain" },
    ])
    const palette = usePaletteStore()
    const entry = palette.commands.find((c) => c.id === "ext:pets/toggle")
    expect(entry).toMatchObject({ label: "Toggle pet", shortcut: "Ctrl+Alt+P", keywords: "cat" })
    expect(runtime.ensureLoaded).not.toHaveBeenCalled()

    const handler = vi.fn()
    provideContributionComponent("pets", "command", "toggle", handler)
    await entry.handler()
    expect(runtime.ensureLoaded).toHaveBeenCalledWith("pets")
    expect(handler).toHaveBeenCalledTimes(1)
    expect(rec.contributes).toHaveLength(2)
  })

  it("runs a command from its shortcut and ignores repeats and handled events", async () => {
    declare([{ id: "toggle", shortcut: "Ctrl+Alt+P" }])
    const handler = vi.fn()
    provideContributionComponent("pets", "command", "toggle", handler)

    const press = (init = {}) => {
      const event = new KeyboardEvent("keydown", {
        ctrlKey: true,
        altKey: true,
        code: "KeyP",
        key: "p",
        cancelable: true,
        ...init,
      })
      window.dispatchEvent(event)
      return event
    }
    const first = press()
    await vi.waitFor(() => expect(handler).toHaveBeenCalledTimes(1))
    expect(first.defaultPrevented).toBe(true)
    press({ repeat: true })
    press({ code: "KeyQ", key: "q" })
    press({ altKey: false })
    await Promise.resolve()
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it("refuses a shortcut the app or another command already owns", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    declare([
      { id: "palette", shortcut: "Ctrl+K" },
      { id: "first", shortcut: "Ctrl+Alt+P" },
      { id: "second", shortcut: "Ctrl+Alt+P" },
    ])
    expect(commandShortcuts.get("Ctrl+Alt+P").key).toBe("ext:pets/first")
    expect(commandShortcuts.has("Ctrl+K")).toBe(false)
    expect(warn).toHaveBeenCalledTimes(2)
    const palette = usePaletteStore()
    expect(palette.commands.find((c) => c.id === "ext:pets/second").shortcut).toBe("")
    warn.mockRestore()
  })

  it("removes entries and shortcuts with the contributions and validates handlers", () => {
    declare([{ id: "toggle", shortcut: "Ctrl+Alt+P" }])
    expect(() => provideContributionComponent("pets", "command", "toggle", {})).toThrow(
      /handler function/,
    )
    unregisterAllContributions()
    expect(usePaletteStore().commands.find((c) => c.id === "ext:pets/toggle")).toBeUndefined()
    expect(commandShortcuts.size).toBe(0)
  })

  it("adds an undeclared command from code", () => {
    declare([])
    provideContributionComponent("pets", "command", "dyn", () => {}, {
      label: "Dynamic",
      shortcut: "Ctrl+Alt+D",
    })
    expect(usePaletteStore().commands.find((c) => c.id === "ext:pets/dyn").label).toBe("Dynamic")
    expect(commandShortcuts.has("Ctrl+Alt+D")).toBe(true)
  })
})

describe("host services", () => {
  beforeEach(() => {
    delete window.__TAURI_INTERNALS__
    delete window.pywebview
    localStorage.clear()
  })

  it("notifies natively when the shell can and with a toast otherwise", async () => {
    expect(await notifyDesktop("Done", "body")).toBe("toast")
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    expect(await notifyDesktop("Done", "body")).toBe("native")
    expect(invoke).toHaveBeenCalledWith("show_notification", { args: ["Done", "body"] })
    expect(hostInfo()).toEqual({ kind: "tauri" })
    delete window.__TAURI_INTERNALS__
    expect(hostInfo()).toEqual({ kind: "browser" })
  })

  it("opens only web and mail links, through the shell when there is one", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue({})
    expect(await openExternal("javascript:alert(1)")).toBe(false)
    expect(await openExternal("file:///etc/passwd")).toBe(false)
    expect(await openExternal("not a url")).toBe(false)
    expect(await openExternal("https://example.test/a")).toBe(true)
    expect(open).toHaveBeenCalledWith("https://example.test/a", "_blank", "noopener,noreferrer")

    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    open.mockClear()
    expect(await openExternal("mailto:a@example.test")).toBe(true)
    expect(invoke).toHaveBeenCalledWith("open_external", { args: ["mailto:a@example.test"] })
    expect(open).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it("keeps package storage separate per package and survives blocked storage", () => {
    const a = createPackageStorage("a")
    const b = createPackageStorage("b")
    a.set("k", { n: 1 })
    expect(a.get("k")).toEqual({ n: 1 })
    expect(b.get("k", "none")).toBe("none")
    a.remove("k")
    expect(a.get("k")).toBeNull()

    const blocked = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    const c = createPackageStorage("c")
    c.set("k", 5)
    expect(c.get("k")).toBe(5)
    c.remove("k")
    blocked.mockRestore()
    set.mockRestore()
  })
})
